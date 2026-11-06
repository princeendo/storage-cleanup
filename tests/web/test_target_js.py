"""Run the pure functions of target.js under macOS JavaScriptCore."""

import json
import subprocess
from pathlib import Path

import pytest

JSC = "/System/Library/Frameworks/JavaScriptCore.framework/Versions/Current/Helpers/jsc"
TARGET_JS = Path(__file__).parents[2] / "src/storage_cleanup/web/static/target.js"
GIB = 1024**3


def run_js(tmp_path: Path, body: str) -> object:
    if not Path(JSC).exists():
        pytest.skip("jsc (JavaScriptCore) is not available")
    driver = tmp_path / "driver.mjs"
    driver.write_text(
        f'import {{ bytesNeeded, fill }} from "{TARGET_JS}";\n'
        "const C = (id, path, size) => ({ item_id: id, path, size });\n"
        f"print(JSON.stringify((() => {{ {body} }})()));\n"
    )
    done = subprocess.run([JSC, "-m", str(driver)], capture_output=True, text=True)
    assert done.returncode == 0, done.stdout + done.stderr
    return json.loads(done.stdout.strip().splitlines()[-1])


def ids(tmp_path: Path, body: str) -> object:
    return run_js(
        tmp_path, f"const r = (() => {{ {body} }})(); return r.map((c) => c.item_id);"
    )


def test_docker_candidate_first(tmp_path: Path) -> None:
    body = 'return fill([C("d", null, 5), C("a", "/h/a", 5), C("b", "/h/b", 5)], 12);'
    assert ids(tmp_path, body) == ["d", "a", "b"]


def test_docker_candidate_after_path_item(tmp_path: Path) -> None:
    body = 'return fill([C("a", "/h/a", 5), C("d", null, 5), C("b", "/h/b", 5)], 12);'
    assert ids(tmp_path, body) == ["a", "d", "b"]


def test_docker_candidate_with_queued_items(tmp_path: Path) -> None:
    body = (
        'return fill([C("d", null, 5), C("a", "/h/a", 5)], 12,'
        ' [C("q", "/h/q", 1), C("qd", null, 1)]);'
    )
    assert ids(tmp_path, body) == ["d", "a"]


def test_queued_docker_item_is_not_picked_again(tmp_path: Path) -> None:
    body = 'return fill([C("d", null, 5), C("a", "/h/a", 5)], 20, [C("d", null, 5)]);'
    assert ids(tmp_path, body) == ["a"]


def test_nested_paths_take_only_the_folder(tmp_path: Path) -> None:
    body = (
        'return fill([C("f", "/h/x", 5), C("c", "/h/x/y", 5),'
        ' C("p", "/h", 5), C("o", "/h/xx", 5)], 100);'
    )
    # /h/x/y is inside /h/x; /h holds /h/x; /h/xx is a sibling, not a child.
    assert ids(tmp_path, body) == ["f", "o"]


def test_nothing_needed_picks_nothing(tmp_path: Path) -> None:
    assert ids(tmp_path, 'return fill([C("a", "/h/a", 5)], 0);') == []
    assert ids(tmp_path, 'return fill([C("a", "/h/a", 5)], -3);') == []
    assert ids(tmp_path, 'return fill([C("a", "/h/a", 5)], NaN);') == []


def test_stops_once_goal_is_met(tmp_path: Path) -> None:
    body = 'return fill([C("a", "/h/a", 6), C("b", "/h/b", 6), C("c", "/h/c", 6)], 12);'
    assert ids(tmp_path, body) == ["a", "b"]
    body = (
        'return fill([C("a", "/h/a", 6), C("b", "/h/b", 6)], 7, [C("q", "/h/q", 7)]);'
    )
    assert ids(tmp_path, body) == []


def test_bytes_needed(tmp_path: Path) -> None:
    ov = f"{{ total_bytes: {100 * GIB}, free_bytes: {10 * GIB} }}"
    got = run_js(
        tmp_path,
        f"const o = {ov}; return [bytesNeeded(o, 'percent', '15'),"
        " bytesNeeded(o, 'amount', '2'), bytesNeeded(o, 'percent', '5'),"
        " String(bytesNeeded(o, 'percent', 'x'))];",
    )
    assert got == [5 * GIB, 2 * GIB, -5 * GIB, "NaN"]


def test_archive_queued_items_block_but_do_not_count(tmp_path: Path) -> None:
    # "a" sits in the Archive queue: neither it nor its child is picked, and
    # its 50 bytes do not count toward the goal of 12.
    body = (
        'return fill([C("a", "/h/a", 50), C("c", "/h/a/c", 6), C("p", "/h", 99),'
        ' C("b", "/h/b", 6), C("d", "/h/d", 6), C("e", "/h/e", 6)], 12, [],'
        ' [C("a", "/h/a", 50)]);'
    )
    assert ids(tmp_path, body) == ["b", "d"]
