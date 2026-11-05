from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

from storage_cleanup.scan.aggregate import category_totals, location_totals
from storage_cleanup.scan.ids import item_id
from storage_cleanup.scan.model import DiskUsage, Item, Scan, SizeSource
from storage_cleanup.scan.run import finalize_items
from storage_cleanup.web.views import (
    DOCKER_DISK,
    TreeNode,
    category_tree,
    exclusive_sizes,
    location_tree,
    primary_category,
)
from tests.helpers import path_item

HOME = Path("/h")
MIB = 1024**2

LIB = path_item("/h/Library", 100, ("home",))
BREW = path_item(
    "/h/Library/Caches/Homebrew", 30, ("home", "homebrew", "dev-caches"), kind="cache"
)
DL = path_item("/h/Library/Caches/Homebrew/downloads", 20, ("home",))
CLAUDE = path_item("/h/.claude", 50, ("home", "ai-tooling"), kind="ai-tool-path")
PROJ = path_item("/h/.claude/projects", 40, ("ai-tooling",))


def scan_of(items: Sequence[Item], used: int = 300) -> Scan:
    finished = finalize_items(items, HOME)
    return Scan(
        schema_version=1,
        created_at="2026-11-05T14:20:00Z",
        home=str(HOME),
        disk=DiskUsage("/", used * 2, used, used),
        trash_bytes=None,
        local_snapshots=(),
        items=finished,
        skipped=(),
        totals_by_category=category_totals(finished),
        totals_by_location=location_totals(finished),
    )


def find(node: TreeNode, node_id: str) -> TreeNode:
    if node.id == node_id:
        return node
    for child in node.children:
        try:
            return find(child, node_id)
        except KeyError:
            continue
    raise KeyError(node_id)


def all_nodes(node: TreeNode) -> list[TreeNode]:
    return [node, *(n for c in node.children for n in all_nodes(c))]


def test_primary_category_precedence() -> None:
    assert primary_category(BREW) == "homebrew"
    assert primary_category(DL) == "home"
    assert primary_category(CLAUDE) == "ai-tooling"


def test_exclusive_sizes() -> None:
    scan = scan_of([LIB, BREW, DL, CLAUDE, PROJ])
    assert exclusive_sizes(scan.items) == {
        LIB.id: 70,
        BREW.id: 10,
        DL.id: 20,
        CLAUDE.id: 10,
        PROJ.id: 40,
    }


def test_category_tree_partitions_used_space() -> None:
    tree = category_tree(scan_of([LIB, BREW, DL, CLAUDE, PROJ]))
    assert tree.name == "Disk used"
    assert tree.value == 300
    values = {c.group: c.value for c in tree.children}
    assert values == {"home": 70, "homebrew": 30, "ai-tooling": 50, "system-other": 150}
    assert [c.group for c in tree.children][-1] == "system-other"
    assert tree.children[0].group == "home"
    assert find(tree, "group:homebrew").name == "Homebrew"


def test_weak_home_items_inherit_specific_ancestor() -> None:
    tree = category_tree(scan_of([LIB, BREW, DL, CLAUDE, PROJ]), small_min_bytes=0)
    brew = find(tree, BREW.id)
    assert brew.value == 30
    assert sorted((c.kind, c.value) for c in brew.children) == [
        ("item", 20),
        ("rest", 10),
    ]
    assert find(tree, f"rest:{BREW.id}").name == "Other files in Homebrew"
    assert find(tree, DL.id).group == "homebrew"


def test_children_larger_than_parent_never_go_negative() -> None:
    parent = path_item("/h/Movies", 10)
    child = path_item("/h/Movies/big", 25)
    tree = category_tree(scan_of([parent, child], used=100))
    assert all(n.value >= 0 for n in all_nodes(tree))
    assert exclusive_sizes(scan_of([parent, child]).items)[parent.id] == 0


def test_location_tree_uses_labels_and_inherits() -> None:
    downloads = path_item("/h/Downloads", 40)
    movie = path_item("/h/Downloads/a.mov", 30, kind="file")
    personal = path_item("/h/workspace/personal", 50)
    total = replace(
        path_item("/x", 60, ("docker",), kind="docker-total"),
        id=item_id("docker-total", "docker"),
        path=None,
    )
    image = replace(
        path_item("/y", 60, ("docker",), kind="docker-image"),
        path=None,
        parent_id=total.id,
    )
    tree = location_tree(
        scan_of([downloads, movie, personal, total, image]), small_min_bytes=0
    )
    values = {c.group: c.value for c in tree.children}
    assert values == {
        "Downloads": 40,
        "Your personal data": 50,
        DOCKER_DISK: 60,
        "system-other": 150,
    }
    assert find(tree, image.id).group == DOCKER_DISK


def test_small_children_are_grouped() -> None:
    folder = path_item("/h/Movies", 50 * MIB)
    clips = [path_item(f"/h/Movies/c{i}", MIB, kind="file") for i in range(50)]
    tree = category_tree(scan_of([folder, *clips], used=1024**4))
    movies = find(tree, folder.id)
    [smaller] = [c for c in movies.children if c.kind == "smaller"]
    assert smaller.name == "50 smaller items"
    assert smaller.value == 50 * MIB
    assert smaller.id == f"smaller:{folder.id}"


def test_single_small_child_is_not_grouped() -> None:
    big = path_item("/h/Movies", 10 * 1024**3)
    small = path_item("/h/Movies/tiny", MIB, kind="file")
    tree = category_tree(scan_of([big, small], used=1024**4))
    assert sorted(c.kind for c in find(tree, big.id).children) == ["item", "rest"]


def test_large_scan_node_count_is_bounded() -> None:
    folders = [path_item(f"/h/f{i}", 1000 * 100 * 1024) for i in range(20)]
    files = [
        path_item(f"/h/f{i}/x{j}", 100 * 1024, kind="file")
        for i in range(20)
        for j in range(1000)
    ]
    tree = category_tree(scan_of([*folders, *files], used=1024**4))
    assert len(all_nodes(tree)) < 500


def test_empty_scan() -> None:
    tree = category_tree(scan_of([], used=500))
    assert [(c.kind, c.value) for c in tree.children] == [("system", 500)]


def test_to_json_omits_empty_children() -> None:
    tree = category_tree(scan_of([LIB]))
    data = tree.to_json()
    assert isinstance(data["children"], list)
    leaf = find(tree, LIB.id).to_json()
    assert "children" not in leaf
    assert leaf["path"] == "/h/Library"
    assert leaf["value"] == 100


def test_items_are_unsized_tool_reports_still_count() -> None:
    item = replace(
        path_item("/z", 5, ("docker",), kind="docker-total"),
        path=None,
        size_source=SizeSource.TOOL_REPORTED,
    )
    tree = category_tree(scan_of([item]))
    assert {c.group: c.value for c in tree.children}["docker"] == 5
