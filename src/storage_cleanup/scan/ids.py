"""Stable identifiers for scanned items."""

import hashlib


def item_id(kind: str, key: str) -> str:
    """Return a stable ID for an item of ``kind`` identified by ``key``."""
    digest = hashlib.sha256(key.encode("utf-8", "surrogateescape")).hexdigest()
    return f"{kind}:{digest[:16]}"
