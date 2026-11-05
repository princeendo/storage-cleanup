"""Detailed measurement of one path, for ``scan --path``."""

from pathlib import Path

from storage_cleanup.scan.categories import HOME
from storage_cleanup.scan.collectors.home import path_item
from storage_cleanup.scan.context import CollectorResult, ScanContext
from storage_cleanup.scan.measure import measure_tree


def collect_path(
    ctx: ScanContext, path: Path, *, depth: int = 3, min_item_bytes: int = 1024**2
) -> CollectorResult:
    measured, skipped = measure_tree(
        path,
        collector="targeted",
        max_item_depth=depth,
        min_item_bytes=min_item_bytes,
        skip=ctx.privacy_skip(),
    )
    items = tuple(path_item(p, m, (HOME,)) for p, m in measured.items())
    return CollectorResult(items=items, skipped=skipped)
