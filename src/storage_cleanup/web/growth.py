"""What changed since the previous scan, and the used-space history."""

import posixpath
import threading
from collections.abc import Sequence
from pathlib import Path
from typing import Final

from storage_cleanup.scan.model import Item, JsonValue, Scan, scan_from_json

GROWTH_MIN_BYTES: Final = 100 * 1024**2
GROWTH_ROWS: Final = 8
SHRINK_ROWS: Final = 5
HISTORY_LIMIT: Final = 60
EXPLAINED_FRACTION: Final = 0.8

_CACHE: dict[tuple[str, int, int], dict[str, JsonValue] | None] = {}
_CACHE_LOCK: Final = threading.Lock()  # the page server handles requests in threads


def _path_items(scan: Scan) -> dict[str, Item]:
    return {i.id: i for i in scan.items if i.path is not None}


def _is_under(path: str, ancestor: str) -> bool:
    return path.startswith(ancestor.rstrip("/") + "/")


def _row(item_id: str, now: Item | None, before: Item | None) -> dict[str, JsonValue]:
    shown = now or before
    assert shown is not None
    old = before.size_bytes if before is not None else 0
    new = now.size_bytes if now is not None else 0
    status = "new" if before is None else "gone" if now is None else "changed"
    return {
        "item_id": item_id,
        "name": shown.name,
        "path": shown.path,
        "before": old,
        "after": new,
        "change": new - old,
        "status": status,
    }


def _change(row: dict[str, JsonValue]) -> int:
    value = row["change"]
    assert isinstance(value, int)
    return value


def _really_appeared(row: dict[str, JsonValue], changes: dict[str, int]) -> bool:
    """Whether a new or gone item really changed, not just gained or lost detail.

    `scan --path` (and similar) measures a folder in more detail, so items appear
    that the previous scan never looked for. They count only when the nearest
    enclosing item measured in both scans changed by about as much.
    """
    if row["status"] == "changed":
        return True
    path = row["path"]
    assert isinstance(path, str)
    change = _change(row)
    parent = posixpath.dirname(path)
    while parent != path:
        if parent in changes:
            enclosing = changes[parent]
            return enclosing * change > 0 and abs(
                enclosing
            ) >= EXPLAINED_FRACTION * abs(change)
        path, parent = parent, posixpath.dirname(parent)
    return True


def growth(scan: Scan, previous: Scan) -> dict[str, JsonValue]:
    """The biggest items that grew or shrank, preferring the most specific item."""
    current = _path_items(scan)
    older = _path_items(previous)
    changes = {
        str(item.path): item.size_bytes - older[item_id].size_bytes
        for item_id, item in current.items()
        if item_id in older
    }
    candidates = [
        row
        for item_id in current.keys() | older.keys()
        if abs(_change(row := _row(item_id, current.get(item_id), older.get(item_id))))
        >= GROWTH_MIN_BYTES
        and _really_appeared(row, changes)
    ]

    def explained(row: dict[str, JsonValue]) -> bool:
        path = row["path"]
        assert isinstance(path, str)
        change = _change(row)
        return any(
            isinstance(other["path"], str)
            and _is_under(other["path"], path)
            and _change(other) * change > 0
            and abs(_change(other)) >= EXPLAINED_FRACTION * abs(change)
            for other in candidates
        )

    kept = [r for r in candidates if not explained(r)]
    kept.sort(key=lambda r: (-abs(_change(r)), str(r["path"])))
    grew = [r for r in kept if _change(r) > 0][:GROWTH_ROWS]
    shrank = [r for r in kept if _change(r) < 0][:SHRINK_ROWS]
    return {
        "previous_created_at": previous.created_at,
        "used_change": scan.disk.used_bytes - previous.disk.used_bytes,
        "grew": list(grew),
        "shrank": list(shrank),
    }


def _summary(path: Path) -> dict[str, JsonValue] | None:
    try:
        stat = path.stat()
        key = (str(path), stat.st_mtime_ns, stat.st_size)
    except OSError:
        return None
    with _CACHE_LOCK:
        if key in _CACHE:
            return _CACHE[key]
    try:
        scan = scan_from_json(path.read_text(encoding="utf-8"))
        entry: dict[str, JsonValue] | None = {
            "created_at": scan.created_at,
            "used_bytes": scan.disk.used_bytes,
            "free_bytes": scan.disk.free_bytes,
        }
    except Exception:  # an unreadable scan is simply left out of the history
        entry = None
    with _CACHE_LOCK:
        while len(_CACHE) >= 2 * HISTORY_LIMIT:
            del _CACHE[next(iter(_CACHE))]
        _CACHE[key] = entry
    return entry


def history(scan_paths: Sequence[Path]) -> list[dict[str, JsonValue]]:
    """Used and free space of the newest scans, oldest first."""
    entries = (_summary(p) for p in sorted(scan_paths, key=lambda p: p.name))
    found = [e for e in entries if e is not None]
    return found[-HISTORY_LIMIT:]
