"""Turn a scan into the page's data: partitions that add up, points, and rows."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Final

from storage_cleanup.scan.categories import (
    AI_TOOLING,
    APP_DATA,
    APPLICATIONS,
    DEV_CACHES,
    DISPLAY_NAMES,
    DOCKER,
    DOWNLOADS,
    HOME,
    HOMEBREW,
    LEFTOVER_APP_DATA,
    PACKAGES,
    PROJECTS,
)
from storage_cleanup.scan.model import AgeSignal, Item, ItemKind, JsonValue, Scan

if TYPE_CHECKING:
    from storage_cleanup.findings import Findings

CATEGORY_PRECEDENCE: Final[tuple[str, ...]] = (
    AI_TOOLING,
    DOCKER,
    APPLICATIONS,
    LEFTOVER_APP_DATA,
    APP_DATA,
    HOMEBREW,
    PACKAGES,
    DEV_CACHES,
    PROJECTS,
    DOWNLOADS,
    HOME,
)
SYSTEM_OTHER: Final = "system-other"
SYSTEM_OTHER_NAME: Final = "macOS and everything not scanned"
OTHER_LOCATIONS: Final = "Other locations"
DOCKER_DISK: Final = "Docker virtual disk"
SMALL_MIN_BYTES: Final = 64 * 1024**2
SMALL_FRACTION: Final = 0.01
SMALLER_SHOWN: Final = 50  # items shown when a "smaller items" box is opened


@dataclass
class TreeNode:
    id: str
    name: str
    value: int
    kind: str
    group: str
    path: str | None = None
    children: list["TreeNode"] = field(default_factory=list)

    def to_json(self) -> dict[str, JsonValue]:
        data: dict[str, JsonValue] = {
            "id": self.id,
            "name": self.name,
            "value": self.value,
            "kind": self.kind,
            "group": self.group,
            "path": self.path,
        }
        if self.children:
            data["children"] = [c.to_json() for c in self.children]
        return data


def primary_category(item: Item) -> str:
    for category in CATEGORY_PRECEDENCE:
        if category in item.categories:
            return category
    return HOME


def _children(items: Sequence[Item]) -> dict[str, list[Item]]:
    ids = {i.id for i in items}
    kids: dict[str, list[Item]] = {}
    for item in items:
        if item.parent_id is not None and item.parent_id in ids:
            kids.setdefault(item.parent_id, []).append(item)
    return kids


def exclusive_sizes(items: Sequence[Item]) -> dict[str, int]:
    """Each item's own bytes: its size minus its direct children, never negative."""
    kids = _children(items)
    return {
        i.id: max(0, i.size_bytes - sum(c.size_bytes for c in kids.get(i.id, [])))
        for i in items
    }


def _groups(
    items: Sequence[Item],
    own: Callable[[Item], str | None],
    inherits: Callable[[str | None], bool],
    fallback: Callable[[Item], str],
) -> dict[str, str]:
    by_id = {i.id: i for i in items}
    memo: dict[str, str] = {}
    visiting: set[str] = set()

    def group(item: Item) -> str:
        if item.id in memo:
            return memo[item.id]
        mine = own(item)
        result: str
        if inherits(mine):
            parent = by_id.get(item.parent_id) if item.parent_id else None
            if parent is not None and parent.id in visiting | {item.id}:
                parent = None  # a broken scan with a parent cycle
            visiting.add(item.id)
            result = group(parent) if parent is not None else (mine or fallback(item))
            visiting.discard(item.id)
        else:
            assert mine is not None
            result = mine
        memo[item.id] = result
        return result

    return {i.id: group(i) for i in items}


def category_groups(items: Sequence[Item]) -> dict[str, str]:
    """Each item's category group; generic home items take their ancestor's."""
    return _groups(items, primary_category, lambda g: g == HOME, lambda _: HOME)


def _location_fallback(item: Item) -> str:
    return DOCKER_DISK if DOCKER in item.categories else OTHER_LOCATIONS


def location_groups(items: Sequence[Item]) -> dict[str, str]:
    return _groups(items, lambda i: i.location, lambda g: g is None, _location_fallback)


