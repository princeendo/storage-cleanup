"""Command-line packages installed outside Homebrew (pipx, uv, npm, cargo)."""

from pathlib import Path
from typing import Final

from storage_cleanup.scan.categories import PACKAGES
from storage_cleanup.scan.context import CollectorResult, ScanContext
from storage_cleanup.scan.ids import item_id
from storage_cleanup.scan.measure import measure
from storage_cleanup.scan.model import AgeSignal, Item, ItemKind, SizeSource, Skipped

_NPM_BUNDLED: Final = frozenset({"npm", "corepack"})


def _children(path: Path) -> list[Path]:
    try:
        return sorted(p for p in path.iterdir() if not p.name.startswith("."))
    except OSError:
        return []


def _npm_packages(node_modules: Path) -> list[tuple[str, Path]]:
    found: list[tuple[str, Path]] = []
    for entry in _children(node_modules):
        if entry.name.startswith("@"):
            found.extend((f"{entry.name}/{p.name}", p) for p in _children(entry))
        elif entry.name not in _NPM_BUNDLED:
            found.append((entry.name, entry))
    return found


def collect_packages(ctx: ScanContext) -> CollectorResult:
    candidates: list[tuple[str, str, Path]] = []
    for manager, base in (
        ("pipx", ctx.home / ".local" / "pipx" / "venvs"),
        ("uv", ctx.home / ".local" / "share" / "uv" / "tools"),
        ("cargo", ctx.home / ".cargo" / "bin"),
    ):
        candidates.extend((manager, p.name, p) for p in _children(base))
    if ctx.brew_prefix is not None:
        for name, path in _npm_packages(ctx.brew_prefix / "lib" / "node_modules"):
            candidates.append(("npm", name, path))

    items: list[Item] = []
    skipped: list[Skipped] = []
    for manager, name, path in candidates:
        m, sk = measure(path, collector="packages")
        skipped.extend(sk)
        items.append(
            Item(
                id=item_id(ItemKind.PACKAGE, str(path)),
                kind=ItemKind.PACKAGE,
                name=name,
                path=str(path),
                size_bytes=m.size_bytes,
                size_source=SizeSource.ALLOCATED,
                categories=(PACKAGES,),
                age_signal=AgeSignal.LAST_MODIFIED if m.newest_mtime else None,
                age_timestamp=m.newest_mtime,
                file_count=m.file_count,
                details={"manager": manager},
            )
        )
    return CollectorResult(items=tuple(items), skipped=tuple(skipped))
