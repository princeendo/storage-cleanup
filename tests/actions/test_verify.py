import shutil
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

import pytest

from storage_cleanup.actions import verify as verify_mod
from storage_cleanup.actions.model import Method, PlanEntry
from storage_cleanup.actions.verify import verify_entry
from storage_cleanup.scan.collectors.home import path_item as measured_item
from storage_cleanup.scan.context import CollectorResult, ScanContext
from storage_cleanup.scan.measure import measure
from storage_cleanup.scan.model import Scan
from storage_cleanup.scan.run import run_scan
from tests.helpers import make_ctx, make_file


def scan_paths(home: Path, paths: Sequence[Path]) -> Scan:
    def collect(ctx: ScanContext) -> CollectorResult:
        items = []
        for p in paths:
            m, _ = measure(p, collector="t")
            items.append(measured_item(p, m, ("home",)))
        return CollectorResult(items=tuple(items))

    return run_scan(make_ctx(home), [("t", collect)])


def entry_for(scan: Scan, path: Path) -> PlanEntry:
    item = next(i for i in scan.items if i.path == str(path))
    return PlanEntry(
        item.id, item.name, item.path, item.size_bytes, Method.TRASH, "", ()
    )


def test_existing_unchanged_folder_is_ok(tmp_path: Path) -> None:
    home = tmp_path / "h"
    folder = home / "Movies"
    make_file(folder / "a.mov", 50_000)
    scan = scan_paths(home, [folder])
    assert verify_entry(entry_for(scan, folder), scan, brew_prefix=None) is None


def test_missing_path_is_skipped(tmp_path: Path) -> None:
    home = tmp_path / "h"
    target = make_file(home / "Downloads" / "a.dmg", 1000)
    scan = scan_paths(home, [target])
    target.unlink()
    assert (
        verify_entry(entry_for(scan, target), scan, brew_prefix=None)
        == "No longer exists."
    )


def test_outside_allowed_roots_is_skipped(tmp_path: Path) -> None:
    home = tmp_path / "h"
    outside = make_file(tmp_path / "elsewhere" / "x", 10)
    home.mkdir(parents=True, exist_ok=True)
    scan = scan_paths(home, [outside, home])
    reason = "Outside the folders this tool may change."
    assert verify_entry(entry_for(scan, outside), scan, brew_prefix=None) == reason
    assert verify_entry(entry_for(scan, home), scan, brew_prefix=None) == reason


def test_brew_prefix_is_an_allowed_root(tmp_path: Path) -> None:
    home = tmp_path / "h"
    prefix = tmp_path / "brew"
    cellar = make_file(prefix / "Cellar" / "jq" / "bin" / "jq", 10).parent.parent
    home.mkdir(parents=True)
    scan = scan_paths(home, [cellar])
    assert verify_entry(entry_for(scan, cellar), scan, brew_prefix=prefix) is None


def test_protected_path_is_skipped(tmp_path: Path) -> None:
    home = tmp_path / "h"
    mail = make_file(home / "Library" / "Mail" / "V10" / "x", 10).parent.parent
    scan = scan_paths(home, [mail])
    entry = entry_for(scan, mail)
    assert verify_entry(entry, scan, brew_prefix=None) == "Protected: Mail"


def test_photos_library_appeared_inside_is_skipped(tmp_path: Path) -> None:
    home = tmp_path / "h"
    folder = home / "Stuff"
    make_file(folder / "a.txt", 10)
    scan = scan_paths(home, [folder])
    (folder / "deep" / "Old.photoslibrary").mkdir(parents=True)
    reason = verify_entry(entry_for(scan, folder), scan, brew_prefix=None)
    assert reason == "Protected: contains Photos library"


def test_git_folder_inside_repo_is_fine(tmp_path: Path) -> None:
    home = tmp_path / "h"
    repo = home / "workspace" / "app"
    make_file(repo / ".git" / "HEAD", 10)
    make_file(repo / "README.md", 10)
    scan = scan_paths(home, [repo])
    assert verify_entry(entry_for(scan, repo), scan, brew_prefix=None) is None


def test_folder_replaced_by_symlink_is_skipped(tmp_path: Path) -> None:
    home = tmp_path / "h"
    folder = home / "Movies"
    make_file(folder / "a.mov", 10)
    scan = scan_paths(home, [folder])
    shutil.rmtree(folder)
    folder.symlink_to(tmp_path)
    reason = verify_entry(entry_for(scan, folder), scan, brew_prefix=None)
    assert reason == "Changed type since the scan."


def test_large_size_drift_is_skipped_small_is_fine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(verify_mod, "SIZE_DRIFT_MIN_BYTES", 100_000)
    home = tmp_path / "h"
    folder = home / "Movies"
    make_file(folder / "a.mov", 1_000_000)
    scan = scan_paths(home, [folder])
    make_file(folder / "tiny", 10)
    assert verify_entry(entry_for(scan, folder), scan, brew_prefix=None) is None
    make_file(folder / "big", 2_000_000)
    reason = verify_entry(entry_for(scan, folder), scan, brew_prefix=None)
    assert reason is not None
    assert reason.startswith("Changed since the scan (was ")
    assert reason.endswith("); rescan first.")


def test_non_path_entries_are_not_checked_here(tmp_path: Path) -> None:
    home = tmp_path / "h"
    folder = home / "Movies"
    make_file(folder / "a", 10)
    scan = scan_paths(home, [folder])
    entry = replace(entry_for(scan, folder), path=None, method=Method.DOCKER_IMAGE)
    assert verify_entry(entry, scan, brew_prefix=None) is None
