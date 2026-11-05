"""Run every collector and assemble one scan."""

import shutil
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from storage_cleanup.scan.aggregate import category_totals, link_tree, location_totals
from storage_cleanup.scan.collectors.apps import collect_apps
from storage_cleanup.scan.collectors.home import collect_home
from storage_cleanup.scan.context import Collector, ScanContext
from storage_cleanup.scan.locations import location_label
from storage_cleanup.scan.measure import measure
from storage_cleanup.scan.model import (
    SCHEMA_VERSION,
    DiskUsage,
    Item,
    Scan,
    Skipped,
)
from storage_cleanup.scan.protected import protected_reason

DEFAULT_COLLECTORS: tuple[tuple[str, Collector], ...] = (
    ("home", collect_home),
    ("apps", collect_apps),
)


def finalize_items(items: Sequence[Item], home: Path) -> tuple[Item, ...]:
    """Link the tree, then label each path item's location and protection."""
    finished: list[Item] = []
    for item in link_tree(items):
        if item.path is not None:
            path = Path(item.path)
            item = replace(
                item,
                location=location_label(path, home),
                protected_reason=protected_reason(path, home),
            )
        finished.append(item)
    return tuple(finished)


def _trash(ctx: ScanContext) -> tuple[int | None, tuple[Skipped, ...]]:
    trash = ctx.home / ".Trash"
    if not trash.exists():
        return 0, ()
    m, skipped = measure(trash, collector="trash")
    if any(s.path == str(trash) for s in skipped):
        return None, (Skipped(str(trash), "no access", "trash"),)
    return m.size_bytes, ()


def _snapshots(ctx: ScanContext) -> tuple[tuple[str, ...], tuple[Skipped, ...]]:
    result = ctx.run(["tmutil", "listlocalsnapshots", "/"])
    if result.returncode != 0:
        reason = f"tmutil failed: {result.stderr.strip() or result.returncode}"
        return (), (Skipped("", reason, "snapshots"),)
    names = tuple(
        line.strip()
        for line in result.stdout.splitlines()
        if line.strip().startswith("com.apple.")
    )
    return names, ()


def run_scan(
    ctx: ScanContext,
    collectors: Sequence[tuple[str, Collector]] = DEFAULT_COLLECTORS,
) -> Scan:
    """Run each collector, tolerate failures, and assemble the scan."""
    items: list[Item] = []
    skipped: list[Skipped] = []
    for name, collect in collectors:
        try:
            result = collect(ctx)
        except Exception as exc:
            reason = f"collector failed: {type(exc).__name__}: {exc}"
            skipped.append(Skipped("", reason, name))
            continue
        items.extend(result.items)
        skipped.extend(result.skipped)

    finished = finalize_items(items, ctx.home)
    usage = shutil.disk_usage(ctx.home)
    trash_bytes, trash_skipped = _trash(ctx)
    snapshots, snapshot_skipped = _snapshots(ctx)
    return Scan(
        schema_version=SCHEMA_VERSION,
        created_at=datetime.fromtimestamp(ctx.now, UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        home=str(ctx.home),
        disk=DiskUsage("/", usage.total, usage.used, usage.free),
        trash_bytes=trash_bytes,
        local_snapshots=snapshots,
        items=finished,
        skipped=(*skipped, *trash_skipped, *snapshot_skipped),
        totals_by_category=category_totals(finished),
        totals_by_location=location_totals(finished),
    )
