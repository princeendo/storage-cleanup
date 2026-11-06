"""List, hash and compare an archived item on the Mac and on the homelab.

Paths in every Manifest are relative to the folder that contains the item, so
they start with the item's own name. This module never talks to the homelab;
it only builds the command and reads the answer.
"""

import hashlib
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from storage_cleanup.scan.model import JsonValue

# Runs on the homelab as: sh -c LIST_SCRIPT sh <folder holding the item>.
# Records are NUL-separated. D <path>, L <path> <target>, F <path> <size>,
# then H, then `sha256sum -z` lines ("<hash>  ./<path>").
LIST_SCRIPT: Final = (
    'cd -- "$1" && find . -mindepth 1 \\( -type d -printf "D\\0%P\\0" \\) '
    '-o \\( -type l -printf "L\\0%P\\0%l\\0" \\) '
    '-o \\( -type f -printf "F\\0%P\\0%s\\0" \\) '
    '&& printf "H\\0" && find . -type f -exec sha256sum -z -- {} +'
)
MAX_PROBLEMS: Final = 5
_CHUNK: Final = 1024 * 1024


class UnsupportedItem(Exception):
    """A socket, FIFO or device inside the item; it cannot be archived."""


@dataclass(frozen=True)
class Manifest:
    files: dict[str, tuple[int, str]]  # relative path -> (bytes, sha256 hex)
    links: dict[str, str]  # relative path -> link target
    dirs: frozenset[str]
    # Trouble found while reading a listing; compare reports it.
    problems: tuple[str, ...] = ()


def list_command(directory: str) -> list[str]:
    """The one `sh` command the remote allowlist accepts."""
    return ["sh", "-c", LIST_SCRIPT, "sh", directory]


def _hash_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while chunk := handle.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


# relative path -> (size, mtime_ns, ctime_ns, inode), from lstat only.
type Snapshot = dict[str, tuple[int, int, int, int]]


def _fingerprint(info: os.stat_result) -> tuple[int, int, int, int]:
    return (info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_ino)


def _walk(
    path: str,
    rel: str,
    files: dict[str, tuple[int, str]],
    links: dict[str, str],
    dirs: set[str],
    seen: Snapshot,
) -> None:
    info = os.lstat(path)
    seen[rel] = _fingerprint(info)  # taken before hashing, so a change shows
    mode = info.st_mode
    if stat.S_ISLNK(mode):
        links[rel] = os.fsdecode(os.readlink(path))
    elif stat.S_ISREG(mode):
        files[rel] = (info.st_size, _hash_file(path))
    elif stat.S_ISDIR(mode):
        dirs.add(rel)
        with os.scandir(path) as entries:
            children = sorted(entry.name for entry in entries)
        for name in children:
            _walk(
                os.path.join(path, name),
                f"{rel}/{os.fsdecode(name)}",
                files,
                links,
                dirs,
                seen,
            )
    else:
        raise UnsupportedItem(f"{rel} is not a regular file, folder or link")


def local_manifest_and_snapshot(path: Path) -> tuple[Manifest, Snapshot]:
    """Walk an item on this Mac without following links, hashing its files.

    The snapshot holds what lstat said about every path in the same walk, so a
    later `snapshot(path)` shows whether anything changed since the hashing.
    """
    files: dict[str, tuple[int, str]] = {}
    links: dict[str, str] = {}
    dirs: set[str] = set()
    seen: Snapshot = {}
    _walk(os.fspath(path), os.fsdecode(path.name), files, links, dirs, seen)
    return Manifest(files, links, frozenset(dirs)), seen


def local_manifest(path: Path) -> Manifest:
    """Walk an item on this Mac without following links, hashing its files."""
    return local_manifest_and_snapshot(path)[0]


