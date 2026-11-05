"""Join items into one containment tree and total them without double counting."""

from collections.abc import Callable, Sequence
from dataclasses import replace
from pathlib import PurePath

from storage_cleanup.scan.model import Item, ItemKind, JsonValue

_GENERIC_KINDS = frozenset({ItemKind.FOLDER, ItemKind.FILE})


def _merge(group: list[Item]) -> Item:
    specific = next((i for i in group if i.kind not in _GENERIC_KINDS), group[0])
    categories: list[str] = []
    details: dict[str, JsonValue] = {}
    for item in group:
        for cat in item.categories:
            if cat not in categories:
                categories.append(cat)
        if item is not specific:
            details.update(item.details)
    details.update(specific.details)
    parent = specific.parent_id or next(
        (i.parent_id for i in group if i.parent_id), None
    )
    return replace(
        specific, categories=tuple(categories), details=details, parent_id=parent
    )


def link_tree(items: Sequence[Item]) -> tuple[Item, ...]:
    """Merge items that share a path and link each to its nearest ancestor."""
    groups: dict[str, list[Item]] = {}
    order: list[str | Item] = []
    for item in items:
        if item.path is None:
            order.append(item)
            continue
        if item.path not in groups:
            groups[item.path] = []
            order.append(item.path)
        groups[item.path].append(item)

    remap: dict[str, str] = {}
    merged: list[Item] = []
    for entry in order:
        if isinstance(entry, Item):
            merged.append(entry)
            continue
        kept = _merge(groups[entry])
        for item in groups[entry]:
            remap[item.id] = kept.id
        merged.append(kept)

    by_path = {i.path: i.id for i in merged if i.path is not None}
    result: list[Item] = []
    for item in merged:
        parent = remap.get(item.parent_id, item.parent_id) if item.parent_id else None
        if parent is None and item.path is not None:
            for ancestor in PurePath(item.path).parents:
                found = by_path.get(str(ancestor))
                if found is not None:
                    parent = found
                    break
        result.append(
            replace(item, parent_id=parent) if parent != item.parent_id else item
        )
    return tuple(result)


def _totals(
    items: Sequence[Item], keys: Callable[[Item], tuple[str, ...]]
) -> dict[str, int]:
    by_id = {i.id: i for i in items}
    totals: dict[str, int] = {}
    for item in items:
        for key in keys(item):
            ancestor_id = item.parent_id
            counted_above = False
            seen: set[str] = set()
            while ancestor_id is not None and ancestor_id not in seen:
                seen.add(ancestor_id)
                ancestor = by_id.get(ancestor_id)
                if ancestor is None:
                    break
                if key in keys(ancestor):
                    counted_above = True
                    break
                ancestor_id = ancestor.parent_id
            if not counted_above:
                totals[key] = totals.get(key, 0) + item.size_bytes
    return totals


def category_totals(items: Sequence[Item]) -> dict[str, int]:
    """Sum sizes per category, skipping items already inside one of that category."""
    return _totals(items, lambda i: i.categories)


def location_totals(items: Sequence[Item]) -> dict[str, int]:
    """Sum sizes per location label with the same no-double-counting rule."""
    return _totals(items, lambda i: (i.location,) if i.location else ())
