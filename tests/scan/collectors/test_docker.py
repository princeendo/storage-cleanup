"""Docker collector tests.

Fixture shapes follow the Docker CLI 29.x documentation for ``--format json``
and ``{{json .}}`` output. Docker was not running when these were written, so
they were not captured from a live daemon.
"""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from storage_cleanup.scan.collectors.docker import collect_docker, parse_docker_size
from storage_cleanup.scan.ids import item_id
from storage_cleanup.scan.model import AgeSignal, Item, SizeSource
from tests.helpers import FakeRunner, make_ctx, ok

INFO = ("docker", "info", "--format", "{{json .ServerVersion}}")
DF = ("docker", "system", "df", "--format", "{{json .}}")
IMAGES = ("docker", "image", "ls", "--all", "--format", "{{json .}}")
PS = ("docker", "ps", "--all", "--format", "{{json .}}")
VOLUMES = ("docker", "system", "df", "-v", "--format", "{{json .Volumes}}")


def lines(*records: dict[str, str]) -> str:
    return "\n".join(json.dumps(r) for r in records) + "\n"


DF_OUT = lines(
    {
        "Type": "Images",
        "TotalCount": "2",
        "Active": "1",
        "Size": "3.4GB",
        "Reclaimable": "1.2GB (35%)",
    },
    {
        "Type": "Containers",
        "TotalCount": "1",
        "Active": "0",
        "Size": "12kB",
        "Reclaimable": "12kB (100%)",
    },
    {
        "Type": "Local Volumes",
        "TotalCount": "1",
        "Active": "0",
        "Size": "500MB",
        "Reclaimable": "500MB (100%)",
    },
    {
        "Type": "Build Cache",
        "TotalCount": "9",
        "Active": "0",
        "Size": "2GB",
        "Reclaimable": "2GB",
    },
)
IMAGES_OUT = lines(
    {
        "ID": "aaa111",
        "Repository": "redis",
        "Tag": "latest",
        "Size": "138MB",
        "CreatedAt": "2025-01-01 12:00:00 +0000 UTC",
    },
    {
        "ID": "bbb222",
        "Repository": "old/tool",
        "Tag": "1.0",
        "Size": "3.2GB",
        "CreatedAt": "2024-06-01 08:00:00 +0000 UTC",
    },
)
PS_OUT = lines(
    {
        "ID": "c1",
        "Image": "redis",
        "State": "exited",
        "CreatedAt": "2026-03-01 09:00:00 +0000 UTC",
    },
)
VOLUMES_OUT = json.dumps([{"Name": "pgdata", "Size": "500MB", "Links": 0}])


def running_runner() -> FakeRunner:
    return FakeRunner(
        {
            INFO: ok('"29.9.2"\n'),
            DF: ok(DF_OUT),
            IMAGES: ok(IMAGES_OUT),
            PS: ok(PS_OUT),
            VOLUMES: ok(VOLUMES_OUT),
        }
    )


def ts(text: str) -> float:
    return datetime.strptime(text, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC).timestamp()


def of_kind(items: tuple[Item, ...], kind: str) -> list[Item]:
    return [i for i in items if i.kind == kind]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("0B", 0),
        ("512kB", 512_000),
        ("1.2MB", 1_200_000),
        ("3.4GB", 3_400_000_000),
        ("1TB", 1_000_000_000_000),
        ("1.5GB (12%)", 1_500_000_000),
        ("N/A", 0),
    ],
)
def test_parse_docker_size(text: str, expected: int) -> None:
    assert parse_docker_size(text) == expected


def test_docker_not_running_is_skipped(tmp_path: Path) -> None:
    result = collect_docker(make_ctx(tmp_path / "h"))
    assert result.items == ()
    assert [s.reason for s in result.skipped] == ["Docker not running or not installed"]


def test_total_item_sums_all_types(tmp_path: Path) -> None:
    items = collect_docker(make_ctx(tmp_path / "h", running_runner())).items
    [total] = of_kind(items, "docker-total")
    assert total.size_bytes == 3_400_000_000 + 12_000 + 500_000_000 + 2_000_000_000
    assert total.path is None
    assert total.categories == ("docker",)
    assert total.size_source is SizeSource.TOOL_REPORTED


def test_images_with_container_use(tmp_path: Path) -> None:
    items = collect_docker(make_ctx(tmp_path / "h", running_runner())).items
    redis = next(i for i in of_kind(items, "docker-image") if i.name == "redis:latest")
    assert redis.size_bytes == 138_000_000
    assert redis.details["containers"] == 1
    assert redis.details["running"] is False
    assert redis.age_signal is AgeSignal.LAST_CONTAINER_USE
    assert redis.age_timestamp == ts("2026-03-01 09:00:00")


def test_unused_image_age_is_created(tmp_path: Path) -> None:
    items = collect_docker(make_ctx(tmp_path / "h", running_runner())).items
    old = next(i for i in of_kind(items, "docker-image") if i.name == "old/tool:1.0")
    assert old.details["containers"] == 0
    assert old.age_signal is AgeSignal.CREATED
    assert old.age_timestamp == ts("2024-06-01 08:00:00")


def test_volumes_are_report_only_and_all_parented(tmp_path: Path) -> None:
    items = collect_docker(make_ctx(tmp_path / "h", running_runner())).items
    [total] = of_kind(items, "docker-total")
    [volume] = of_kind(items, "docker-volume")
    assert volume.name == "pgdata"
    assert volume.size_bytes == 500_000_000
    assert volume.details["report_only"] is True
    [cache] = of_kind(items, "docker-build-cache")
    assert cache.size_bytes == 2_000_000_000
    children = [i for i in items if i is not total]
    assert all(i.parent_id == total.id for i in children)
    assert all(i.path is None for i in items)
    assert total.id == item_id("docker-total", "docker")
