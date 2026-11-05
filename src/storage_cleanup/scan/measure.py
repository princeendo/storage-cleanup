"""Measure allocated disk usage in one pass, without following symlinks."""

import os
import stat
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from storage_cleanup.scan.model import Skipped

type SkipRule = Callable[[Path], str | None]


@dataclass(frozen=True)
class Measurement:
    size_bytes: int
    file_count: int
    newest_mtime: float | None
    oldest_mtime: float | None


@dataclass
class _Acc:
    parent: Path | None
    depth: int
    size: int = 0
    count: int = 0
    newest: float | None = None
    oldest: float | None = None

    def add_mtime(self, newest: float | None, oldest: float | None) -> None:
        if newest is not None and (self.newest is None or newest > self.newest):
            self.newest = newest
        if oldest is not None and (self.oldest is None or oldest < self.oldest):
            self.oldest = oldest

    def freeze(self) -> Measurement:
        return Measurement(self.size, self.count, self.newest, self.oldest)


_EMPTY = Measurement(0, 0, None, None)


def measure_tree(
    root: Path,
    *,
    collector: str,
    max_item_depth: int = 0,
    min_item_bytes: int = 0,
    skip: SkipRule | None = None,
) -> tuple[dict[Path, Measurement], tuple[Skipped, ...]]:
    """Walk ``root`` once and return sizes for it and its larger descendants.

    The result always contains ``root``. Descendants at depth ``<=
    max_item_depth`` (children are depth 1) whose size is at least
    ``min_item_bytes`` are included too.
    """
    skipped: list[Skipped] = []
    try:
        root_st = os.lstat(root)
    except FileNotFoundError:
        return {root: _EMPTY}, ()
    except PermissionError:
        return {root: _EMPTY}, (Skipped(str(root), "no access", collector),)

    if not stat.S_ISDIR(root_st.st_mode):
        mtime = root_st.st_mtime
        return {root: Measurement(root_st.st_blocks * 512, 1, mtime, mtime)}, ()

    device = root_st.st_dev
    seen_links: set[tuple[int, int]] = set()
    dirs: dict[Path, _Acc] = {root: _Acc(None, 0, size=root_st.st_blocks * 512)}
    order: list[Path] = []
    files: list[tuple[Path, int, Measurement]] = []  # file items for the result
    stack: list[Path] = [root]

    while stack:
        current = stack.pop()
        order.append(current)
        acc = dirs[current]
        try:
            entries = list(os.scandir(current))
        except PermissionError:
            skipped.append(Skipped(str(current), "no access", collector))
            continue
        except (FileNotFoundError, NotADirectoryError):
            continue
        for entry in entries:
            path = Path(entry.path)
            try:
                st = os.lstat(path)
            except (FileNotFoundError, NotADirectoryError):
                continue
            except PermissionError:
                skipped.append(Skipped(str(path), "no access", collector))
                continue
            if stat.S_ISDIR(st.st_mode):
                if st.st_dev != device:
                    continue
                reason = skip(path) if skip is not None else None
                if reason is not None:
                    skipped.append(Skipped(str(path), reason, collector))
                    continue
                dirs[path] = _Acc(current, acc.depth + 1, size=st.st_blocks * 512)
                stack.append(path)
                continue
            size = st.st_blocks * 512
            if st.st_nlink > 1:
                key = (st.st_dev, st.st_ino)
                if key in seen_links:
                    size = 0
                seen_links.add(key)
            acc.size += size
            acc.count += 1
            acc.add_mtime(st.st_mtime, st.st_mtime)
            depth = acc.depth + 1
            if depth <= max_item_depth and size >= min_item_bytes:
                files.append(
                    (path, depth, Measurement(size, 1, st.st_mtime, st.st_mtime))
                )

    # Pre-order reversed visits every child before its parent.
    for path in reversed(order):
        acc = dirs[path]
        if acc.parent is not None:
            parent = dirs[acc.parent]
            parent.size += acc.size
            parent.count += acc.count
            parent.add_mtime(acc.newest, acc.oldest)

    result: dict[Path, Measurement] = {root: dirs[root].freeze()}
    for path, acc in dirs.items():
        if 0 < acc.depth <= max_item_depth and acc.size >= min_item_bytes:
            result[path] = acc.freeze()
    for path, _depth, m in files:
        result[path] = m
    return result, tuple(skipped)


def measure(
    path: Path, *, collector: str, skip: SkipRule | None = None
) -> tuple[Measurement, tuple[Skipped, ...]]:
    """Measure one path's total allocated size."""
    result, skipped = measure_tree(path, collector=collector, skip=skip)
    return result[path], skipped
