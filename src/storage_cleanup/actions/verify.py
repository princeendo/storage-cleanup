"""Check an entry against the disk immediately before acting on it."""

import os
import stat
from pathlib import Path, PurePath
from typing import Final

from storage_cleanup.actions.model import Method, PlanEntry
from storage_cleanup.scan.measure import measure
from storage_cleanup.scan.model import ItemKind, Scan
from storage_cleanup.scan.protected import (
    GIT_REASON,
    containing_reason,
    home_store_paths,
    protected_reason,
)

SIZE_DRIFT_FRACTION: Final = 0.10
SIZE_DRIFT_MIN_BYTES: Final = 100 * 1024**2
STORE_SEARCH_DEPTH: Final = 3
_STORE_SUFFIXES: Final = {
    ".photoslibrary": "Photos library",
    ".sparsebundle": "Time Machine",
    ".backupbundle": "Time Machine",
}
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


def _stores_inside(path: Path) -> list[tuple[PurePath, str]]:
    """Protected stores found within a few levels of `path` (no symlinks followed)."""
    found: list[tuple[PurePath, str]] = []
    stack: list[tuple[Path, int]] = [(path, 0)]
    while stack:
        current, depth = stack.pop()
        try:
            entries = list(os.scandir(current))
        except OSError:
            continue
        for entry in entries:
            if not entry.is_dir(follow_symlinks=False):
                continue
            child = Path(entry.path)
            for suffix, reason in _STORE_SUFFIXES.items():
                if entry.name.lower().endswith(suffix):
                    found.append((child, reason))
                    break
            else:
                if depth + 1 < STORE_SEARCH_DEPTH:
                    stack.append((child, depth + 1))
    return found


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
    path = Path(entry.path)
    home = Path(scan.home)
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return "No longer exists."
    except OSError as exc:
        return f"Cannot check it: {exc.strerror or exc}."

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
    if stat.S_ISDIR(st.st_mode):
        stores.extend(_stores_inside(path))
    reason = containing_reason(path, stores)
    if reason is not None:
        return f"Protected: {reason}"

    item = next((i for i in scan.items if i.id == entry.item_id), None)
    if item is not None:
        is_dir = stat.S_ISDIR(st.st_mode)
        if (item.kind in _DIRECTORY_KINDS and not is_dir) or (
            item.kind == ItemKind.FILE and is_dir
        ):
            return "Changed type since the scan."

    now, _ = measure(path, collector="verify")
    drift = abs(now.size_bytes - entry.size_bytes)
    if drift > SIZE_DRIFT_MIN_BYTES and drift > SIZE_DRIFT_FRACTION * entry.size_bytes:
        return (
            f"Changed since the scan (was {_size(entry.size_bytes)}, now "
            f"{_size(now.size_bytes)}); rescan first."
        )
    return None
