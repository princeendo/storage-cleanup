from pathlib import Path

from storage_cleanup.scan.collectors.dev_caches import KNOWN_CACHES, collect_dev_caches
from tests.helpers import make_ctx, make_file


def test_known_cache_reported_with_reasons(tmp_path: Path) -> None:
    home = tmp_path / "h"
    make_file(home / "Library" / "Developer" / "Xcode" / "DerivedData" / "x", 50_000)
    [item] = collect_dev_caches(make_ctx(home)).items
    assert item.kind == "cache"
    assert item.categories == ("dev-caches",)
    assert item.path == str(home / "Library" / "Developer" / "Xcode" / "DerivedData")
    assert item.size_bytes >= 50_000
    assert item.details["why_safe"]
    assert item.details["regenerates"]


def test_missing_caches_absent(tmp_path: Path) -> None:
    assert collect_dev_caches(make_ctx(tmp_path / "h")).items == ()


def test_every_known_cache_documents_safety() -> None:
    assert len(KNOWN_CACHES) >= 15
    for cache in KNOWN_CACHES:
        assert cache.why_safe and cache.regenerates and cache.name
    huggingface = next(c for c in KNOWN_CACHES if c.rel_path == ".cache/huggingface")
    assert "download" in huggingface.regenerates.lower()
