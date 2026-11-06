import threading
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from storage_cleanup.scan import model
from storage_cleanup.scan.model import Item, Scan, scan_to_json
from storage_cleanup.web import growth as growth_module
from storage_cleanup.web.growth import (
    GROWTH_MIN_BYTES,
    GROWTH_ROWS,
    HISTORY_LIMIT,
    SHRINK_ROWS,
    growth,
    history,
)
from tests.helpers import path_item
from tests.web.test_views import scan_of

MIB = 1024**2
GIB = 1024**3


def pair(
    before: list[Item], after: list[Item], used: tuple[int, int] = (10 * GIB, 12 * GIB)
) -> tuple[Scan, Scan]:
    return scan_of(after, used=used[1]), scan_of(before, used=used[0])


def rows(result: Any, key: str) -> dict[str, Any]:
    return {r["name"]: r for r in result[key]}


def test_grown_shrunk_new_and_gone_rows() -> None:
    now, old = pair(
        [
            path_item("/h/grew", GIB),
            path_item("/h/shrank", 3 * GIB),
            path_item("/h/gone", 2 * GIB),
        ],
        [
            path_item("/h/grew", 2 * GIB),
            path_item("/h/shrank", GIB),
            path_item("/h/new", GIB),
        ],
    )
    result: Any = growth(now, old)
    assert result["previous_created_at"] == old.created_at
    grew = rows(result, "grew")
    shrank = rows(result, "shrank")
    assert set(grew) == {"grew", "new"}
    assert set(shrank) == {"shrank", "gone"}
    assert grew["grew"]["before"] == GIB
    assert grew["grew"]["after"] == 2 * GIB
    assert grew["grew"]["change"] == GIB
    assert grew["grew"]["status"] == "changed"
    assert grew["grew"]["path"] == "/h/grew"
    assert grew["grew"]["item_id"] == path_item("/h/grew", 1).id
    assert grew["new"]["before"] == 0
    assert grew["new"]["status"] == "new"
    assert shrank["gone"]["after"] == 0
    assert shrank["gone"]["change"] == -2 * GIB
    assert shrank["gone"]["status"] == "gone"
    assert [r["name"] for r in result["shrank"]] == ["gone", "shrank"]


def test_used_change_is_disk_difference() -> None:
    now, old = pair([], [], used=(10 * GIB, 13 * GIB))
    result: Any = growth(now, old)
    assert result["used_change"] == 3 * GIB
    assert result["grew"] == [] and result["shrank"] == []


def test_threshold_is_100_mib() -> None:
    now, old = pair(
        [path_item("/h/a", GIB), path_item("/h/b", GIB)],
        [
            path_item("/h/a", GIB + GROWTH_MIN_BYTES - 1),
            path_item("/h/b", GIB + GROWTH_MIN_BYTES),
        ],
    )
    result: Any = growth(now, old)
    assert [r["name"] for r in result["grew"]] == ["b"]


def test_child_preferred_over_parent_it_explains() -> None:
    parent_before = path_item("/h/Lib", GIB)
    parent_after = path_item("/h/Lib", 2 * GIB)
    child = path_item("/h/Lib/Caches", GIB, parent_id=parent_after.id)
    child_before = path_item("/h/Lib/Caches", 0)
    now, old = pair([parent_before, child_before], [parent_after, child])
    result: Any = growth(now, old)
    assert [r["name"] for r in result["grew"]] == ["Caches"]


def test_parent_kept_when_children_explain_less_than_80_percent() -> None:
    now, old = pair(
        [path_item("/h/Lib", GIB), path_item("/h/Lib/C", 0)],
        [path_item("/h/Lib", 2 * GIB), path_item("/h/Lib/C", 700 * MIB)],
    )
    result: Any = growth(now, old)
    assert {r["name"] for r in result["grew"]} == {"Lib", "C"}


def test_sibling_with_shared_prefix_is_not_a_descendant() -> None:
    now, old = pair(
        [path_item("/h/Lib", 0), path_item("/h/Library", 0)],
        [path_item("/h/Lib", GIB), path_item("/h/Library", GIB)],
    )
    result: Any = growth(now, old)
    assert {r["name"] for r in result["grew"]} == {"Lib", "Library"}


