import os
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
    assert reason == "Is a link; there is nothing here to free."


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


# --- Final review fixes -----------------------------------------------------


def test_parent_swapped_for_a_link_is_skipped(tmp_path: Path) -> None:
    home = tmp_path / "h"
    target = make_file(home / "proj" / "sub" / "data.bin", 5000).parent
    scan = scan_paths(home, [target])
    outside = make_file(tmp_path / "outside" / "proj" / "sub" / "PRECIOUS.bin", 5000)
    shutil.rmtree(home / "proj")
    (home / "proj").symlink_to(tmp_path / "outside" / "proj")
    reason = verify_entry(entry_for(scan, target), scan, brew_prefix=None)
    assert reason == "A folder in its path is now a link."
    assert outside.exists()


def test_dot_dot_in_path_is_skipped(tmp_path: Path) -> None:
    home = tmp_path / "h"
    victim = make_file(tmp_path / "victim" / "x", 10).parent
    make_file(home / "a", 1)
    scan = scan_paths(home, [victim])
    sneaky = replace(entry_for(scan, victim), path=f"{home}/../victim")
    assert verify_entry(sneaky, scan, brew_prefix=None) == (
        "A folder in its path is now a link."
    )


@pytest.mark.parametrize(
    ("inner", "reason"),
    [
        ("a/b/c/d/Old.photoslibrary", "Protected: contains Photos library"),
        ("old-mac/Backups.backupdb", "Protected: contains Time Machine"),
        ("x/y/z/w/disk.sparsebundle", "Protected: contains Time Machine"),
    ],
)
def test_deep_protected_stores_are_found(
    tmp_path: Path, inner: str, reason: str
) -> None:
    home = tmp_path / "h"
    folder = home / "Archive"
    make_file(folder / "keep.txt", 10)
    scan = scan_paths(home, [folder])
    (folder / inner).mkdir(parents=True)
    assert verify_entry(entry_for(scan, folder), scan, brew_prefix=None) == reason


class _Stat:
    def __init__(self, real: object, **overrides: int) -> None:
        self._real = real
        self._overrides = overrides

    def __getattr__(self, name: str) -> object:
        if name in self._overrides:
            return self._overrides[name]
        return getattr(self._real, name)


def test_folder_holding_another_disk_is_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "h"
    folder = home / "mnt"
    mount = folder / "share"
    make_file(mount / "remote.bin", 10)
    scan = scan_paths(home, [folder])
    real_lstat = os.lstat

    def lstat(path: os.PathLike[str] | str) -> object:
        st = real_lstat(path)
        return _Stat(st, st_dev=st.st_dev + 1) if Path(path) == mount else st

    monkeypatch.setattr(os, "lstat", lstat)
    assert verify_entry(entry_for(scan, folder), scan, brew_prefix=None) == (
        "Contains another disk (a mounted share or disk image)."
    )


def test_online_only_files_are_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "h"
    folder = home / "Documents" / "synced"
    placeholder = make_file(folder / "cloud.pages", 10)
    scan = scan_paths(home, [folder])
    real_lstat = os.lstat

    def lstat(path: os.PathLike[str] | str) -> object:
        st = real_lstat(path)
        if Path(path) == placeholder:
            return _Stat(st, st_flags=st.st_flags | 0x40000000)
        return st

    monkeypatch.setattr(os, "lstat", lstat)
    assert verify_entry(entry_for(scan, folder), scan, brew_prefix=None) == (
        "Holds online-only (iCloud or cloud drive) files; manage it in Finder."
    )


def test_file_replaced_by_link_is_skipped(tmp_path: Path) -> None:
    home = tmp_path / "h"
    big = make_file(home / "Downloads" / "big.dmg", 5000)
    scan = scan_paths(home, [big])
    elsewhere = make_file(tmp_path / "elsewhere" / "same.dmg", 5000)
    big.unlink()
    big.symlink_to(elsewhere)
    assert verify_entry(entry_for(scan, big), scan, brew_prefix=None) == (
        "Is a link; there is nothing here to free."
    )
