import json
from typing import Any

from storage_cleanup.findings import AgentCategory, Findings, Headline
from storage_cleanup.scan.recommend import Recommendation
from storage_cleanup.web.recommendations import (
    merge_recommendations,
    reclaimable_bytes,
    recommendation_rows,
    target_candidates,
)
from storage_cleanup.web.views import build_view
from tests.actions.test_plan import IMAGE, TOTAL, VOLUME
from tests.helpers import path_item
from tests.web.test_views import scan_of

GIB = 1024**3
MIB = 1024**2
NOW = 1_800_000_000.0

DOWNLOADS = path_item("/h/Downloads", 20 * GIB)
DMGS = [
    path_item(f"/h/Downloads/app{i}.dmg", (i + 1) * GIB, ("downloads",), kind="file")
    for i in range(4)
]
MOVIES = path_item("/h/Movies", 10 * GIB)
CLIP = path_item("/h/Movies/clip.mov", 4 * GIB, kind="file")
MAIL = path_item("/h/Library/Mail", GIB)


def rec(
    item_id: str,
    confidence: float = 0.8,
    reason: str = "r",
    source: str = "rule",
    action: str = "delete",
) -> Recommendation:
    return Recommendation(item_id, action, confidence, reason, source)


def scan() -> Any:
    return scan_of([DOWNLOADS, *DMGS, MOVIES, CLIP, MAIL], used=100 * GIB)


def test_agent_overrides_rule_for_same_item() -> None:
    merged = merge_recommendations(
        scan(), [rec(MOVIES.id, 0.5)], [rec(MOVIES.id, 0.9, "agent says", "agent")]
    )
    assert [(r.item_id, r.confidence, r.source) for r in merged] == [
        (MOVIES.id, 0.9, "agent")
    ]


def test_merge_drops_protected_and_unknown() -> None:
    merged = merge_recommendations(
        scan(), [rec(MAIL.id)], [rec("file:nope", source="agent"), rec(CLIP.id)]
    )
    assert [r.item_id for r in merged] == [CLIP.id]


def test_reclaimable_counts_nested_once() -> None:
    recs = [rec(MOVIES.id), rec(CLIP.id)]
    assert reclaimable_bytes(scan(), recs) == 10 * GIB


def rows_of(recs: list[Recommendation]) -> Any:
    return json.loads(json.dumps(recommendation_rows(scan(), recs, NOW)))


def test_rows_sorted_by_score() -> None:
    rows = rows_of([rec(DMGS[0].id, 0.9), rec(MOVIES.id, 0.9)])
    assert [r["item_id"] for r in rows] == [MOVIES.id, DMGS[0].id]
    assert rows[0]["score"] > rows[1]["score"]
    assert rows[0]["category"] == "home"
    assert rows[1]["category"] == "downloads"


def test_rollup_groups_siblings_with_same_action() -> None:
    recs = [
        rec(d.id, 0.9 - i * 0.1, "Installer already used.") for i, d in enumerate(DMGS)
    ]
    [row] = recommendation_rows(scan(), recs, NOW)
    assert row["id"] == f"rollup:{DOWNLOADS.id}:delete"
    assert row["name"] == "4 items in Downloads"
    assert row["size"] == 10 * GIB
    assert row["confidence"] == 0.6
    assert row["reason"] == "Installer already used."
    children = row["children"]
    assert isinstance(children, list)
    assert len(children) == 4


def test_rollup_mixed_reasons_say_so() -> None:
    recs = [rec(d.id, reason=f"r{i}") for i, d in enumerate(DMGS[:3])]
    [row] = recommendation_rows(scan(), recs, NOW)
    assert row["reason"] == "3 items; open to see each reason"


def test_rollup_needs_three_and_same_action() -> None:
    two = recommendation_rows(scan(), [rec(DMGS[0].id), rec(DMGS[1].id)], NOW)
    assert len(two) == 2
    mixed = recommendation_rows(
        scan(),
        [rec(DMGS[0].id), rec(DMGS[1].id), rec(DMGS[2].id, action="archive")],
        NOW,
    )
    assert len(mixed) == 3


def test_nested_recommendation_shown_under_ancestor() -> None:
    [row] = rows_of([rec(MOVIES.id), rec(CLIP.id)])
    assert row["item_id"] == MOVIES.id
    assert [c["item_id"] for c in row["children"]] == [CLIP.id]


def findings() -> Findings:
    return Findings(
        schema_version=1,
        scan="s.json",
        created_at="2026-11-05T23:00:00Z",
        agent="Test agent",
        headlines=(
            Headline("Old installers", "4 files, 10 GiB.", tuple(d.id for d in DMGS)),
        ),
        categories=(AgentCategory("media", "Media", "Videos.", (MOVIES.id,)),),
        recommendations=(rec(MOVIES.id, 0.7, "Old videos.", "agent", "archive"),),
    )


