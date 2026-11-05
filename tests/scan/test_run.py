from pathlib import Path

from storage_cleanup.scan.context import CollectorResult, ScanContext
from storage_cleanup.scan.run import run_scan
from tests.helpers import FakeRunner, make_ctx, ok, path_item

TMUTIL = ("tmutil", "listlocalsnapshots", "/")


def test_collector_exception_becomes_skipped_and_scan_finishes(tmp_path: Path) -> None:
    home = tmp_path / "h"

    def boom(ctx: ScanContext) -> CollectorResult:
        raise RuntimeError("kaput")

    def fine(ctx: ScanContext) -> CollectorResult:
        return CollectorResult(items=(path_item(str(ctx.home / "Movies"), 7),))

    scan = run_scan(make_ctx(home), [("boom", boom), ("ok", fine)])
    assert [i.path for i in scan.items] == [str(home / "Movies")]
    failures = [s for s in scan.skipped if s.collector == "boom"]
    assert failures[0].reason.startswith("collector failed: RuntimeError")
    assert scan.totals_by_category == {"home": 7}
    assert scan.created_at.endswith("Z")


def test_run_scan_marks_protected_and_location(tmp_path: Path) -> None:
    home = tmp_path / "h"

    def items(ctx: ScanContext) -> CollectorResult:
        return CollectorResult(
            items=(
                path_item(str(ctx.home / "Library" / "Mail"), 1),
                path_item(str(ctx.home / "Downloads" / "x"), 1),
            )
        )

    scan = run_scan(make_ctx(home), [("items", items)])
    found = {i.path: i for i in scan.items}
    assert found[str(home / "Library" / "Mail")].protected_reason == "Mail"
    assert found[str(home / "Downloads" / "x")].location == "Downloads"
    assert found[str(home / "Downloads" / "x")].protected_reason is None


def test_snapshots_parsed(tmp_path: Path) -> None:
    runner = FakeRunner(
        {
            TMUTIL: ok(
                "Snapshots for disk /:\ncom.apple.TimeMachine.2026-11-05-120000.local\n"
            )
        }
    )
    scan = run_scan(make_ctx(tmp_path / "h", runner), [])
    assert scan.local_snapshots == ("com.apple.TimeMachine.2026-11-05-120000.local",)


def test_tmutil_failure_tolerated(tmp_path: Path) -> None:
    scan = run_scan(make_ctx(tmp_path / "h"), [])
    assert scan.local_snapshots == ()
    assert any(s.collector == "snapshots" for s in scan.skipped)


def test_unreadable_trash_gives_none(tmp_path: Path) -> None:
    home = tmp_path / "h"
    trash = home / ".Trash"
    trash.mkdir(parents=True)
    trash.chmod(0)
    try:
        scan = run_scan(make_ctx(home), [])
    finally:
        trash.chmod(0o755)
    assert scan.trash_bytes is None
    assert any(s.collector == "trash" for s in scan.skipped)


def test_readable_trash_is_measured(tmp_path: Path) -> None:
    home = tmp_path / "h"
    (home / ".Trash").mkdir(parents=True)
    (home / ".Trash" / "old").write_bytes(b"x" * 50_000)
    scan = run_scan(make_ctx(home), [])
    assert scan.trash_bytes is not None
    assert scan.trash_bytes >= 50_000


def test_disk_usage_present(tmp_path: Path) -> None:
    scan = run_scan(make_ctx(tmp_path / "h"), [])
    assert scan.disk.total_bytes > 0
    assert scan.disk.free_bytes > 0


def test_folders_containing_protected_stores_are_protected(tmp_path: Path) -> None:
    home = tmp_path / "h"
    (home / "Library" / "Mail").mkdir(parents=True)
    (home / "Pictures" / "Photos Library.photoslibrary").mkdir(parents=True)
    (home / "workspace" / "repo" / ".git").mkdir(parents=True)

    def items(ctx: ScanContext) -> CollectorResult:
        return CollectorResult(
            items=(
                path_item(str(ctx.home / "Library"), 1),
                path_item(str(ctx.home / "Pictures"), 1),
                path_item(str(ctx.home / "workspace" / "repo"), 1, kind="repo"),
                path_item(str(ctx.home / "workspace" / "repo" / ".git"), 1),
                path_item(str(ctx.home / "Movies"), 1),
            )
        )

    found = {i.path: i for i in run_scan(make_ctx(home), [("items", items)]).items}
    assert found[str(home / "Library")].protected_reason == "contains Mail"
    assert found[str(home / "Pictures")].protected_reason == "contains Photos library"
    assert found[str(home / "workspace" / "repo")].protected_reason is None
    assert (
        found[str(home / "workspace" / "repo" / ".git")].protected_reason
        == "inside a .git folder"
    )
    assert found[str(home / "Movies")].protected_reason is None
