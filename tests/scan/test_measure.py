import os
from pathlib import Path

import pytest

from storage_cleanup.scan.measure import measure, measure_tree
from tests.helpers import make_file


def blocks(path: Path) -> int:
    return os.lstat(path).st_blocks * 512


def tree_blocks(root: Path) -> int:
    total = blocks(root)
    for dirpath, dirnames, filenames in os.walk(root):
        for name in dirnames + filenames:
            total += blocks(Path(dirpath) / name)
    return total


def test_size_is_allocated_blocks_including_dirs(tmp_path: Path) -> None:
    root = tmp_path / "root"
    make_file(root / "a", 10_000)
    make_file(root / "sub" / "b", 1)
    m, skipped = measure(root, collector="t")
    assert m.size_bytes == tree_blocks(root)
    assert m.file_count == 2
    assert skipped == ()


def test_hard_links_count_once(tmp_path: Path) -> None:
    root = tmp_path / "root"
    big = make_file(root / "big", 1_000_000)
    before, _ = measure(root, collector="t")
    os.link(big, root / "link")
    after, _ = measure(root, collector="t")
    assert after.file_count == 2
    assert after.size_bytes - before.size_bytes < 100_000


def test_symlinks_are_not_followed_and_loops_terminate(tmp_path: Path) -> None:
    outside = make_file(tmp_path / "outside" / "big", 2_000_000)
    root = tmp_path / "root"
    root.mkdir()
    (root / "link").symlink_to(outside)
    (root / "loop").symlink_to(root)
    (root / "dirlink").symlink_to(outside.parent)
    m, _ = measure(root, collector="t")
    assert m.size_bytes < 1_000_000


def test_sparse_file_reports_allocated_not_apparent(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    with open(root / "sparse", "wb") as f:
        f.truncate(1024**3)
        f.write(b"x" * 4096)
    m, _ = measure(root, collector="t")
    assert m.size_bytes < 1024**2


def test_unreadable_dir_is_skipped_with_reason(tmp_path: Path) -> None:
    root = tmp_path / "root"
    locked = root / "locked"
    make_file(locked / "secret", 10)
    make_file(root / "open", 10)
    locked.chmod(0)
    try:
        m, skipped = measure(root, collector="t")
    finally:
        locked.chmod(0o755)
    assert [(s.path, s.reason, s.collector) for s in skipped] == [
        (str(locked), "no access", "t")
    ]
    assert m.file_count == 1


def test_vanishing_file_is_ignored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "root"
    gone = make_file(root / "gone", 10)
    make_file(root / "kept", 10)
    real_lstat = os.lstat

    def flaky_lstat(path: os.PathLike[str] | str) -> os.stat_result:
        if Path(path) == gone:
            raise FileNotFoundError(path)
        return real_lstat(path)

    monkeypatch.setattr(os, "lstat", flaky_lstat)
    m, skipped = measure(root, collector="t")
    assert m.file_count == 1
    assert skipped == ()


def test_skip_rule_prevents_descent(tmp_path: Path) -> None:
    root = tmp_path / "root"
    make_file(root / "Containers" / "com.x" / "data", 500_000)

    def rule(p: Path) -> str | None:
        return "privacy" if p.name == "Containers" else None

    m, skipped = measure(root, collector="t", skip=rule)
    assert m.file_count == 0
    assert [(s.path, s.reason) for s in skipped] == [
        (str(root / "Containers"), "privacy")
    ]


def test_item_depth_and_threshold(tmp_path: Path) -> None:
    root = tmp_path / "root"
    make_file(root / "a" / "f", 2_000_000)
    make_file(root / "b", 10_000)
    make_file(root / "a" / "c" / "f", 2_000_000)
    result, _ = measure_tree(
        root, collector="t", max_item_depth=1, min_item_bytes=1_000_000
    )
    assert set(result) == {root, root / "a"}
    assert result[root / "a"].file_count == 2


def test_odd_names_measured(tmp_path: Path) -> None:
    root = tmp_path / "root"
    for name in ("new\nline", "emoji 🚀", "naïve"):
        make_file(root / name, 10)
    m, _ = measure(root, collector="t")
    assert m.file_count == 3


def test_newest_and_oldest_mtime(tmp_path: Path) -> None:
    root = tmp_path / "root"
    make_file(root / "old", 10, mtime=1000)
    make_file(root / "new", 10, mtime=2000)
    m, _ = measure(root, collector="t")
    assert m.oldest_mtime == 1000
    assert m.newest_mtime == 2000
