"""Command-line entry point."""

import argparse
import json
import sys
import time
from collections.abc import Sequence
from pathlib import Path

from storage_cleanup import __version__, inspect_cmd
from storage_cleanup.actions.archive_config import DEFAULT_CONFIG_PATH, load_config
from storage_cleanup.actions.remote import (
    RemoteError,
    check_storage,
    connect,
    ssh_runner,
)
from storage_cleanup.findings import (
    findings_path_for,
    parse_findings,
    validate_findings,
)
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
    serve.add_argument(
        "--findings",
        type=Path,
        help="Agent findings file (default: the one named after the scan).",
    )
    serve.add_argument(
        "--archive-config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help="Homelab settings for Archive (default: local/archive.toml).",
    )
    inspect = commands.add_parser(
        "inspect", help="Ask focused questions about a scan (for agents)."
    )
    inspect.add_argument("--scan", type=Path, help="Scan file (default: the latest).")
    inspect.add_argument(
        "--output-dir",
        type=Path,
        default=Path("reports/scans"),
        help="Where to look for the latest scan (default: reports/scans).",
    )
    inspect.add_argument("--summary", action="store_true", help="Totals only.")
    inspect.add_argument("--id", help="Show one item with all its details.")
    inspect.add_argument("--category", help="Category ID, e.g. downloads.")
    inspect.add_argument("--location", help='Location label, e.g. "Downloads".')
    inspect.add_argument("--kind", help="Item kind, e.g. app or docker-image.")
    inspect.add_argument("--under", help="Only paths that start with this.")
    inspect.add_argument("--min-size", default="0", help="e.g. 500M or 2G.")
    inspect.add_argument(
        "--older-than", type=float, help="Only items unused for this many days."
    )
    inspect.add_argument("--include-protected", action="store_true")
    inspect.add_argument("--sort", choices=["size", "age"], default="size")
    inspect.add_argument("--limit", type=int, default=50)
    inspect.add_argument("--json", action="store_true", help="Print JSON rows.")

    findings = commands.add_parser(
        "findings", help="Check or locate an agent's findings file."
    )
    findings_commands = findings.add_subparsers(dest="findings_command", required=True)
    check = findings_commands.add_parser("check", help="Validate a findings file.")
    check.add_argument("file", type=Path)
    check.add_argument(
        "--scan", type=Path, help="Scan file (default: the scan the findings name)."
    )
    where = findings_commands.add_parser(
        "path", help="Print where findings for a scan belong."
    )
    where.add_argument("--scan", type=Path, help="Scan file (default: the latest).")
    where.add_argument(
        "--output-dir",
        type=Path,
        default=Path("reports/scans"),
        help="Where to look for the latest scan (default: reports/scans).",
    )

    archive = commands.add_parser(
        "archive", help="Copy items to the homelab (build step 5)."
    )
    archive_commands = archive.add_subparsers(dest="archive_command", required=True)
    archive_check = archive_commands.add_parser(
        "check", help="Test the homelab connection and disk (read-only)."
    )
    archive_check.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help=f"Homelab settings file (default: {DEFAULT_CONFIG_PATH}).",
    )
    return parser


def _inspect(args: argparse.Namespace) -> int:
    scan_path = args.scan or latest_scan(args.output_dir)
    if scan_path is None:
        print('No scan found. Run "storage-cleanup scan" first.', file=sys.stderr)
        return 2
    try:
        scan = scan_from_json(scan_path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"Cannot read scan {scan_path}: {exc}", file=sys.stderr)
        return 2
    if args.summary:
        print(inspect_cmd.summary(scan))
        return 0
    if args.id:
        item = next((i for i in scan.items if i.id == args.id), None)
        if item is None:
            print(f"No item with ID {args.id}", file=sys.stderr)
            return 1
        print(json.dumps(inspect_cmd.item_details(item), indent=1, ensure_ascii=False))
        return 0
    try:
        min_bytes = inspect_cmd.parse_size(args.min_size)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 2
    query = inspect_cmd.Query(
        category=args.category,
        location=args.location,
        kind=args.kind,
        path_prefix=args.under,
        min_bytes=min_bytes,
        older_than_days=args.older_than,
        include_protected=args.include_protected,
        limit=args.limit,
        sort=args.sort,
    )
    now = time.time()
    found = inspect_cmd.run_query(scan, query, now)
    if args.json:
        rows = [inspect_cmd.compact(i, now) for i in found[: args.limit]]
        print(json.dumps(rows, indent=1, ensure_ascii=False))
    else:
        print(inspect_cmd.format_rows(found, now, limit=args.limit))
    return 0


def _findings(args: argparse.Namespace) -> int:
    if args.findings_command == "path":
        scan_path = args.scan or latest_scan(args.output_dir)
        if scan_path is None:
            print('No scan found. Run "storage-cleanup scan" first.', file=sys.stderr)
            return 2
        print(findings_path_for(scan_path))
        return 0
    try:
        findings = parse_findings(args.file.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"Cannot read findings {args.file}: {exc}")
        return 1
    scan_path = args.scan or args.file.parent.parent / "scans" / findings.scan
    try:
        scan = scan_from_json(scan_path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"Cannot read the scan these findings name ({scan_path}): {exc}")
        return 1
    problems = validate_findings(findings, scan, scan_path.name)
    if problems:
        for problem in problems:
            print(problem)
        return 1
    print(
        f"Findings are valid: {len(findings.headlines)} headlines, "
        f"{len(findings.recommendations)} recommendations."
    )
    return 0


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
    if args.findings is not None and not args.findings.is_file():
        print(f"Cannot read findings {args.findings}: no such file.", file=sys.stderr)
        return 2
    serve(
        scan_path,
        port=args.port,
        open_browser=not args.no_open,
        findings_path=args.findings,
        archive_config_path=args.archive_config,
    )
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


def _archive(args: argparse.Namespace) -> int:
    if not args.config.is_file():
        print(
            f"No homelab settings at {args.config}. Copy archive.example.toml "
            "to local/archive.toml and fill it in.",
            file=sys.stderr,
        )
        return 2
    try:
        config = load_config(args.config)
        remote = connect(config, ssh_runner)
        free = check_storage(remote, 0)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 2
    except RemoteError as exc:
        print(exc.message, file=sys.stderr)
        return 2
    print(f"Connected to {remote.host} ({config.expected_hostname}).")
    print(f"Archive disk {config.mount_point}: {_gib(free)} free.")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "scan":
        return _scan(args)
    if args.command == "serve":
        return _serve(args)
    if args.command == "findings":
        return _findings(args)
    if args.command == "inspect":
        return _inspect(args)
    if args.command == "archive":
        return _archive(args)
    parser.print_help()
    return 0