def _group_small(node: TreeNode, small_min_bytes: int) -> None:
    threshold = max(small_min_bytes, SMALL_FRACTION * node.value)
    small = [c for c in node.children if c.value < threshold and c.kind != "system"]
    if len(small) >= 2:
        small_ids = {id(c) for c in small}
        kept = [
            c for c in node.children if id(c) not in small_ids and c.kind != "system"
        ]
        small.sort(key=lambda c: c.value, reverse=True)
        inside = small[:SMALLER_SHOWN]
        rest = small[SMALLER_SHOWN:]
        if rest:
            inside.append(
                TreeNode(
                    id=f"more:{node.id}",
                    name=f"{len(rest)} more items",
                    value=sum(c.value for c in rest),
                    kind="more",
                    group=node.group,
                )
            )
        kept.append(
            TreeNode(
                id=f"smaller:{node.id}",
                name=f"{len(small)} smaller items",
                value=sum(c.value for c in small),
                kind="smaller",
                group=node.group,
                children=inside,
            )
        )
        kept.extend(c for c in node.children if c.kind == "system")
        node.children = kept
    for child in node.children:
        # A "smaller" box is opened, not regrouped; its items get the usual pass.
        targets = child.children if child.kind == "smaller" else [child]
        for target in targets:
            _group_small(target, small_min_bytes)


def _partition(
    scan: Scan,
    groups: dict[str, str],
    display: Callable[[str], str],
    small_min_bytes: int,
) -> TreeNode:
    items = scan.items
    by_id = {i.id: i for i in items}
    exclusive = exclusive_sizes(items)
    kids = _children(items)

    def node(item: Item) -> TreeNode:
        g = groups[item.id]
        same = [node(c) for c in kids.get(item.id, []) if groups[c.id] == g]
        # Tool-reported children (Docker images share layers) can add up to more
        # than their parent; never let a subtree claim more than was measured.
        value = min(item.size_bytes, exclusive[item.id] + sum(c.value for c in same))
        children = sorted(same, key=lambda n: n.value, reverse=True)
        if same and exclusive[item.id] > 0:
            children.append(
                TreeNode(
                    id=f"rest:{item.id}",
                    name=f"Other files in {item.name}",
                    value=exclusive[item.id],
                    kind="rest",
                    group=g,
                    path=item.path,
                )
            )
        return TreeNode(item.id, item.name, value, "item", g, item.path, children)

    by_group: dict[str, list[TreeNode]] = {}
    for item in items:
        parent = by_id.get(item.parent_id) if item.parent_id else None
        if parent is None or groups[parent.id] != groups[item.id]:
            by_group.setdefault(groups[item.id], []).append(node(item))

    used = scan.disk.used_bytes
    group_nodes = [
        TreeNode(
            id=f"group:{g}",
            name=display(g),
            value=sum(n.value for n in roots),
            kind="group",
            group=g,
            children=sorted(roots, key=lambda n: n.value, reverse=True),
        )
        for g, roots in by_group.items()
    ]
    group_nodes.sort(key=lambda n: n.value, reverse=True)
    scanned = sum(n.value for n in group_nodes)
    group_nodes.append(
        TreeNode(
            id=f"group:{SYSTEM_OTHER}",
            name=SYSTEM_OTHER_NAME,
            value=max(0, used - scanned),
            kind="system",
            group=SYSTEM_OTHER,
        )
    )
    root = TreeNode("root", "Disk used", used, "group", "", children=group_nodes)
    _group_small(root, small_min_bytes)
    return root


def category_tree(scan: Scan, *, small_min_bytes: int = SMALL_MIN_BYTES) -> TreeNode:
    return _partition(
        scan,
        category_groups(scan.items),
        lambda g: DISPLAY_NAMES.get(g, g),
        small_min_bytes,
    )


def location_tree(scan: Scan, *, small_min_bytes: int = SMALL_MIN_BYTES) -> TreeNode:
    return _partition(scan, location_groups(scan.items), lambda g: g, small_min_bytes)


SCATTER_MIN_BYTES: Final = 10 * 1024**2
RECLAIM_MIN_BYTES: Final = 1024**3
RECLAIM_MIN_AGE_DAYS: Final = 365
RECLAIM_ZONE: Final[dict[str, JsonValue]] = {
    "min_bytes": RECLAIM_MIN_BYTES,
    "min_age_days": RECLAIM_MIN_AGE_DAYS,
}
_GENERIC_KINDS: Final = frozenset({ItemKind.FOLDER, ItemKind.FILE})
_DAY: Final = 86_400.0


def _age_days(item: Item, now: float) -> float | None:
    if item.age_timestamp is None:
        return None
    return max(0.0, (now - item.age_timestamp) / _DAY)


def _is_scatter_unit(item: Item, home: Path) -> bool:
    if item.kind not in _GENERIC_KINDS:
        return True
    if item.path is None:
        return False
    parent = Path(item.path).parent
    return parent == home or parent == home / "Downloads"


