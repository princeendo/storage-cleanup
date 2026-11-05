"""The home folder tree: the "where is it" lens."""

import os
import stat
from pathlib import Path
from typing import Final

from storage_cleanup.scan.categories import HOME
from storage_cleanup.scan.context import CollectorResult, ScanContext
from storage_cleanup.scan.ids import item_id
from storage_cleanup.scan.measure import Measurement, measure_tree
from storage_cleanup.scan.model import AgeSignal, Item, ItemKind, SizeSource

HOME_ITEM_DEPTH: Final = 3
HOME_MIN_ITEM_BYTES: Final = 50 * 1024**2


def _is_dir(path: Path) -> bool:
    try:
        return stat.S_ISDIR(os.lstat(path).st_mode)
    except OSError:
        return False


def path_item(path: Path, m: Measurement, categories: tuple[str, ...]) -> Item:
    """Build a folder or file item from a measurement."""
    kind = ItemKind.FOLDER if _is_dir(path) else ItemKind.FILE
    return Item(
        id=item_id(kind, str(path)),
        kind=kind,
        name=path.name,
        path=str(path),
        size_bytes=m.size_bytes,
        size_source=SizeSource.ALLOCATED,
        categories=categories,
        age_signal=AgeSignal.LAST_MODIFIED if m.newest_mtime is not None else None,
        age_timestamp=m.newest_mtime,
        file_count=m.file_count,
    )


def collect_home(ctx: ScanContext) -> CollectorResult:
    measured, skipped = measure_tree(
        ctx.home,
        collector="home",
        max_item_depth=HOME_ITEM_DEPTH,
        skip=ctx.privacy_skip(),
    )
    items: list[Item] = []
    for path, m in measured.items():
        if path == ctx.home:
            continue
        depth = len(path.relative_to(ctx.home).parts)
        if depth == 1 or m.size_bytes >= HOME_MIN_ITEM_BYTES:
            items.append(path_item(path, m, (HOME,)))
    return CollectorResult(items=tuple(items), skipped=skipped)