def test_opposite_sign_descendant_does_not_hide_parent() -> None:
    now, old = pair(
        [path_item("/h/Lib", GIB), path_item("/h/Lib/C", 2 * GIB)],
        [path_item("/h/Lib", 2 * GIB), path_item("/h/Lib/C", 0)],
    )
    result: Any = growth(now, old)
    assert [r["name"] for r in result["grew"]] == ["Lib"]
    assert [r["name"] for r in result["shrank"]] == ["C"]


def test_row_limits() -> None:
    names = [f"/h/g{i}" for i in range(12)]
    shrinks = [f"/h/s{i}" for i in range(9)]
    now, old = pair(
        [path_item(p, 0) for p in names] + [path_item(p, 2 * GIB) for p in shrinks],
        [path_item(p, GIB + i * MIB) for i, p in enumerate(names)]
        + [path_item(p, GIB) for p in shrinks],
    )
    result: Any = growth(now, old)
    assert len(result["grew"]) == GROWTH_ROWS
    assert len(result["shrank"]) == SHRINK_ROWS
    changes = [r["change"] for r in result["grew"]]
    assert changes == sorted(changes, reverse=True)


# -- history ---------------------------------------------------------------


def write(directory: Path, name: str, created: str, used: int) -> Path:
    scan = replace(scan_of([], used=used), created_at=created)
    path = directory / name
    path.write_text(scan_to_json(scan))
    return path


def test_history_is_oldest_first_and_limited(tmp_path: Path) -> None:
    growth_module._CACHE.clear()
    paths = [
        write(
            tmp_path, f"{i:03d}.json", f"2026-01-01T00:{i // 60:02d}:{i % 60:02d}Z", i
        )
        for i in range(HISTORY_LIMIT + 5)
    ]
    result = history(paths)
    assert len(result) == HISTORY_LIMIT
    assert result[0]["used_bytes"] == 5
    assert result[-1]["used_bytes"] == HISTORY_LIMIT + 4
    assert set(result[0]) == {"created_at", "used_bytes", "free_bytes"}
    assert result[0]["free_bytes"] == 5


def test_history_skips_broken_files(tmp_path: Path) -> None:
    growth_module._CACHE.clear()
    good = write(tmp_path, "a.json", "2026-01-01T00:00:00Z", 7)
    bad = tmp_path / "b.json"
    bad.write_text("{not json")
    missing = tmp_path / "c.json"
    result = history([good, bad, missing])
    assert [r["used_bytes"] for r in result] == [7]


def test_history_reuses_cache(tmp_path: Path) -> None:
    growth_module._CACHE.clear()
    paths = [write(tmp_path, f"{i}.json", "2026-01-01T00:00:00Z", i) for i in range(3)]
    with patch.object(
        growth_module, "scan_from_json", wraps=model.scan_from_json
    ) as spy:
        first = history(paths)
        assert spy.call_count == 3
        assert history(paths) == first
        assert spy.call_count == 3
        paths[0].write_text(
            scan_to_json(
                replace(scan_of([], used=99), created_at="2026-01-01T00:00:00Z")
            )
        )
        again = history(paths)
        assert spy.call_count == 4
    assert 99 in [r["used_bytes"] for r in again]


def test_history_cache_is_capped(tmp_path: Path) -> None:
    growth_module._CACHE.clear()
    paths = [
        write(tmp_path, f"{i:03d}.json", "2026-01-01T00:00:00Z", i)
        for i in range(2 * HISTORY_LIMIT + 10)
    ]
    history(paths)
    assert len(growth_module._CACHE) <= 2 * HISTORY_LIMIT


def test_history_cache_is_thread_safe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    growth_module._CACHE.clear()
    monkeypatch.setattr(growth_module, "HISTORY_LIMIT", 4)
    paths = [
        write(tmp_path, f"{i:03d}.json", "2026-01-01T00:00:00Z", i) for i in range(40)
    ]
    errors: list[BaseException] = []
    results: list[list[int]] = []

    def work() -> None:
        try:
            for _ in range(20):
                results.append([r["used_bytes"] for r in history(paths)])  # type: ignore[misc]
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=work) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert all(r == [36, 37, 38, 39] for r in results)
    assert len(growth_module._CACHE) <= 8
