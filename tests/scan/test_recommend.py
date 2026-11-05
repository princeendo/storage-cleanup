from dataclasses import replace

import pytest

from storage_cleanup.scan.ids import item_id
from storage_cleanup.scan.model import AgeSignal, Item, SizeSource
from storage_cleanup.scan.recommend import (
    format_age,
    rule_recommendations,
    score,
    size_factor,
)
from tests.helpers import path_item
from tests.web.test_views import scan_of

GIB = 1024**3
MIB = 1024**2
DAY = 86_400.0
NOW = 1_800_000_000.0


def aged(item: Item, days: float, signal: AgeSignal = AgeSignal.LAST_MODIFIED) -> Item:
    return replace(item, age_signal=signal, age_timestamp=NOW - days * DAY)


def virtual(
    kind: str, key: str, size: int, cats: tuple[str, ...], **details: object
) -> Item:
    return Item(
        id=item_id(kind, key),
        kind=kind,
        name=key,
        path=None,
        size_bytes=size,
        size_source=SizeSource.TOOL_REPORTED,
        categories=cats,
        details=dict(details),  # type: ignore[arg-type]
    )


def test_size_factor_endpoints() -> None:
    assert size_factor(4096) == 0
    assert size_factor(1) == 0
    assert size_factor(0) == 0
    assert size_factor(32 * GIB) == 1
    assert size_factor(64 * GIB) == 1
    assert 0.4 < size_factor(32 * MIB) < 0.7


def test_score_is_monotonic() -> None:
    sizes = [0, 4096, MIB, 100 * MIB, GIB, 10 * GIB, 100 * GIB]
    confidences = [0, 0.25, 0.5, 0.75, 1]
    for c in confidences:
        values = [score(c, s) for s in sizes]
        assert values == sorted(values)
    for s in sizes:
        values = [score(c, s) for c in confidences]
        assert values == sorted(values)
    assert score(1, 64 * GIB) == 100


def test_tiny_file_scores_low_even_when_certain() -> None:
    assert score(1.0, 4 * 1024) == 0


def test_folder_rollup_scores_higher_than_its_files() -> None:
    assert score(0.9, 40 * GIB) > score(0.9, 4 * MIB)


@pytest.mark.parametrize(
    ("days", "text"),
    [
        (0.5, "today"),
        (1.5, "1 day"),
        (10, "10 days"),
        (100, "3 months"),
        (400, "1.1 years"),
    ],
)
def test_format_age_matches_page_wording(days: float, text: str) -> None:
    assert format_age(days) == text


def rule_items() -> list[Item]:
    repo = aged(
        path_item("/h/workspace/old", 3 * GIB, ("projects",), kind="repo"),
        400,
        AgeSignal.LAST_COMMIT,
    )
    total = virtual("docker-total", "docker", 6 * GIB, ("docker",))
    return [
        path_item(
            "/h/Library/Developer/Xcode/DerivedData",
            5 * GIB,
            ("dev-caches",),
            kind="cache",
            details={
                "why_safe": "Build products only.",
                "regenerates": "Rebuilt by Xcode.",
            },
        ),
        path_item(
            "/h/Downloads/Slack.dmg",
            200 * MIB,
            ("downloads",),
            kind="file",
            details={"file_type": "installer", "installer_for": "Slack"},
        ),
        total,
        replace(
            virtual("docker-build-cache", "bc", 2 * GIB, ("docker",)),
            parent_id=total.id,
        ),
        replace(
            aged(
                virtual("docker-image", "old:1", GIB, ("docker",), containers=0),
                183,
                AgeSignal.CREATED,
            ),
            parent_id=total.id,
        ),
        repo,
        path_item(
            "/h/workspace/old/node_modules",
            2 * GIB,
            ("projects",),
            kind="regenerable",
            details={"pattern": "node_modules"},
        ),
        path_item(
            "/h/.claude/projects/-gone",
            300 * MIB,
            ("ai-tooling",),
            kind="ai-sessions",
            details={
                "tool_name": "Claude Code",
                "cwd": "/gone/repo",
                "cwd_exists": False,
            },
        ),
        path_item(
            "/h/Library/Caches/com.old.app",
            50 * MIB,
            ("leftover-app-data",),
            kind="app-data",
            details={"guessed_owner": "com.old.app"},
        ),
    ]


def test_rules_cover_each_kind() -> None:
    items = rule_items()
    recs = {
        r.item_id: r for r in rule_recommendations(scan_of(items, used=100 * GIB), NOW)
    }
    expected = {
        items[0].id: ("delete", 0.9, "Build products only. Rebuilt by Xcode."),
        items[1].id: (
            "delete",
            0.85,
            "Installer for Slack, which is already installed.",
        ),
        items[3].id: (
            "delete",
            0.9,
            "Docker build cache; rebuilt when images are built again.",
        ),
        items[4].id: ("delete", 0.6, "Docker image no container has used in 6 months."),
        items[6].id: (
            "delete",
            0.7,
            "node_modules in a project last committed 1.1 years ago; "
            "rebuilt by its tools.",
        ),
        items[7].id: (
            "delete",
            0.6,
            "Claude Code sessions for a folder that no longer exists (/gone/repo).",
        ),
        items[8].id: (
            "delete",
            0.5,
            "Data left by com.old.app, which is no longer installed.",
        ),
    }
    assert {k: (r.action, r.confidence, r.reason) for k, r in recs.items()} == expected
    assert all(r.source == "rule" for r in recs.values())


def test_rules_skip_protected_items() -> None:
    cache = path_item(
        "/h/workspace/repo/.git/lfs/cache",
        GIB,
        ("dev-caches",),
        kind="cache",
        details={"why_safe": "x", "regenerates": "y"},
    )
    assert rule_recommendations(scan_of([cache], used=10 * GIB), NOW) == []


def test_recent_project_and_docker_image_are_not_recommended() -> None:
    repo = aged(
        path_item("/h/workspace/new", 3 * GIB, ("projects",), kind="repo"),
        30,
        AgeSignal.LAST_COMMIT,
    )
    regen = path_item(
        "/h/workspace/new/node_modules",
        GIB,
        ("projects",),
        kind="regenerable",
        details={"pattern": "node_modules"},
    )
    image = aged(
        virtual("docker-image", "new:1", GIB, ("docker",), containers=0),
        30,
        AgeSignal.CREATED,
    )
    used = aged(
        virtual("docker-image", "used:1", GIB, ("docker",), containers=2),
        900,
        AgeSignal.CREATED,
    )
    assert (
        rule_recommendations(scan_of([repo, regen, image, used], used=10 * GIB), NOW)
        == []
    )
