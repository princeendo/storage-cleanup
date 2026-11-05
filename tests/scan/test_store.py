from pathlib import Path

from storage_cleanup.scan.collectors.targeted import collect_path
from storage_cleanup.scan.context import CollectorResult, ScanContext
from storage_cleanup.scan.model import Scan, scan_from_json
from storage_cleanup.scan.run import run_scan
from storage_cleanup.scan.store import (
    latest_scan,
    merge_items,
    previous_scan,
    scans_dir,
    write_scan,
)
from tests.helpers import make_ctx, make_file, path_item

MIB = 1024**2


def sample_scan(home: Path, now: float = 1_793_900_000.0) -> Scan:
    def items(ctx: ScanContext) -> CollectorResult:
        return CollectorResult(
            items=(path_item(str(ctx.home / "Downloads" / "big"), 10 * MIB),)
        )

    return run_scan(make_ctx(home, now=now), [("items", items)])


def test_scans_dir(tmp_path: Path) -> None:
    assert scans_dir(tmp_path) == tmp_path / "reports" / "scans"


def test_write_is_atomic_and_named_by_time(tmp_path: Path) -> None:
    scan = sample_scan(tmp_path / "h")
    out = tmp_path / "out"
    path = write_scan(scan, out)
    assert path.name == "20261105T173320Z.json"
    assert [p.name for p in out.iterdir()] == [path.name]
    assert scan_from_json(path.read_text()) == scan


def test_latest_and_previous(tmp_path: Path) -> None:
    out = tmp_path / "out"
    assert latest_scan(out) is None
    first = write_scan(sample_scan(tmp_path / "h", now=1_700_000_000.0), out)
    second = write_scan(sample_scan(tmp_path / "h", now=1_800_000_000.0), out)
    (out / "notes.txt").write_text("ignored")
    assert latest_scan(out) == second
    assert previous_scan(out, second) == first
    assert previous_scan(out, first) is None


def test_merge_replaces_and_relinks(tmp_path: Path) -> None:
    home = tmp_path / "h"
    big = home / "Downloads" / "big"
    make_file(big / "movie.mov", 2 * MIB)
    base = sample_scan(home)
    addition = collect_path(make_ctx(home), big)
    merged = merge_items(base, addition)
    found = {i.path: i for i in merged.items}
    big_item = found[str(big)]
    assert found[str(big / "movie.mov")].parent_id == big_item.id
    assert found[str(big / "movie.mov")].location == "Downloads"
    assert len([i for i in merged.items if i.path == str(big)]) == 1
    assert merged.totals_by_category["home"] == big_item.size_bytes


def test_collect_path_items(tmp_path: Path) -> None:
    home = tmp_path / "h"
    target = home / "Movies"
    make_file(target / "a" / "big.mov", 2 * MIB)
    make_file(target / "small.txt", 10)
    result = collect_path(make_ctx(home), target)
    paths = {i.path for i in result.items}
    assert paths == {str(target), str(target / "a"), str(target / "a" / "big.mov")}
    assert all(i.categories == ("home",) for i in result.items)
