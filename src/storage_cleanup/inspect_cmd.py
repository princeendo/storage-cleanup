"""Focused, bounded questions about a scan, for agents with small context windows."""

import re
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from typing import Final

from storage_cleanup.scan.categories import DISPLAY_NAMES
from storage_cleanup.scan.model import Item, JsonValue, Scan
from storage_cleanup.scan.recommend import format_age

_SIZE: Final = re.compile(
    r"^\s*([0-9]*\.?[0-9]+)\s*([kmgt]?)(?:i?b)?\s*$", re.IGNORECASE
)
_UNITS: Final = {"": 1, "k": 1024, "m": 1024**2, "g": 1024**3, "t": 1024**4}
_DAY: Final = 86_400.0
GIB: Final = 1024**3


@dataclass(frozen=True)
class Query:
    category: str | None = None
    location: str | None = None
    kind: str | None = None
    path_prefix: str | None = None
    min_bytes: int = 0
    older_than_days: float | None = None
    include_protected: bool = False
    limit: int = 50
    sort: str = "size"


def parse_size(text: str) -> int:
    """'500M', '2G', '10GiB', '1234' -> bytes (binary units)."""
    match = _SIZE.match(text)
    if not match:
        raise ValueError(f"Not a size: {text!r} (try 500M, 2G, or 10GiB)")
    return int(float(match.group(1)) * _UNITS[match.group(2).lower()])


def _age(item: Item, now: float) -> float | None:
    if item.age_timestamp is None:
        return None
    return max(0.0, (now - item.age_timestamp) / _DAY)


def run_query(scan: Scan, query: Query, now: float) -> list[Item]:
    """Every matching item, sorted; callers decide how many to show."""
    found: list[Item] = []
    for item in scan.items:
        if item.protected_reason is not None and not query.include_protected:
            continue
        if query.category and query.category not in item.categories:
            continue
        if query.location and item.location != query.location:
            continue
        if query.kind and item.kind != query.kind:
            continue
        if query.path_prefix and not (item.path or "").startswith(query.path_prefix):
            continue
        if item.size_bytes < query.min_bytes:
            continue
        if query.older_than_days is not None:
            age = _age(item, now)
            if age is None or age < query.older_than_days:
                continue
        found.append(item)
    if query.sort == "age":
        found.sort(key=lambda i: (i.age_timestamp is None, i.age_timestamp or 0.0))
    else:
        found.sort(key=lambda i: i.size_bytes, reverse=True)
    return found


def _size(n: int) -> str:
    value = float(n)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if value < 1024:
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TiB"


def compact(item: Item, now: float) -> dict[str, JsonValue]:
    age = _age(item, now)
    return {
        "id": item.id,
        "size": item.size_bytes,
        "age_days": round(age, 1) if age is not None else None,
        "age_signal": item.age_signal.value if item.age_signal else None,
        "kind": item.kind,
        "categories": list(item.categories),
        "location": item.location,
        "path": item.path,
        "name": item.name,
        "protected": item.protected_reason,
        "details": dict(item.details),
    }


def format_rows(items: Sequence[Item], now: float, *, limit: int = 50) -> str:
    """A tab-separated table: header, at most `limit` rows, and a 'more' line."""
    lines = ["id\tsize\tage\tkind\tcategories\tpath"]
    for item in items[:limit]:
        age = _age(item, now)
        signal = item.age_signal.value if item.age_signal else ""
        age_text = f"{format_age(age)} ({signal})" if age is not None else "unknown"
        lines.append(
            "\t".join(
                [
                    item.id,
                    _size(item.size_bytes),
                    age_text,
                    item.kind,
                    ",".join(item.categories),
                    item.path or item.name,
                ]
            )
        )
    if len(items) > limit:
        lines.append(f"… {len(items) - limit} more; narrow the query or raise --limit.")
    return "\n".join(lines)


def summary(scan: Scan) -> str:
    disk = scan.disk
    lines = [
        f"Scan: {scan.created_at} · {len(scan.items)} items",
        f"Disk: {_size(disk.used_bytes)} used, {_size(disk.free_bytes)} free of "
        f"{_size(disk.total_bytes)}",
        "",
        "By category (overlapping lenses; do not add these up):",
    ]
    for cat, size in sorted(scan.totals_by_category.items(), key=lambda kv: -kv[1]):
        lines.append(f"  {_size(size):>10}  {cat} ({DISPLAY_NAMES.get(cat, cat)})")
    lines.append("")
    lines.append("By location:")
    for loc, size in sorted(scan.totals_by_location.items(), key=lambda kv: -kv[1]):
        lines.append(f"  {_size(size):>10}  {loc}")
    lines.append("")
    lines.append(f"Skipped paths: {len(scan.skipped)}")
    return "\n".join(lines)


def item_details(item: Item) -> dict[str, JsonValue]:
    data: dict[str, JsonValue] = {}
    for key, value in asdict(item).items():
        data[key] = list(value) if isinstance(value, tuple) else value
    return data
