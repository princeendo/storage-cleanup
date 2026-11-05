"""Location labels: the "where is it" lens."""

import os
from pathlib import PurePath
from typing import Final

DEFAULT_LOCATION_LABELS: Final[tuple[tuple[str, str], ...]] = (
    ("Downloads", "Downloads"),
    ("workspace/personal", "Your personal data"),
    ("workspace", "Workspace"),
    ("Library", "Library"),
    ("Documents", "Documents"),
    ("Desktop", "Desktop"),
)

_HOMEBREW_ROOTS: Final = (
    PurePath("/opt/homebrew"),
    PurePath("/usr/local/Homebrew"),
    PurePath("/usr/local/Cellar"),
    PurePath("/usr/local/Caskroom"),
)


def location_label(path: PurePath, home: PurePath) -> str | None:
    """Return the location label for ``path``, or ``None`` if it has none."""
    path = PurePath(os.path.abspath(path))
    if path.is_relative_to(home):
        rel = path.relative_to(home)
        if not rel.parts:
            return None
        best: tuple[int, str] | None = None
        for prefix, label in DEFAULT_LOCATION_LABELS:
            prefix_parts = PurePath(prefix).parts
            if rel.parts[: len(prefix_parts)] == prefix_parts and (
                best is None or len(prefix_parts) > best[0]
            ):
                best = (len(prefix_parts), label)
        return best[1] if best else rel.parts[0]
    if path.is_relative_to("/Applications"):
        return "Applications"
    if any(path.is_relative_to(root) for root in _HOMEBREW_ROOTS):
        return "Homebrew"
    return None
