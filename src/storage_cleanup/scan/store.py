"""Where scan files live, and how targeted scans are merged into them."""

import os
import tempfile
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from storage_cleanup.scan.aggregate import category_totals, location_totals
from storage_cleanup.scan.context import CollectorResult
from storage_cleanup.scan.model import Item, Scan, scan_to_json
from storage_cleanup.scan.run import finalize_items


def scans_dir(root: Path) -> Path:
    return root / "reports" / "scans"


def _file_name(scan: Scan) -> str:
    created = datetime.strptime(scan.created_at, "%Y-%m-%dT%H:%M:%SZ")
    return created.strftime("%Y%m%dT%H%M%SZ") + ".json"


def write_scan(scan: Scan, directory: Path, *, path: Path | None = None) -> Path:
    """Write ``scan`` atomically; by default the file is named by its time."""
    directory.mkdir(parents=True, exist_ok=True)
    target = path or directory / _file_name(scan)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".scan-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(scan_to_json(scan))
        os.replace(tmp, target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return target


def _scan_files(directory: Path) -> list[Path]:
    if not directory.is_dir():
        return []
    return sorted(p for p in directory.glob("*.json") if not p.name.startswith("."))


def latest_scan(directory: Path) -> Path | None:
    files = _scan_files(directory)
    return files[-1] if files else None


def previous_scan(directory: Path, current: Path) -> Path | None:
    earlier = [p for p in _scan_files(directory) if p.name < current.name]
    return earlier[-1] if earlier else None


def merge_items(base: Scan, addition: CollectorResult) -> Scan:
    """Replace items by ID, add new ones, and recompute the tree and totals."""
    by_id: dict[str, Item] = {i.id: i for i in base.items}
    for item in addition.items:
        if item.id in by_id:
            old = by_id[item.id]
            categories = tuple(dict.fromkeys((*old.categories, *item.categories)))
            item = replace(
                old,
                size_bytes=item.size_bytes,
                file_count=item.file_count,
                age_signal=item.age_signal,
                age_timestamp=item.age_timestamp,
                categories=categories,
            )
        by_id[item.id] = item
    # Path items' parents are derived from paths; re-derive them so new
    # intermediate folders slot into the tree. Virtual items keep theirs.
    relinked = [
        replace(i, parent_id=None) if i.path is not None else i for i in by_id.values()
    ]
    finished = finalize_items(relinked, Path(base.home))
    return replace(
        base,
        items=finished,
        skipped=(*base.skipped, *addition.skipped),
        totals_by_category=category_totals(finished),
        totals_by_location=location_totals(finished),
    )
