import json
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

from storage_cleanup.scan.aggregate import category_totals, location_totals
from storage_cleanup.scan.ids import item_id
from storage_cleanup.scan.model import AgeSignal, DiskUsage, Item, Scan, SizeSource
from storage_cleanup.scan.run import finalize_items
from storage_cleanup.web.views import (
    DOCKER_DISK,
    TreeNode,
    build_view,
    category_tree,
    exclusive_sizes,
    location_tree,
    overview,
    primary_category,
    project_rows,
    scatter_points,
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
    tree = category_tree(scan_of([LIB, BREW, DL, CLAUDE, PROJ]), small_min_bytes=0)
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
    assert len(smaller.children) == 50
    assert {c.kind for c in smaller.children} == {"item"}


def test_smaller_box_shows_largest_and_bundles_the_rest() -> None:
    folder = path_item("/h/Movies", 120 * MIB + 120)
    clips = [path_item(f"/h/Movies/c{i}", MIB + i, kind="file") for i in range(120)]
    tree = category_tree(scan_of([folder, *clips], used=1024**4))
    smaller = find(tree, f"smaller:{folder.id}")
    assert smaller.name == "120 smaller items"
    shown = [c for c in smaller.children if c.kind == "item"]
    [more] = [c for c in smaller.children if c.kind == "more"]
    assert len(shown) == 50
    assert shown[0].value == MIB + 119
    assert more.name == "70 more items"
    assert more.children == []
    assert sum(c.value for c in smaller.children) == smaller.value


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
    assert len(all_nodes(tree)) < 2_000


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


GIB = 1024**3
DAY = 86_400.0
NOW = 1_800_000_000.0


def aged(item: Item, days: float, signal: str = "last-modified") -> Item:
    return replace(item, age_signal=AgeSignal(signal), age_timestamp=NOW - days * DAY)


def test_scatter_includes_specific_kinds_and_top_level_only() -> None:
    app = aged(
        path_item("/Applications/Big.app", 2 * GIB, ("applications",), kind="app"), 10
    )
    deep = aged(path_item("/h/Movies/a/b", 2 * GIB), 10)
    movies = aged(path_item("/h/Movies", 3 * GIB), 10)
    points = scatter_points(scan_of([app, deep, movies], used=10 * GIB), NOW)
    assert {p["id"] for p in points} == {app.id, movies.id}
    point = next(p for p in points if p["id"] == app.id)
    assert point["category"] == "applications"
    assert point["size"] == 2 * GIB
    assert point["age_days"] == 10
    assert point["age_signal"] == "last-modified"
    assert point["protected"] is False


def test_scatter_includes_direct_downloads_children() -> None:
    downloads = aged(path_item("/h/Downloads", 3 * GIB), 1)
    dmg = aged(
        path_item("/h/Downloads/x.dmg", 2 * GIB, ("downloads",), kind="file"), 50
    )
    points = scatter_points(scan_of([downloads, dmg], used=10 * GIB), NOW)
    assert dmg.id in {p["id"] for p in points}


def test_scatter_skips_small_and_ageless() -> None:
    small = aged(path_item("/h/Small", 5 * MIB), 10)
    ageless = path_item("/h/Ageless", 2 * GIB)
    assert scatter_points(scan_of([small, ageless], used=10 * GIB), NOW) == []


def test_in_zone_flag() -> None:
    old_big = aged(path_item("/h/A", 2 * GIB), 400)
    new_big = aged(path_item("/h/B", 2 * GIB), 30)
    old_small = aged(path_item("/h/C", 500 * MIB), 900)
    points = {
        p["id"]: p
        for p in scatter_points(
            scan_of([old_big, new_big, old_small], used=10 * GIB), NOW
        )
    }
    assert points[old_big.id]["in_zone"] is True
    assert points[new_big.id]["in_zone"] is False
    assert points[old_small.id]["in_zone"] is False


def test_future_ages_floor_at_zero() -> None:
    future = aged(path_item("/h/F", 2 * GIB), -5)
    [point] = scatter_points(scan_of([future], used=10 * GIB), NOW)
    assert point["age_days"] == 0


def repo(name: str, size: int, regen: int, days: float | None) -> Item:
    item = path_item(
        f"/h/workspace/{name}",
        size,
        ("projects",),
        kind="repo",
        details={
            "regenerable_bytes": regen,
            "dirty": True,
            "unpushed_commits": 2,
            "has_remote": False,
        },
    )
    return aged(item, days, "last-commit") if days is not None else item


def test_project_rows_sorted_and_split() -> None:
    a = repo("a", 10 * GIB, 1 * GIB, 30)
    b = repo("b", 5 * GIB, 4 * GIB, 400)
    c = repo("c", 1 * GIB, 0, None)
    rows = project_rows(scan_of([a, b, c], used=100 * GIB), NOW)
    assert [r["name"] for r in rows] == ["b", "a", "c"]
    assert rows[0]["own_bytes"] == 1 * GIB
    assert rows[0]["regenerable_bytes"] == 4 * GIB
    assert rows[0]["last_commit_days"] == 400
    assert rows[0]["dirty"] is True
    assert rows[0]["unpushed_commits"] == 2
    assert rows[0]["has_remote"] is False
    assert rows[2]["last_commit_days"] is None


def test_overview_handles_unreadable_trash() -> None:
    data = overview(scan_of([LIB], used=300))
    assert data["trash_bytes"] is None
    assert data["used_bytes"] == 300
    assert data["free_bytes"] == 300
    assert data["total_bytes"] == 600
    assert data["percent_free"] == 50.0
    assert data["item_count"] == 1
    assert data["skipped"] == []


def test_build_view_is_json_serializable_with_hostile_names() -> None:
    evil = "<img src=x onerror=alert(1)>"
    item = aged(path_item(f"/h/{evil}", 2 * GIB), 10)
    view = build_view(scan_of([item], used=10 * GIB), NOW)
    assert evil in json.dumps(view, ensure_ascii=False)
    assert view["schema"] == 1
    assert set(view) == {
        "schema", "overview", "categories", "locations", "scatter",
        "projects", "reclaim_zone", "category_names",
    }  # fmt: skip
    names = view["category_names"]
    assert isinstance(names, dict)
    assert names["system-other"] == "macOS and everything not scanned"


def test_build_view_empty_scan() -> None:
    view = build_view(scan_of([], used=500), NOW)
    assert view["scatter"] == []
    assert view["projects"] == []
    tree = json.loads(json.dumps(view["categories"]))
    assert [c["kind"] for c in tree["children"]] == ["system"]


def test_small_top_level_groups_are_grouped() -> None:
    big = path_item("/h/Movies", 100 * GIB)
    tiny = [path_item(f"/h/.t{i}", MIB) for i in range(5)]
    tree = location_tree(scan_of([big, *tiny], used=200 * GIB))
    kinds = [(c.kind, c.name) for c in tree.children]
    assert ("smaller", "5 smaller items") in kinds
    assert kinds[-1][0] == "system"
    assert sum(c.value for c in tree.children) == 200 * GIB


def test_grouping_many_children_is_fast() -> None:
    import time

    folder = path_item("/h/Downloads", 20_000 * 1024)
    files = [path_item(f"/h/Downloads/f{i}", 1024, kind="file") for i in range(20_000)]
    scan = scan_of([folder, *files], used=1024**4)
    start = time.perf_counter()
    category_tree(scan)
    location_tree(scan)
    assert time.perf_counter() - start < 3


def test_children_reported_bigger_than_parent_are_capped() -> None:
    total = replace(
        path_item("/x", 10, ("docker",), kind="docker-total"),
        id=item_id("docker-total", "docker"),
        path=None,
        size_source=SizeSource.TOOL_REPORTED,
    )
    images = [
        replace(
            path_item(f"/img{i}", 8, ("docker",), kind="docker-image"),
            path=None,
            parent_id=total.id,
            size_source=SizeSource.TOOL_REPORTED,
        )
        for i in range(3)
    ]
    tree = category_tree(scan_of([total, *images], used=100), small_min_bytes=0)
    values = {c.group: c.value for c in tree.children}
    assert values == {"docker": 10, "system-other": 90}


def test_parent_cycles_do_not_crash() -> None:
    a = path_item("/h/A", 5)
    b = path_item("/h/B", 5)
    a = replace(a, parent_id=b.id)
    b = replace(b, parent_id=a.id)
    selfish = replace(path_item("/h/C", 5), parent_id=item_id("folder", "/h/C"))
    scan = scan_of([], used=100)
    scan = replace(scan, items=(a, b, selfish))
    tree = category_tree(scan)
    assert all(n.value >= 0 for n in all_nodes(tree))
    assert build_view(scan, NOW)["schema"] == 1
