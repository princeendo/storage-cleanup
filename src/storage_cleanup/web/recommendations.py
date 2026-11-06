"""Recommendations for the page: merged, nested, rolled up, and ranked."""

from collections.abc import Sequence
from typing import Final

from storage_cleanup.actions.model import Method
from storage_cleanup.actions.plan import removal_method
from storage_cleanup.scan.model import Item, JsonValue, Scan
from storage_cleanup.scan.recommend import Recommendation, score
from storage_cleanup.web.views import category_groups

ROLLUP_MIN_ITEMS: Final = 3
_DAY: Final = 86_400.0

type Row = dict[str, JsonValue]


def merge_recommendations(
    scan: Scan, rule: Sequence[Recommendation], agent: Sequence[Recommendation]
) -> list[Recommendation]:
    """Agent recommendations win per item; unknown or protected items are dropped."""
    items = {i.id: i for i in scan.items}
    merged: dict[str, Recommendation] = {}
    for rec in (*rule, *agent):
        item = items.get(rec.item_id)
        if item is None or item.protected_reason is not None:
            continue
        merged[rec.item_id] = rec
    return list(merged.values())


def _recommended_ancestor(
    item: Item, by_id: dict[str, Item], recommended: set[str]
) -> str | None:
    seen: set[str] = set()
    parent_id = item.parent_id
    while parent_id is not None and parent_id not in seen:
        if parent_id in recommended:
            return parent_id
        seen.add(parent_id)
        parent = by_id.get(parent_id)
        parent_id = parent.parent_id if parent is not None else None
    return None


def reclaimable_bytes(scan: Scan, recs: Sequence[Recommendation]) -> int:
    """Space the recommendations would free, counting nested items once."""
    by_id = {i.id: i for i in scan.items}
    recommended = {r.item_id for r in recs if r.item_id in by_id}
    return sum(
        by_id[item_id].size_bytes
        for item_id in recommended
        if _recommended_ancestor(by_id[item_id], by_id, recommended) is None
    )


def _int(value: JsonValue) -> int:
    return value if isinstance(value, int) else 0


def _score_of(row: Row) -> float:
    value = row["score"]
    return float(value) if isinstance(value, int | float) else 0.0


def recommendation_rows(
    scan: Scan, recs: Sequence[Recommendation], now: float
) -> list[Row]:
    """One row per recommendation; nested items sit under their ancestor's row,
    and three or more siblings with the same action roll up into one row."""
    by_id = {i.id: i for i in scan.items}
    groups = category_groups(scan.items)
    valid = [r for r in recs if r.item_id in by_id]
    recommended = {r.item_id for r in valid}

    rows: dict[str, Row] = {}
    for rec in valid:
        item = by_id[rec.item_id]
        age = (
            max(0.0, (now - item.age_timestamp) / _DAY)
            if item.age_timestamp is not None
            else None
        )
        rows[rec.item_id] = {
            "id": rec.item_id,
            "item_id": rec.item_id,
            "name": item.name,
            "path": item.path,
            "size": item.size_bytes,
            "age_days": age,
            "age_signal": item.age_signal.value if item.age_signal else None,
            "category": groups[item.id],
            "action": rec.action,
            "confidence": rec.confidence,
            "reason": rec.reason,
            "source": rec.source,
            "rule": rec.rule,
            "score": score(rec.confidence, item.size_bytes),
            "protected": False,
            "removable": removal_method(item, permanent=False) is not None,
        }

    top: list[Row] = []
    nested: dict[str, list[Row]] = {}
    for rec in valid:
        ancestor = _recommended_ancestor(by_id[rec.item_id], by_id, recommended)
        if ancestor is None:
            top.append(rows[rec.item_id])
        else:
            nested.setdefault(ancestor, []).append(rows[rec.item_id])
    for item_id, children in nested.items():
        ordered: list[JsonValue] = [*sorted(children, key=_score_of, reverse=True)]
        rows[item_id]["children"] = ordered

    # Roll up only like with like: same folder, same action, same rule.
    siblings: dict[tuple[str, str, str], list[Row]] = {}
    for row in top:
        parent_id = by_id[str(row["item_id"])].parent_id
        if parent_id is not None and parent_id in by_id:
            key = (parent_id, str(row["action"]), str(row["rule"]))
            siblings.setdefault(key, []).append(row)

    rolled = {
        id(row)
        for group in siblings.values()
        if len(group) >= ROLLUP_MIN_ITEMS
        for row in group
    }
    result: list[Row] = [row for row in top if id(row) not in rolled]
    for (parent_id, action, rule), group in siblings.items():
        if len(group) < ROLLUP_MIN_ITEMS:
            continue
        parent = by_id[parent_id]
        size = sum(_int(r["size"]) for r in group)
        confidence = min(float(str(r["confidence"])) for r in group)
        members: list[JsonValue] = [*sorted(group, key=_score_of, reverse=True)]
        reasons = {str(r["reason"]) for r in group}
        sources = {str(r["source"]) for r in group}
        categories = {r["category"] for r in group}
        result.append(
            {
                "id": f"rollup:{parent_id}:{action}" + (f":{rule}" if rule else ""),
                "item_id": None,
                "name": f"{len(group)} items in {parent.name}",
                "path": parent.path,
                "size": size,
                "age_days": None,
                "age_signal": None,
                "category": categories.pop() if len(categories) == 1 else None,
                "action": action,
                "confidence": confidence,
                "reason": reasons.pop()
                if len(reasons) == 1
                else f"{len(group)} items; open to see each reason",
                "source": sources.pop() if len(sources) == 1 else "mixed",
                "rule": rule,
                "score": score(confidence, size),
                "protected": False,
                "removable": any(r["removable"] for r in group),
                "children": members,
            }
        )
    return sorted(result, key=_score_of, reverse=True)


def target_candidates(scan: Scan, recs: Sequence[Recommendation]) -> list[Row]:
    """Removable recommended items for target mode, best score first.

    An item under another candidate is left out, because its parent covers it."""
    by_id = {i.id: i for i in scan.items}
    groups = category_groups(scan.items)
    chosen = {
        r.item_id: r.confidence
        for r in recs
        if (item := by_id.get(r.item_id)) is not None
        and item.protected_reason is None
        and removal_method(item, permanent=False) is not None
    }
    rows: list[Row] = []
    for item_id in chosen:
        item = by_id[item_id]
        if _recommended_ancestor(item, by_id, set(chosen)) is not None:
            continue
        rows.append(
            {
                "item_id": item_id,
                "name": item.name,
                "path": item.path,
                "size": item.size_bytes,
                "score": score(chosen[item_id], item.size_bytes),
                "category": groups[item_id],
                "frees_now": removal_method(item, permanent=False) is not Method.TRASH,
            }
        )
    return sorted(rows, key=lambda r: (_score_of(r), _int(r["size"])), reverse=True)
