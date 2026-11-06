"""Run queue.js under macOS JavaScriptCore."""

import json
import subprocess
from pathlib import Path

import pytest

JSC = "/System/Library/Frameworks/JavaScriptCore.framework/Versions/Current/Helpers/jsc"
QUEUE_JS = Path(__file__).parents[2] / "src/storage_cleanup/web/static/queue.js"

FAKE_STORAGE = """
const data = new Map();
globalThis.sessionStorage = {
  getItem: (k) => (data.has(k) ? data.get(k) : null),
  setItem: (k, v) => { data.set(k, v); },
};
"""
THROWING_STORAGE = """
globalThis.sessionStorage = {
  getItem: () => { throw new Error("blocked"); },
  setItem: () => { throw new Error("blocked"); },
};
"""


def run_js(tmp_path: Path, body: str, storage: str = FAKE_STORAGE) -> object:
    if not Path(JSC).exists():
        pytest.skip("jsc (JavaScriptCore) is not available")
    driver = tmp_path / "driver.mjs"
    driver.write_text(
        f"{storage}\n"
        f'const q = await import("{QUEUE_JS}");\n'
        "const E = (id) => ({ item_id: id, name: id, path: `/h/${id}`, size: 1 });\n"
        "const ids = (queue) => queue.queued().map((e) => e.item_id);\n"
        f"print(JSON.stringify((() => {{ {body} }})()));\n"
    )
    done = subprocess.run([JSC, "-m", str(driver)], capture_output=True, text=True)
    assert done.returncode == 0, done.stdout + done.stderr
    return json.loads(done.stdout.strip().splitlines()[-1])


def test_add_has_remove_clear(tmp_path: Path) -> None:
    body = """
      const s = q.makeQueue("k");
      s.add([E("a"), E("b")]);
      const afterAdd = [ids(s), s.has("a"), s.has("z")];
      s.remove(["a"]);
      const afterRemove = ids(s);
      s.clear();
      return [afterAdd, afterRemove, ids(s)];
    """
    assert run_js(tmp_path, body) == [[["a", "b"], True, False], ["b"], []]


def test_top_level_exports_are_the_delete_queue(tmp_path: Path) -> None:
    body = """
      q.add([E("a")]);
      const raw = sessionStorage.getItem("storage-cleanup:delete-queue");
      return [q.has("a"), ids(q.archive), JSON.parse(raw).length];
    """
    assert run_js(tmp_path, body) == [True, [], 1]


def test_adding_to_one_queue_removes_from_the_other(tmp_path: Path) -> None:
    body = """
      q.add([E("a"), E("b")]);
      q.archive.add([E("a")]);
      const first = [ids(q), ids(q.archive)];
      q.add([E("a")]);
      return [first, [ids(q), ids(q.archive)]];
    """
    assert run_js(tmp_path, body) == [
        [["b"], ["a"]],
        [["b", "a"], []],
    ]


def test_move_notifies_both_queues(tmp_path: Path) -> None:
    body = """
      let deleteCalls = 0;
      q.onChange(() => { deleteCalls += 1; });
      q.add([E("a")]);
      const before = deleteCalls;
      q.archive.add([E("a")]);
      return [before, deleteCalls];
    """
    assert run_js(tmp_path, body) == [1, 2]


def test_queues_persist_under_their_own_keys(tmp_path: Path) -> None:
    body = """
      q.archive.add([E("a")]);
      const raw = sessionStorage.getItem("storage-cleanup:archive-queue");
      const other = sessionStorage.getItem("storage-cleanup:delete-queue");
      return [JSON.parse(raw).map((e) => e.item_id), other];
    """
    assert run_js(tmp_path, body) == [["a"], None]


def test_storage_that_throws_still_works_in_memory(tmp_path: Path) -> None:
    body = """
      q.add([E("a")]);
      q.archive.add([E("b")]);
      return [ids(q), ids(q.archive)];
    """
    assert run_js(tmp_path, body, THROWING_STORAGE) == [["a"], ["b"]]