def test_build_view_includes_findings_and_reclaimable() -> None:
    view = json.loads(json.dumps(build_view(scan(), NOW, findings())))
    assert view["findings"]["agent"] == "Test agent"
    [headline] = view["findings"]["headlines"]
    assert headline["title"] == "Old installers"
    assert [i["name"] for i in headline["items"]] == [d.name for d in DMGS]
    assert headline["items"][0]["size"] == GIB
    assert view["findings"]["categories"][0]["item_count"] == 1
    [row] = view["recommendations"]
    assert (row["source"], row["action"]) == ("agent", "archive")
    assert view["overview"]["reclaimable_bytes"] == 10 * GIB
    assert view["overview"]["recommendation_count"] == 1
    assert view["findings_note"] is None


def test_build_view_without_findings_uses_rules() -> None:
    installer = path_item(
        "/h/Downloads/Slack.dmg",
        GIB,
        ("downloads",),
        kind="file",
        details={"installer_for": "Slack"},
    )
    view = json.loads(
        json.dumps(build_view(scan_of([installer], used=10 * GIB), NOW, None, "note"))
    )
    assert view["findings"] is None
    assert view["findings_note"] == "note"
    assert [r["source"] for r in view["recommendations"]] == ["rule"]


def test_rollup_does_not_mix_rules() -> None:
    recs = [
        Recommendation(DMGS[0].id, "delete", 0.9, "a", "rule", "dev-cache"),
        Recommendation(DMGS[1].id, "delete", 0.9, "b", "rule", "dev-cache"),
        Recommendation(DMGS[2].id, "delete", 0.9, "c", "rule", "dev-cache"),
        Recommendation(DMGS[3].id, "delete", 0.5, "d", "rule", "leftover-app-data"),
    ]
    rows = rows_of(recs)
    rollups = [r for r in rows if r["id"].startswith("rollup:")]
    assert [len(r["children"]) for r in rollups] == [3]
    assert rollups[0]["confidence"] == 0.9
    assert len(rows) == 2


def test_rows_say_whether_the_page_can_remove_them() -> None:
    from tests.actions.test_plan import TOTAL, VOLUME

    s = scan_of([DOWNLOADS, *DMGS, TOTAL, VOLUME], used=100 * GIB)
    rows = json.loads(
        json.dumps(recommendation_rows(s, [rec(DMGS[0].id), rec(VOLUME.id)], NOW))
    )
    removable = {r["item_id"]: r["removable"] for r in rows}
    assert removable == {DMGS[0].id: True, VOLUME.id: False}
    rollup = json.loads(
        json.dumps(recommendation_rows(scan(), [rec(d.id) for d in DMGS], NOW))
    )
    assert rollup[0]["removable"] is True
    assert all(c["removable"] for c in rollup[0]["children"])


def target_scan() -> Any:
    return scan_of(
        [DOWNLOADS, *DMGS, MOVIES, CLIP, MAIL, TOTAL, IMAGE, VOLUME], used=100 * GIB
    )


def candidates(recs: list[Recommendation]) -> Any:
    return json.loads(json.dumps(target_candidates(target_scan(), recs)))


def test_target_candidates_ordered_by_score() -> None:
    rows = candidates([rec(DMGS[0].id, 0.9), rec(MOVIES.id, 0.9)])
    assert [r["item_id"] for r in rows] == [MOVIES.id, DMGS[0].id]
    assert rows[0]["category"] == "home"
    assert set(rows[0]) == {
        "item_id",
        "name",
        "path",
        "size",
        "score",
        "category",
        "frees_now",
    }


def test_target_candidates_exclude_nested_child() -> None:
    rows = candidates([rec(MOVIES.id), rec(CLIP.id)])
    assert [r["item_id"] for r in rows] == [MOVIES.id]


def test_target_candidates_exclude_volumes_protected_and_unknown() -> None:
    rows = candidates(
        [rec(VOLUME.id), rec(TOTAL.id), rec(MAIL.id), rec("file:nope"), rec(CLIP.id)]
    )
    assert [r["item_id"] for r in rows] == [CLIP.id]


def test_target_candidates_only_delete_actions() -> None:
    rows = candidates([rec(MOVIES.id, action="archive"), rec(CLIP.id, action="delete")])
    assert [r["item_id"] for r in rows] == [CLIP.id]


def test_target_candidates_frees_now() -> None:
    rows = {r["item_id"]: r for r in candidates([rec(IMAGE.id), rec(CLIP.id)])}
    assert rows[IMAGE.id]["frees_now"] is True
    assert rows[CLIP.id]["frees_now"] is False


def test_build_view_includes_target_candidates() -> None:
    view = build_view(scan(), NOW)
    assert isinstance(view["target_candidates"], list)
