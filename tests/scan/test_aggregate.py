from collections.abc import Sequence
from dataclasses import replace

from storage_cleanup.scan.aggregate import category_totals, link_tree, location_totals
from storage_cleanup.scan.model import Item
from tests.helpers import path_item

GB = 1_000_000_000


def by_path(items: Sequence[Item]) -> dict[str | None, Item]:
    return {i.path: i for i in items}


def test_same_path_merges_and_keeps_specific_kind() -> None:
    folder = path_item("/h/Library/Caches/Homebrew", 5, ("home",))
    cache = path_item(
        "/h/Library/Caches/Homebrew",
        5,
        ("homebrew", "dev-caches"),
        kind="cache",
        details={"regenerates": "yes"},
    )
    merged = link_tree([folder, cache])
    assert len(merged) == 1
    assert merged[0].kind == "cache"
    assert merged[0].id == cache.id
    assert merged[0].categories == ("home", "homebrew", "dev-caches")
    assert merged[0].details == {"regenerates": "yes"}


def test_parent_is_nearest_path_ancestor() -> None:
    lib = path_item("/h/Library", 10)
    brew = path_item("/h/Library/Caches/Homebrew", 5, kind="cache")
    dl = path_item("/h/Library/Caches/Homebrew/downloads", 3)
    sibling = path_item("/h/Library2", 1)
    items = by_path(link_tree([dl, brew, lib, sibling]))
    assert items["/h/Library"].parent_id is None
    assert items["/h/Library/Caches/Homebrew"].parent_id == lib.id
    assert items["/h/Library/Caches/Homebrew/downloads"].parent_id == brew.id
    assert items["/h/Library2"].parent_id is None


def test_virtual_items_keep_explicit_parent() -> None:
    codex = path_item("/h/.codex", 10, ("ai-tooling",), kind="ai-tool-path")
    virtual = path_item(
        "/x", 4, ("ai-tooling",), kind="ai-sessions", parent_id=codex.id
    )
    virtual = replace(virtual, path=None)
    linked = link_tree([codex, virtual])
    assert [i.parent_id for i in linked if i.path is None] == [codex.id]


def test_category_totals_do_not_double_count_nested_items() -> None:
    parent = path_item("/h/.claude", 10 * GB, ("ai-tooling",))
    child = path_item("/h/.claude/projects", 4 * GB, ("ai-tooling",))
    assert category_totals(link_tree([parent, child]))["ai-tooling"] == 10 * GB


def test_category_totals_count_nested_item_of_other_category() -> None:
    lib = path_item("/h/Library", 10 * GB, ("home",))
    brew = path_item("/h/Library/Caches/Homebrew", 3 * GB, ("homebrew",), kind="cache")
    totals = category_totals(link_tree([lib, brew]))
    assert totals == {"home": 10 * GB, "homebrew": 3 * GB}


def test_location_totals_use_same_rule() -> None:
    dl = path_item("/h/Downloads", 5 * GB)
    movie = path_item("/h/Downloads/movie.mov", 2 * GB, kind="file")
    linked = tuple(replace(i, location="Downloads") for i in link_tree([dl, movie]))
    assert location_totals(linked) == {"Downloads": 5 * GB}


def test_location_totals_are_exclusive_for_nested_labels() -> None:
    ws = replace(path_item("/h/workspace", 100), location="Workspace")
    personal = replace(
        path_item("/h/workspace/personal", 80), location="Your personal data"
    )
    repo = replace(
        path_item("/h/workspace/personal/app", 50, kind="repo"),
        location="Your personal data",
    )
    totals = location_totals(link_tree([ws, personal, repo]))
    assert totals == {"Workspace": 20, "Your personal data": 80}
