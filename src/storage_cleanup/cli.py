"""Command-line entry point."""

import argparse
from collections.abc import Sequence

from storage_cleanup import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="storage-cleanup",
        description="Find and safely reclaim disk space on macOS.",
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    build_parser().parse_args(argv)
    print("Nothing to do yet: no scanners are implemented.")
    return 0
