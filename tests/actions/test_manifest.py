import hashlib
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from storage_cleanup.actions.manifest import (
    LIST_SCRIPT,
    Manifest,
    UnsupportedItem,
    compare,
    list_command,
    local_manifest,
    manifest_to_json,
    parse_listing,
)
from storage_cleanup.actions.remote import Remote
from storage_cleanup.scan.context import CommandResult
from tests.actions.test_remote import make_config

AWKWARD = ["a b", "it's", 'q"q', "$x", "`bt`", "-dash", "é", "nl\nname"]


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def make_tree(tmp_path: Path) -> Path:
    big = tmp_path / "Big"
    big.mkdir()
    for i, name in enumerate(AWKWARD):
        (big / name).write_bytes(f"content {i}".encode())
    (big / "empty").mkdir()
    (big / "sub").mkdir()
    (big / "sub" / "inner").write_bytes(b"inner")
    (big / "lnk").symlink_to("../x")
    (big / "hard1").write_bytes(b"same")
    os.link(big / "hard1", big / "hard2")
    return big


def listing_for_tree() -> str:
    """The homelab's answer for make_tree, built by hand."""
    files = {f"Big/{name}": f"content {i}".encode() for i, name in enumerate(AWKWARD)}
    files.update(
        {"Big/sub/inner": b"inner", "Big/hard1": b"same", "Big/hard2": b"same"}
    )
    out = ["D", "Big", "D", "Big/empty", "D", "Big/sub", "L", "Big/lnk", "../x"]
    for name, data in files.items():
        out += ["F", name, str(len(data))]
    out.append("H")
    for name, data in reversed(files.items()):  # any order
        out.append(f"{sha(data)}  ./{name}")
    return "\0".join(out) + "\0"


def test_local_manifest_tree(tmp_path: Path) -> None:
    big = make_tree(tmp_path)
    m = local_manifest(big)
    assert m.dirs == {"Big", "Big/empty", "Big/sub"}
    assert m.links == {"Big/lnk": "../x"}
    assert m.files["Big/nl\nname"] == (len(b"content 7"), sha(b"content 7"))
    assert m.files["Big/$x"] == (len(b"content 3"), sha(b"content 3"))
    assert m.files["Big/hard1"] == m.files["Big/hard2"] == (4, sha(b"same"))
    assert m.files["Big/sub/inner"] == (5, sha(b"inner"))
    assert len(m.files) == len(AWKWARD) + 3


def test_links_are_not_followed(tmp_path: Path) -> None:
    big = tmp_path / "Big"
    big.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret").write_text("x")
    (big / "dirlink").symlink_to(outside)
    (big / "filelink").symlink_to(outside / "secret")
    (big / "dangling").symlink_to("nowhere")
    m = local_manifest(big)
    assert m.files == {}
    assert m.dirs == {"Big"}
    assert set(m.links) == {"Big/dirlink", "Big/filelink", "Big/dangling"}


def test_link_item_itself(tmp_path: Path) -> None:
    (tmp_path / "t").write_text("x")
    (tmp_path / "ln").symlink_to("t")
    m = local_manifest(tmp_path / "ln")
    assert m.links == {"ln": "t"}
    assert m.files == {}


def test_single_file_item(tmp_path: Path) -> None:
    f = tmp_path / "movie.mkv"
    f.write_bytes(b"x" * 2_500_000)
    m = local_manifest(f)
    assert m == Manifest(
        {"movie.mkv": (2_500_000, sha(b"x" * 2_500_000))}, {}, frozenset()
    )
    remote = parse_listing(
        f"F\0movie.mkv\0{2_500_000}\0H\0{m.files['movie.mkv'][1]}  ./movie.mkv\0"
    )
    assert compare(m, remote) == []


def test_fifo_is_unsupported(tmp_path: Path) -> None:
    big = tmp_path / "Big"
    big.mkdir()
    os.mkfifo(big / "pipe")
    with pytest.raises(UnsupportedItem, match="pipe"):
        local_manifest(big)


def test_parse_listing_matches_local(tmp_path: Path) -> None:
    local = local_manifest(make_tree(tmp_path))
    remote = parse_listing(listing_for_tree())
    assert remote == local
    assert compare(local, remote) == []