def _snap(path: str, rel: str, seen: Snapshot) -> None:
    info = os.lstat(path)
    seen[rel] = _fingerprint(info)
    if stat.S_ISDIR(info.st_mode):
        with os.scandir(path) as entries:
            names = sorted(entry.name for entry in entries)
        for name in names:
            _snap(os.path.join(path, name), f"{rel}/{os.fsdecode(name)}", seen)


def snapshot(path: Path) -> Snapshot:
    """lstat every path in the item again, without hashing or following links."""
    seen: Snapshot = {}
    _snap(os.fspath(path), os.fsdecode(path.name), seen)
    return seen


def parse_listing(data: str) -> Manifest:
    """Read LIST_SCRIPT's output (decoded with surrogateescape)."""
    tokens = data.split("\0")
    if tokens and tokens[-1] == "":
        tokens.pop()
    files: dict[str, tuple[int, str]] = {}
    links: dict[str, str] = {}
    dirs: set[str] = set()
    problems: list[str] = []
    i = 0
    while i < len(tokens) and tokens[i] != "H":
        kind = tokens[i]
        width = {"D": 1, "L": 2, "F": 2}.get(kind)
        if width is None or i + width >= len(tokens):
            problems.append("the homelab's listing is malformed")
            break
        path = tokens[i + 1]
        if kind == "D":
            dirs.add(path)
        elif kind == "L":
            links[path] = tokens[i + 2]
        else:
            try:
                size = int(tokens[i + 2])
            except ValueError:
                problems.append(f"{path}: unreadable size from the homelab")
                size = -1
            files[path] = (size, "")
        i += 1 + width
    else:
        if i >= len(tokens):
            problems.append("the homelab's listing is incomplete")
    hashes: dict[str, str] = {}
    for token in tokens[i + 1 :]:
        digest, sep, name = token.partition("  ")
        if not sep:
            problems.append("the homelab's checksum list is malformed")
            continue
        hashes[name.removeprefix("./")] = digest
    for path in files:
        if path in hashes:
            files[path] = (files[path][0], hashes.pop(path))
        else:
            problems.append(f"{path}: no checksum from the homelab")
    problems.extend(f"{path}: checksum without a file" for path in hashes)
    return Manifest(files, links, frozenset(dirs), tuple(problems))


def compare(local: Manifest, remote: Manifest, limit: int = MAX_PROBLEMS) -> list[str]:
    """Short problems, [] when the homelab copy is identical."""
    found: list[str] = list(remote.problems)
    for path, (size, digest) in sorted(local.files.items()):
        if path not in remote.files:
            found.append(f"{path}: missing on the homelab")
        elif remote.files[path][0] != size:
            found.append(f"{path}: size differs")
        elif remote.files[path][1] != digest:
            found.append(f"{path}: checksum differs")
    for path, target in sorted(local.links.items()):
        if path not in remote.links:
            found.append(f"{path}: missing on the homelab")
        elif remote.links[path] != target:
            found.append(f"{path}: link differs")
    found.extend(
        f"{path}: missing on the homelab" for path in sorted(local.dirs - remote.dirs)
    )
    known = set(local.files) | set(local.links) | local.dirs
    extra = set(remote.files) | set(remote.links) | remote.dirs
    found.extend(f"{path}: extra on the homelab" for path in sorted(extra - known))
    return found[:limit]


def manifest_to_json(m: Manifest, base: str) -> list[dict[str, JsonValue]]:
    """Rows for the action log; `base` is the folder holding the item."""
    prefix = base.rstrip("/")
    rows: list[dict[str, JsonValue]] = []
    for path, (size, digest) in sorted(m.files.items()):
        rows.append(
            {
                "path": f"{prefix}/{path}",
                "kind": "file",
                "size": size,
                "sha256": digest,
            }
        )
    for path, target in sorted(m.links.items()):
        rows.append({"path": f"{prefix}/{path}", "kind": "link", "target": target})
    rows.extend({"path": f"{prefix}/{path}", "kind": "dir"} for path in sorted(m.dirs))
    return rows