def scatter_points(scan: Scan, now: float) -> list[dict[str, JsonValue]]:
    """One dot per meaningful item with a known age and at least 10 MiB."""
    home = Path(scan.home)
    groups = category_groups(scan.items)
    points: list[dict[str, JsonValue]] = []
    for item in scan.items:
        age = _age_days(item, now)
        if (
            age is None
            or item.size_bytes < SCATTER_MIN_BYTES
            or not _is_scatter_unit(item, home)
        ):
            continue
        points.append(
            {
                "id": item.id,
                "name": item.name,
                "path": item.path,
                "size": item.size_bytes,
                "age_days": age,
                "age_signal": item.age_signal.value if item.age_signal else None,
                "category": groups[item.id],
                "protected": item.protected_reason is not None,
                "in_zone": item.size_bytes >= RECLAIM_MIN_BYTES
                and age >= RECLAIM_MIN_AGE_DAYS,
            }
        )
    return points


def _int(value: JsonValue) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def project_rows(scan: Scan, now: float) -> list[dict[str, JsonValue]]:
    """Repositories: your files against regenerable folders, plus unsaved work."""
    rows: list[dict[str, JsonValue]] = []
    for item in scan.items:
        if item.kind != ItemKind.REPO:
            continue
        regenerable = _int(item.details.get("regenerable_bytes"))
        rows.append(
            {
                "id": item.id,
                "name": item.name,
                "path": item.path,
                "size": item.size_bytes,
                "regenerable_bytes": regenerable,
                "own_bytes": max(0, item.size_bytes - regenerable),
                "last_commit_days": _age_days(item, now)
                if item.age_signal is AgeSignal.LAST_COMMIT
                else None,
                "dirty": item.details.get("dirty"),
                "unpushed_commits": item.details.get("unpushed_commits"),
                "has_remote": item.details.get("has_remote"),
            }
        )
    rows.sort(
        key=lambda r: (_int(r["regenerable_bytes"]), _int(r["size"])), reverse=True
    )
    return rows


def overview(scan: Scan) -> dict[str, JsonValue]:
    disk = scan.disk
    percent = (
        round(100 * disk.free_bytes / disk.total_bytes, 1) if disk.total_bytes else 0.0
    )
    return {
        "used_bytes": disk.used_bytes,
        "free_bytes": disk.free_bytes,
        "total_bytes": disk.total_bytes,
        "percent_free": percent,
        "created_at": scan.created_at,
        "trash_bytes": scan.trash_bytes,
        "local_snapshots": len(scan.local_snapshots),
        "skipped": [
            {"path": s.path, "reason": s.reason, "collector": s.collector}
            for s in scan.skipped
        ],
        "item_count": len(scan.items),
    }


def _findings_view(scan: Scan, findings: "Findings") -> dict[str, JsonValue]:
    by_id = {i.id: i for i in scan.items}

    def items(ids: Sequence[str]) -> list[JsonValue]:
        return [
            {
                "id": i,
                "name": by_id[i].name,
                "path": by_id[i].path,
                "size": by_id[i].size_bytes,
            }
            for i in ids
            if i in by_id
        ]

    return {
        "agent": findings.agent,
        "created_at": findings.created_at,
        "headlines": [
            {"title": h.title, "body": h.body, "items": items(h.item_ids)}
            for h in findings.headlines
        ],
        "categories": [
            {
                "id": c.id,
                "name": c.name,
                "description": c.description,
                "item_ids": list(c.item_ids),
                "item_count": len(c.item_ids),
            }
            for c in findings.categories
        ],
    }


def build_view(
    scan: Scan,
    now: float,
    findings: "Findings | None" = None,
    findings_note: str | None = None,
) -> dict[str, JsonValue]:
    """Everything the page needs, as one JSON-ready document."""
    # Imported here: recommendations builds on this module's category groups.
    from storage_cleanup.scan.recommend import rule_recommendations
    from storage_cleanup.web.recommendations import (
        merge_recommendations,
        reclaimable_bytes,
        recommendation_rows,
    )

    names: dict[str, JsonValue] = {**DISPLAY_NAMES, SYSTEM_OTHER: SYSTEM_OTHER_NAME}
    agent_recs = findings.recommendations if findings is not None else ()
    recs = merge_recommendations(scan, rule_recommendations(scan, now), agent_recs)
    summary = overview(scan)
    summary["reclaimable_bytes"] = reclaimable_bytes(scan, recs)
    summary["recommendation_count"] = len(recs)
    return {
        "schema": 1,
        "overview": summary,
        "recommendations": list(recommendation_rows(scan, recs, now)),
        "findings": _findings_view(scan, findings) if findings is not None else None,
        "findings_note": findings_note,
        "categories": category_tree(scan).to_json(),
        "locations": location_tree(scan).to_json(),
        "scatter": list(scatter_points(scan, now)),
        "projects": list(project_rows(scan, now)),
        "reclaim_zone": dict(RECLAIM_ZONE),
        "category_names": names,
    }
