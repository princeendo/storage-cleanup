"""Run every collector and assemble one scan."""

import os
import shutil
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path, PurePath

from storage_cleanup.scan.aggregate import category_totals, link_tree, location_totals
from storage_cleanup.scan.collectors.ai_tools import collect_ai_tools
from storage_cleanup.scan.collectors.apps import collect_apps
from storage_cleanup.scan.collectors.dev_caches import collect_dev_caches
from storage_cleanup.scan.collectors.docker import collect_docker
from storage_cleanup.scan.collectors.downloads import collect_downloads
from storage_cleanup.scan.collectors.home import collect_home
from storage_cleanup.scan.collectors.homebrew import collect_homebrew
from storage_cleanup.scan.collectors.packages import collect_packages
from storage_cleanup.scan.collectors.projects import collect_projects
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
from storage_cleanup.scan.protected import (
    GIT_REASON,
    containing_reason,
    home_store_paths,
    protected_reason,
)

DEFAULT_COLLECTORS: tuple[tuple[str, Collector], ...] = (
    ("home", collect_home),
    ("apps", collect_apps),
    ("downloads", collect_downloads),
    ("ai-tools", collect_ai_tools),
    ("homebrew", collect_homebrew),
    ("packages", collect_packages),
    ("docker", collect_docker),
    ("dev-caches", collect_dev_caches),
    ("projects", collect_projects),
)


def finalize_items(items: Sequence[Item], home: Path) -> tuple[Item, ...]:
    """Link the tree, then label each path item's location and protection.

    A folder that contains a protected store (Mail, a Photos library, and so
    on) is protected as a whole, so it can never be removed in one piece.
    """
    linked = link_tree(items)
    stores = _protected_stores(linked, home)
    finished: list[Item] = []
    for item in linked:
        if item.path is not None:
            path = Path(item.path)
            reason = protected_reason(path, home) or containing_reason(path, stores)
            item = replace(
                item, location=location_label(path, home), protected_reason=reason
            )
        finished.append(item)
    return tuple(finished)


def _protected_stores(items: Sequence[Item], home: Path) -> list[tuple[PurePath, str]]:
    """Protected stores that exist: fixed ones, Photos libraries, scanned ones."""
    stores = [(p, r) for p, r in home_store_paths(home) if os.path.lexists(p)]
    for library in sorted((home / "Pictures").glob("*.photoslibrary")):
        stores.append((library, "Photos library"))
    for item in items:
        if item.path is None:
            continue
        reason = protected_reason(Path(item.path), home)
        if reason is not None and reason != GIT_REASON:
            stores.append((PurePath(item.path), reason))
    return stores


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
