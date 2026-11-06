"""Check an entry against the disk immediately before acting on it.

Everything here is about refusing to act when the disk no longer matches what
the user reviewed: links in the path, protected stores anywhere inside, other
disks mounted inside, online-only cloud files, or a big size change.
"""

import os
import stat
from dataclasses import dataclass
from pathlib import Path, PurePath
from typing import Final

from storage_cleanup.actions.model import Method, PlanEntry
from storage_cleanup.scan.model import ItemKind, Scan
from storage_cleanup.scan.protected import (
    GIT_REASON,
    containing_reason,
    home_store_paths,
    protected_reason,
)

SIZE_DRIFT_FRACTION: Final = 0.10
SIZE_DRIFT_MIN_BYTES: Final = 100 * 1024**2
SF_DATALESS: Final = 0x40000000
LINK_IN_PATH: Final = "A folder in its path is now a link."
IS_LINK: Final = "Is a link; there is nothing here to free."
OTHER_DISK: Final = "Contains another disk (a mounted share or disk image)."
ONLINE_ONLY: Final = (
    "Holds online-only (iCloud or cloud drive) files; manage it in Finder."
)
_STORE_SUFFIXES: Final = {
    ".photoslibrary": "Photos library",
    ".sparsebundle": "Time Machine",
    ".backupbundle": "Time Machine",
}
_STORE_NAMES: Final = {"backups.backupdb": "Time Machine"}
_DIRECTORY_KINDS: Final = frozenset(
    {
        ItemKind.FOLDER,
        ItemKind.APP,
        ItemKind.APP_DATA,
        ItemKind.CACHE,
        ItemKind.REPO,
        ItemKind.REGENERABLE,
        ItemKind.AI_TOOL_PATH,
        ItemKind.AI_SESSIONS,
        ItemKind.BREW_FORMULA,
        ItemKind.BREW_CASK,
    }
)


def allowed_roots(scan: Scan, brew_prefix: Path | None) -> tuple[Path, ...]:
    roots = [Path(scan.home), Path("/Applications")]
    if brew_prefix is not None:
        roots.append(brew_prefix)
    return tuple(roots)


def link_in_path(path: str) -> bool:
    """True if the path has '.'/'..' parts or any folder in it resolves elsewhere."""
    raw = PurePath(path)
    if not raw.is_absolute() or raw != PurePath(os.path.abspath(raw)):
        return True
    real_parent = Path(os.path.realpath(raw.parent))
    return real_parent / raw.name != Path(raw)


def recheck_path(path: str) -> str | None:
    """The cheap checks to repeat right before acting (closes the verify-act gap)."""
    if link_in_path(path):
        return LINK_IN_PATH
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return "No longer exists."
    if stat.S_ISLNK(st.st_mode):
        return IS_LINK
    return None


def _store_name(name: str) -> str | None:
    lowered = name.lower()
    for suffix, reason in _STORE_SUFFIXES.items():
        if lowered.endswith(suffix):
            return reason
    return _STORE_NAMES.get(lowered)


@dataclass
class _Walk:
    size: int = 0
    reason: str | None = None


def _walk(root: Path, root_st: os.stat_result) -> _Walk:
    """One full pass: allocated size, plus the first reason to refuse, if any."""
    result = _Walk(size=root_st.st_blocks * 512)
    if root_st.st_flags & SF_DATALESS:
        result.reason = ONLINE_ONLY
        return result
    if not stat.S_ISDIR(root_st.st_mode):
        return result
    device = root_st.st_dev
    seen: set[tuple[int, int]] = set()
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            names = [entry.name for entry in os.scandir(current)]
        except OSError:
            continue
        for name in names:
            child = current / name
            try:
                st = os.lstat(child)
            except OSError:
                continue
            if st.st_dev != device:
                result.reason = OTHER_DISK
                return result
            if st.st_flags & SF_DATALESS:
                result.reason = ONLINE_ONLY
                return result
            if stat.S_ISDIR(st.st_mode):
                store = _store_name(name)
                if store is not None:
                    result.reason = f"Protected: contains {store}"
                    return result
                result.size += st.st_blocks * 512
                stack.append(child)
                continue
            if st.st_nlink > 1:
                key = (st.st_dev, st.st_ino)
                if key in seen:
                    continue
                seen.add(key)
            result.size += st.st_blocks * 512
    return result


def _size(n: int) -> str:
    value = float(n)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if value < 1024:
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TiB"


def verify_entry(
    entry: PlanEntry, scan: Scan, *, brew_prefix: Path | None
) -> str | None:
    """None if it is still safe to act on this entry; otherwise why it is skipped."""
    if entry.path is None or entry.method not in (Method.TRASH, Method.DELETE):
        return None  # Docker and Homebrew: their own commands decide
    if link_in_path(entry.path):
        return LINK_IN_PATH
    path = Path(entry.path)
    home = Path(scan.home)
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return "No longer exists."
    except OSError as exc:
        return f"Cannot check it: {exc.strerror or exc}."
    if stat.S_ISLNK(st.st_mode):
        return IS_LINK

    roots = allowed_roots(scan, brew_prefix)
    if not any(path != root and path.is_relative_to(root) for root in roots):
        return "Outside the folders this tool may change."

    reason = protected_reason(path, home)
    if reason is not None:
        return f"Protected: {reason}"
    stores = [(p, r) for p, r in home_store_paths(home) if os.path.lexists(p)]
    for other in scan.items:
        why = other.protected_reason
        if (
            other.path is not None
            and why is not None
            and why != GIT_REASON
            and not why.startswith("contains ")
        ):
            stores.append((PurePath(other.path), why))
    reason = containing_reason(path, stores)
    if reason is not None:
        return f"Protected: {reason}"

    item = next((i for i in scan.items if i.id == entry.item_id), None)
    is_dir = stat.S_ISDIR(st.st_mode)
    if item is not None and (
        (item.kind in _DIRECTORY_KINDS and not is_dir)
        or (item.kind == ItemKind.FILE and is_dir)
    ):
        return "Changed type since the scan."

    walk = _walk(path, st)
    if walk.reason is not None:
        return walk.reason
    drift = abs(walk.size - entry.size_bytes)
    if drift > SIZE_DRIFT_MIN_BYTES and drift > SIZE_DRIFT_FRACTION * entry.size_bytes:
        return (
            f"Changed since the scan (was {_size(entry.size_bytes)}, now "
            f"{_size(walk.size)}); rescan first."
        )
    return None
