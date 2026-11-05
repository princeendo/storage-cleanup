"""Test helpers: synthetic files and a fake command runner."""

import os
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from storage_cleanup.scan.context import CommandResult, Runner, ScanContext
from storage_cleanup.scan.ids import item_id
from storage_cleanup.scan.model import Item, JsonValue, SizeSource


def make_file(path: Path, size: int, *, mtime: float | None = None) -> Path:
    """Write ``size`` non-zero bytes so blocks are really allocated."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


@dataclass
class FakeRunner:
    """Answers commands from a table; unknown commands look not installed."""

    responses: dict[tuple[str, ...], CommandResult] = field(default_factory=dict)
    calls: list[tuple[str, ...]] = field(default_factory=list)

    def __call__(self, args: Sequence[str]) -> CommandResult:
        key = tuple(args)
        self.calls.append(key)
        return self.responses.get(key, CommandResult(127, "", "not found"))


def ok(stdout: str) -> CommandResult:
    return CommandResult(0, stdout, "")


def path_item(
    path: str,
    size: int,
    categories: tuple[str, ...] = ("home",),
    *,
    kind: str = "folder",
    parent_id: str | None = None,
    details: dict[str, JsonValue] | None = None,
) -> Item:
    return Item(
        id=item_id(kind, path),
        kind=kind,
        name=Path(path).name,
        path=path,
        size_bytes=size,
        size_source=SizeSource.ALLOCATED,
        categories=categories,
        parent_id=parent_id,
        details=details or {},
    )


def make_ctx(home: Path, runner: Runner | None = None, **kwargs: Any) -> ScanContext:
    home.mkdir(parents=True, exist_ok=True)
    defaults: dict[str, Any] = {
        "home": home,
        "app_dirs": (home / "Applications",),
        "workspace_roots": (home / "workspace",),
        "brew_prefix": None,
        "run": runner or FakeRunner(),
        "now": 1_793_900_000.0,
    }
    defaults.update(kwargs)
    return ScanContext(**defaults)
