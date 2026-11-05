"""Turn a scan into the page's data: partitions that add up, points, and rows."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Final

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
from storage_cleanup.scan.model import Item, JsonValue, Scan

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

    def group(item: Item) -> str:
        if item.id in memo:
            return memo[item.id]
        mine = own(item)
        result: str
        if inherits(mine):
            parent = by_id.get(item.parent_id) if item.parent_id else None
            result = group(parent) if parent is not None else (mine or fallback(item))
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
        kept = [c for c in node.children if c not in small]
        kept.append(
            TreeNode(
                id=f"smaller:{node.id}",
                name=f"{len(small)} smaller items",
                value=sum(c.value for c in small),
                kind="smaller",
                group=node.group,
            )
        )
        node.children = kept
    for child in node.children:
        _group_small(child, small_min_bytes)


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
        value = exclusive[item.id] + sum(c.value for c in same)
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
    for child in root.children:
        _group_small(child, small_min_bytes)
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
