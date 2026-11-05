"""Paths the tool must never remove, whatever a rule, finding, or user says."""

import os
from pathlib import PurePath

GIT_REASON = "inside a .git folder"

_SYSTEM_PREFIXES: tuple[tuple[str, ...], ...] = (
    ("/", "system"),
    ("/", "bin"),
    ("/", "sbin"),
    ("/", "usr"),
    ("/", "private", "var", "db"),
    ("/", "library", "apple"),
)
_SYSTEM_EXCEPTIONS: tuple[tuple[str, ...], ...] = (("/", "usr", "local"),)

_HOME_STORES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("library", "mail"), "Mail"),
    (("library", "messages"), "Messages"),
    (("library", "keychains"), "Keychains"),
    (("library", "mobile documents"), "iCloud Drive"),
)


_HOME_STORE_NAMES: dict[tuple[str, ...], tuple[str, ...]] = {
    ("library", "mail"): ("Library", "Mail"),
    ("library", "messages"): ("Library", "Messages"),
    ("library", "keychains"): ("Library", "Keychains"),
    ("library", "mobile documents"): ("Library", "Mobile Documents"),
}


def _parts(path: PurePath) -> tuple[str, ...]:
    return tuple(p.lower() for p in PurePath(os.path.abspath(path)).parts)


def _starts_with(parts: tuple[str, ...], prefix: tuple[str, ...]) -> bool:
    return parts[: len(prefix)] == prefix


def protected_reason(path: PurePath, home: PurePath) -> str | None:
    """Return why ``path`` is protected, or ``None`` if it is not."""
    parts = _parts(path)
    if any(_starts_with(parts, p) for p in _SYSTEM_PREFIXES) and not any(
        _starts_with(parts, p) for p in _SYSTEM_EXCEPTIONS
    ):
        return "system location"
    if any(p.endswith(".photoslibrary") for p in parts):
        return "Photos library"
    home_parts = _parts(home)
    if _starts_with(parts, home_parts):
        rest = parts[len(home_parts) :]
        for store, reason in _HOME_STORES:
            if _starts_with(rest, store):
                return reason
    if any(
        p.endswith((".backupbundle", ".sparsebundle")) or p == "backups.backupdb"
        for p in parts
    ) or _starts_with(parts, ("/", "volumes", ".timemachine")):
        return "Time Machine"
    if ".git" in parts:
        return GIT_REASON
    return None


def home_store_paths(home: PurePath) -> list[tuple[PurePath, str]]:
    """The fixed protected stores under ``home``, with their reasons."""
    base = PurePath(os.path.abspath(home))
    stores: list[tuple[PurePath, str]] = []
    for store, reason in _HOME_STORES:
        stores.append((base.joinpath(*_HOME_STORE_NAMES[store]), reason))
    return stores


def containing_reason(path: PurePath, stores: list[tuple[PurePath, str]]) -> str | None:
    """Return ``"contains <store>"`` when a protected store sits inside ``path``.

    A ``.git`` folder does not count: a whole repository may be archived or
    trashed as one unit (spec section 9, Colin's decision).
    """
    parts = _parts(path)
    for store, reason in stores:
        store_parts = _parts(store)
        if len(store_parts) > len(parts) and store_parts[: len(parts)] == parts:
            return f"contains {reason}"
    return None
