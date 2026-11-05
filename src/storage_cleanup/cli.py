"""Command-line entry point."""

import argparse
import sys
import time
from collections.abc import Sequence
from pathlib import Path

from storage_cleanup import __version__
from storage_cleanup.scan.categories import DISPLAY_NAMES
from storage_cleanup.scan.collectors.targeted import collect_path
from storage_cleanup.scan.context import ScanContext, subprocess_runner
from storage_cleanup.scan.model import Scan, scan_from_json
from storage_cleanup.scan.run import run_scan
from storage_cleanup.scan.store import latest_scan, merge_items, write_scan
from storage_cleanup.web.server import load_scan, serve

GIB = 1024**3


def default_context(
    *, include_app_data: bool = False, workspace_roots: Sequence[Path] = ()
) -> ScanContext:
    """Build the context for scanning this Mac as the current user."""
    home = Path.home()
    brew_prefix: Path | None = None
    for candidate in (Path("/opt/homebrew"), Path("/usr/local")):
        if (candidate / "bin" / "brew").exists():
            brew_prefix = candidate
            break
    return ScanContext(
        home=home,
        app_dirs=(Path("/Applications"), home / "Applications"),
        workspace_roots=tuple(workspace_roots) or (home / "workspace",),
        brew_prefix=brew_prefix,
        run=subprocess_runner,
        now=time.time(),
        include_app_data=include_app_data,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="storage-cleanup",
        description="Find and safely reclaim disk space on macOS.",
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    commands = parser.add_subparsers(dest="command")
    scan = commands.add_parser("scan", help="Measure where disk space is (read-only).")
    scan.add_argument(
        "--output-dir",
        type=Path,
        default=Path("reports/scans"),
        help="Where scan files go (default: reports/scans).",
    )
    scan.add_argument(
        "--include-app-data",
        action="store_true",
        help="Also measure other apps' sandboxed data (may show privacy prompts).",
    )
    scan.add_argument(
        "--workspace-root",
        type=Path,
        action="append",
        default=[],
        help="Folder to search for git projects (default: ~/workspace).",
    )
    scan.add_argument(
        "--path",
        type=Path,
        help="Measure one path in detail and add it to an existing scan.",
    )
    scan.add_argument(
        "--into", type=Path, help="Scan file to add to (default: the latest)."
    )
    serve = commands.add_parser(
        "serve", help="Show a scan in a local, read-only web page."
    )
    serve.add_argument("--scan", type=Path, help="Scan file (default: the latest).")
    serve.add_argument(
        "--output-dir",
        type=Path,
        default=Path("reports/scans"),
        help="Where to look for the latest scan (default: reports/scans).",
    )
    serve.add_argument(
        "--port", type=int, default=0, help="Port (default: any free port)."
    )
    serve.add_argument(
        "--no-open", action="store_true", help="Print the link; don't open a browser."
    )
    return parser


def _serve(args: argparse.Namespace) -> int:
    scan_path = args.scan or latest_scan(args.output_dir)
    if scan_path is None:
        print('No scan found. Run "storage-cleanup scan" first.', file=sys.stderr)
        return 2
    try:
        load_scan(scan_path)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 2
    serve(scan_path, port=args.port, open_browser=not args.no_open)
    return 0


def _gib(size: int) -> str:
    return f"{size / GIB:.1f} GiB"


def print_summary(scan: Scan, path: Path) -> None:
    disk = scan.disk
    percent_free = 100 * disk.free_bytes / disk.total_bytes if disk.total_bytes else 0
    print(f"Scan saved to {path}")
    print(
        f"Disk: {_gib(disk.used_bytes)} used, {_gib(disk.free_bytes)} free "
        f"({percent_free:.0f}% free)"
    )
    roots = sorted(
        (i for i in scan.items if i.parent_id is None),
        key=lambda i: i.size_bytes,
        reverse=True,
    )
    print("\nLargest items:")
    for item in roots[:10]:
        where = f"  [{item.location}]" if item.location else ""
        print(f"  {_gib(item.size_bytes):>10}  {item.name}{where}")
    print("\nBy category:")
    for cat, size in sorted(
        scan.totals_by_category.items(), key=lambda kv: kv[1], reverse=True
    ):
        print(f"  {_gib(size):>10}  {DISPLAY_NAMES.get(cat, cat)}")
    print(f"\nSkipped paths: {len(scan.skipped)} (see the scan file for reasons)")


def _scan(args: argparse.Namespace) -> int:
    ctx = default_context(
        include_app_data=args.include_app_data, workspace_roots=args.workspace_root
    )
    if args.path is not None:
        target = args.into or latest_scan(args.output_dir)
        if target is None or not target.exists():
            print('No scan found. Run "storage-cleanup scan" first.', file=sys.stderr)
            return 2
        base = scan_from_json(target.read_text(encoding="utf-8"))
        merged = merge_items(base, collect_path(ctx, args.path.resolve()))
        write_scan(merged, target.parent, path=target)
        added = len(merged.items) - len(base.items)
        print(f"Added {added} items under {args.path} to {target}")
        return 0
    scan = run_scan(ctx)
    path = write_scan(scan, args.output_dir)
    print_summary(scan, path)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "scan":
        return _scan(args)
    if args.command == "serve":
        return _serve(args)
    parser.print_help()
    return 0
