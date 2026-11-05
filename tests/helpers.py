"""Test helpers: synthetic files and a fake command runner."""

import os
from pathlib import Path


def make_file(path: Path, size: int, *, mtime: float | None = None) -> Path:
    """Write ``size`` non-zero bytes so blocks are really allocated."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path
