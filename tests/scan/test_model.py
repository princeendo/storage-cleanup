import pytest

from storage_cleanup.scan.categories import (
    AI_TOOLING,
    APP_DATA,
    APPLICATIONS,
    DEV_CACHES,
    DISPLAY_NAMES,
    DOCKER,
    DOWNLOADS,
    HOME,
    HOMEBREW,
    LEFTOVER_APP_DATA,
    PACKAGES,
    PROJECTS,
)
from storage_cleanup.scan.ids import item_id
from storage_cleanup.scan.model import (
    AgeSignal,
    DiskUsage,
    Item,
    Scan,
    SizeSource,
    Skipped,
    scan_from_json,
    scan_to_json,
)


def test_round_trip_preserves_everything() -> None:
    item = Item(
        id=item_id("file", "/h/a"),
        kind="file",
        name="naïve\nfile 🚀.dmg",
        path="/h/Downloads/naïve\nfile 🚀.dmg",
        size_bytes=4096,
        size_source=SizeSource.ALLOCATED,
        categories=(DOWNLOADS,),
        age_signal=AgeSignal.LAST_MODIFIED,
        age_timestamp=1.5,
        details={"installer_for": "Slack", "nested": {"a": [1, None]}},
    )
    scan = Scan(
        schema_version=1,
        created_at="2026-11-05T14:20:00Z",
        home="/h",
        disk=DiskUsage("/", 100, 60, 40),
        trash_bytes=None,
        local_snapshots=("com.apple.TimeMachine.2026-11-05-120000.local",),
        items=(item,),
        skipped=(Skipped("/h/Library/Mail", "no access", "home"),),
        totals_by_category={DOWNLOADS: 4096},
        totals_by_location={"Downloads": 4096},
    )
    assert scan_from_json(scan_to_json(scan)) == scan


def test_rejects_unknown_schema_version() -> None:
    with pytest.raises(ValueError, match="schema_version"):
        scan_from_json('{"schema_version": 2}')


def test_item_id_is_stable_and_kind_prefixed() -> None:
    assert item_id("folder", "/h/x") == item_id("folder", "/h/x")
    assert item_id("folder", "/h/x").startswith("folder:")
    assert item_id("folder", "/h/x") != item_id("folder", "/h/y")


def test_every_category_has_a_display_name() -> None:
    for cat in (
        AI_TOOLING,
        DOCKER,
        APPLICATIONS,
        APP_DATA,
        LEFTOVER_APP_DATA,
        HOMEBREW,
        PACKAGES,
        DEV_CACHES,
        PROJECTS,
        DOWNLOADS,
        HOME,
    ):
        assert DISPLAY_NAMES[cat]


def test_non_utf8_names_round_trip_and_encode() -> None:
    item = Item(
        id=item_id("file", "/h/bad\udcff"),
        kind="file",
        name="bad\udcff",
        path="/h/bad\udcff",
        size_bytes=1,
        size_source=SizeSource.ALLOCATED,
        categories=(DOWNLOADS,),
    )
    scan = Scan(
        schema_version=1,
        created_at="2026-11-05T14:20:00Z",
        home="/h",
        disk=DiskUsage("/", 1, 1, 0),
        trash_bytes=0,
        local_snapshots=(),
        items=(item,),
        skipped=(),
        totals_by_category={},
        totals_by_location={},
    )
    text = scan_to_json(scan)
    text.encode("utf-8")
    assert scan_from_json(text) == scan
