import os
import plistlib
from datetime import UTC, datetime
from pathlib import Path

from storage_cleanup.scan.collectors.apps import collect_apps, find_app_bundles
from storage_cleanup.scan.model import AgeSignal, Item
from tests.helpers import FakeRunner, make_ctx, make_file, ok


def make_app(apps: Path, name: str, bundle_id: str, version: str = "1.0") -> Path:
    bundle = apps / f"{name}.app"
    plist = bundle / "Contents" / "Info.plist"
    plist.parent.mkdir(parents=True)
    with plist.open("wb") as f:
        plistlib.dump(
            {"CFBundleIdentifier": bundle_id, "CFBundleShortVersionString": version}, f
        )
    make_file(bundle / "Contents" / "MacOS" / name, 200_000)
    return bundle


def mdls(path: Path) -> tuple[str, ...]:
    return ("mdls", "-raw", "-name", "kMDItemLastUsedDate", str(path))


def by_kind(items: tuple[Item, ...], kind: str) -> list[Item]:
    return [i for i in items if i.kind == kind]


def test_find_app_bundles_reads_info_plist(tmp_path: Path) -> None:
    apps = tmp_path / "Applications"
    make_app(apps, "Slack", "com.tinyspeck.slackmacgap", "4.41")
    (apps / "NotAnApp").mkdir()
    bundles = find_app_bundles([apps, tmp_path / "missing"])
    assert [(b.name, b.bundle_id, b.version) for b in bundles] == [
        ("Slack", "com.tinyspeck.slackmacgap", "4.41")
    ]


def test_bundle_size_version_and_last_opened(tmp_path: Path) -> None:
    home = tmp_path / "h"
    app = make_app(home / "Applications", "Slack", "com.tinyspeck.slackmacgap")
    runner = FakeRunner({mdls(app): ok("2026-01-02 03:04:05 +0000")})
    result = collect_apps(make_ctx(home, runner))
    [item] = by_kind(result.items, "app")
    assert item.path == str(app)
    assert item.categories == ("applications",)
    assert item.size_bytes >= 200_000
    assert item.details["bundle_id"] == "com.tinyspeck.slackmacgap"
    assert item.details["version"] == "1.0"
    assert item.age_signal is AgeSignal.LAST_OPENED
    assert item.age_timestamp == datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC).timestamp()


def test_mdls_null_falls_back_to_mtime(tmp_path: Path) -> None:
    home = tmp_path / "h"
    app = make_app(home / "Applications", "Old", "com.example.old")
    os.utime(app, (1000, 1000))
    runner = FakeRunner({mdls(app): ok("(null)")})
    [item] = by_kind(collect_apps(make_ctx(home, runner)).items, "app")
    assert item.age_signal is AgeSignal.LAST_MODIFIED
    assert item.age_timestamp == 1000


def test_app_data_matched_by_bundle_id_and_name(tmp_path: Path) -> None:
    home = tmp_path / "h"
    make_app(home / "Applications", "Slack", "com.tinyspeck.slackmacgap")
    lib = home / "Library"
    make_file(lib / "Application Support" / "Slack" / "db", 300_000)
    make_file(lib / "Caches" / "com.tinyspeck.slackmacgap" / "c", 100_000)
    make_file(
        lib / "Saved Application State" / "com.tinyspeck.slackmacgap.savedState" / "s",
        10_000,
    )
    result = collect_apps(make_ctx(home))
    data = by_kind(result.items, "app-data")
    assert {Path(i.path or "").name for i in data} == {
        "Slack",
        "com.tinyspeck.slackmacgap",
        "com.tinyspeck.slackmacgap.savedState",
    }
    assert all(i.categories == ("app-data",) for i in data)
    assert all(
        i.details["owner_bundle_id"] == "com.tinyspeck.slackmacgap" for i in data
    )
    [app] = by_kind(result.items, "app")
    assert app.details["library_bytes"] == sum(i.size_bytes for i in data)


def test_leftover_data_for_uninstalled_app(tmp_path: Path) -> None:
    home = tmp_path / "h"
    caches = home / "Library" / "Caches"
    make_file(caches / "com.oldvendor.gone" / "c", 2 * 1024**2)
    make_file(caches / "com.apple.Safari" / "c", 2 * 1024**2)
    make_file(caches / "SomeFolder" / "c", 2 * 1024**2)
    [item] = by_kind(collect_apps(make_ctx(home)).items, "app-data")
    assert item.name == "com.oldvendor.gone"
    assert item.categories == ("leftover-app-data",)
    assert item.details["guessed_owner"] == "com.oldvendor.gone"


def test_small_leftovers_ignored(tmp_path: Path) -> None:
    home = tmp_path / "h"
    make_file(home / "Library" / "Caches" / "com.oldvendor.tiny" / "c", 1000)
    assert by_kind(collect_apps(make_ctx(home)).items, "app-data") == []


def test_containers_respect_privacy_rule(tmp_path: Path) -> None:
    home = tmp_path / "h"
    make_app(home / "Applications", "Notes", "com.example.notes")
    make_file(home / "Library" / "Containers" / "com.example.notes" / "d", 100_000)
    assert by_kind(collect_apps(make_ctx(home)).items, "app-data") == []
    opted = collect_apps(make_ctx(home, include_app_data=True))
    assert len(by_kind(opted.items, "app-data")) == 1