def test_missing_hash_is_a_problem(tmp_path: Path) -> None:
    local = local_manifest(make_tree(tmp_path))
    text = listing_for_tree()
    lines = text.split("\0")
    drop = [t for t in lines if t.endswith("  ./Big/hard1")]
    remote = parse_listing("\0".join(t for t in lines if t not in drop))
    problems = compare(local, remote)
    assert problems
    assert any("Big/hard1" in p for p in problems)


def test_hash_without_file_is_a_problem() -> None:
    remote = parse_listing(f"F\0a\01\0H\0{sha(b'a')}  ./a\0{sha(b'z')}  ./ghost\0")
    problems = compare(Manifest({"a": (1, sha(b"a"))}, {}, frozenset()), remote)
    assert any("ghost" in p for p in problems)


def test_compare_each_difference() -> None:
    h1, h2 = sha(b"1"), sha(b"2")
    local = Manifest(
        {"I/a": (1, h1), "I/b": (1, h1), "I/c": (1, h1), "I/d": (1, h1)},
        {"I/l": "t", "I/l2": "t"},
        frozenset({"I", "I/dir"}),
    )
    remote = Manifest(
        {"I/a": (1, h1), "I/b": (2, h1), "I/c": (1, h2), "I/x": (1, h1)},
        {"I/l": "other"},
        frozenset({"I"}),
    )
    problems = compare(local, remote)
    assert len(problems) == 5  # capped
    everything = compare_all(local, remote)
    text = "\n".join(everything)
    assert "I/d: missing on the homelab" in text
    assert "I/b: size differs" in text
    assert "I/c: checksum differs" in text
    assert "I/l: link differs" in text
    assert "I/x: extra on the homelab" in text
    assert "I/dir: missing on the homelab" in text
    assert "I/l2: missing on the homelab" in text
    assert "I/a" not in text


def compare_all(local: Manifest, remote: Manifest) -> list[str]:
    from storage_cleanup.actions import manifest

    return manifest.compare(local, remote, limit=1000)


def test_manifest_to_json(tmp_path: Path) -> None:
    m = Manifest({"I/a": (1, "h")}, {"I/l": "t"}, frozenset({"I"}))
    rows = manifest_to_json(m, "/dest/.incoming/1")
    assert {
        "path": "/dest/.incoming/1/I/a",
        "kind": "file",
        "size": 1,
        "sha256": "h",
    } in rows
    assert {"path": "/dest/.incoming/1/I/l", "kind": "link", "target": "t"} in rows
    assert {"path": "/dest/.incoming/1/I", "kind": "dir"} in rows


def test_list_command_is_allowed_by_remote() -> None:
    def run(args: object, timeout: float) -> CommandResult:
        return CommandResult(0, "", "")

    remote = Remote(make_config(), "one.example", run)
    argv = list_command("/mnt/x/.incoming/1")
    assert argv == ["sh", "-c", LIST_SCRIPT, "sh", "/mnt/x/.incoming/1"]
    assert remote.ssh_args(argv)[-1].startswith("sudo -n sh -c ")


@pytest.mark.parametrize(
    "argv",
    [
        ["sh", "-c", LIST_SCRIPT + ";rm -rf /", "sh", "x"],
        ["sh", "-c", LIST_SCRIPT, "sh", "x", "y"],
        ["sh", "-c", LIST_SCRIPT, "sh"],
        ["sh", "-c", "id"],
        ["sh"],
    ],
)
def test_other_sh_forms_are_refused(argv: list[str]) -> None:
    def run(args: object, timeout: float) -> CommandResult:
        return CommandResult(0, "", "")

    with pytest.raises(ValueError):
        Remote(make_config(), "one.example", run).ssh_args(argv)


def test_list_script_runs_under_posix_sh(tmp_path: Path) -> None:
    find = subprocess.run(
        ["find", "--version"], capture_output=True, text=True, check=False
    )
    if "GNU" not in find.stdout or shutil.which("sha256sum") is None:
        pytest.skip("needs GNU find and sha256sum")
    local = local_manifest(make_tree(tmp_path))
    done = subprocess.run(
        ["/bin/sh", "-c", LIST_SCRIPT, "sh", str(tmp_path)],
        capture_output=True,
        encoding="utf-8",
        errors="surrogateescape",
        check=True,
    )
    remote = parse_listing(done.stdout)
    assert compare(local, remote) == []
