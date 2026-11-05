import zipfile
from pathlib import Path

import pytest

from storage_cleanup.scan.collectors.downloads import (
    collect_downloads,
    file_type,
    normalize_app_name,
)
from storage_cleanup.scan.model import AgeSignal
from tests.helpers import make_ctx, make_file


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("Slack-4.41.105-macOS.dmg", "slack"),
        ("Docker_arm64.dmg", "docker"),
        ("Visual Studio Code.app", "visualstudiocode"),
        ("googlechrome.dmg", "googlechrome"),
    ],
)
def test_normalize_app_name(filename: str, expected: str) -> None:
    assert normalize_app_name(filename) == expected


@pytest.mark.parametrize(
    ("name", "kind"),
    [
        ("a.dmg", "installer"),
        ("a.PKG", "installer"),
        ("a.tar.gz", "archive"),
        ("a.zip", "archive"),
        ("a.mov", "video"),
        ("a.mp3", "audio"),
        ("a.heic", "image"),
        ("a.pdf", "document"),
        ("a.xyz", "other"),
    ],
)
def test_file_type(tmp_path: Path, name: str, kind: str) -> None:
    assert file_type(make_file(tmp_path / name, 1)) == kind


def test_folder_type(tmp_path: Path) -> None:
    (tmp_path / "f").mkdir()
    assert file_type(tmp_path / "f") == "folder"


def make_app(home: Path, name: str) -> None:
    (home / "Applications" / f"{name}.app" / "Contents").mkdir(parents=True)


def test_installer_for_installed_app(tmp_path: Path) -> None:
    home = tmp_path / "h"
    make_app(home, "Slack")
    make_file(home / "Downloads" / "Slack-4.41.105-macOS.dmg", 5000, mtime=3000)
    [item] = collect_downloads(make_ctx(home)).items
    assert item.details == {"file_type": "installer", "installer_for": "Slack"}
    assert item.categories == ("downloads",)
    assert item.age_signal is AgeSignal.LAST_MODIFIED
    assert item.age_timestamp == 3000


def test_installer_for_uninstalled_app_has_no_match(tmp_path: Path) -> None:
    home = tmp_path / "h"
    make_file(home / "Downloads" / "Zoom.pkg", 10)
    [item] = collect_downloads(make_ctx(home)).items
    assert "installer_for" not in item.details


def test_zip_containing_installed_app(tmp_path: Path) -> None:
    home = tmp_path / "h"
    make_app(home, "Rectangle")
    (home / "Downloads").mkdir(parents=True)
    with zipfile.ZipFile(home / "Downloads" / "download.zip", "w") as z:
        z.writestr("Rectangle.app/Contents/Info.plist", "x")
    [item] = collect_downloads(make_ctx(home)).items
    assert item.details["installer_for"] == "Rectangle"


def test_corrupt_zip_does_not_crash(tmp_path: Path) -> None:
    home = tmp_path / "h"
    make_app(home, "Rectangle")
    make_file(home / "Downloads" / "broken.zip", 100)
    [item] = collect_downloads(make_ctx(home)).items
    assert item.details == {"file_type": "archive"}


def test_every_entry_is_an_item_with_type(tmp_path: Path) -> None:
    home = tmp_path / "h"
    make_file(home / "Downloads" / "a.pdf", 10)
    make_file(home / "Downloads" / "b.mov", 10)
    make_file(home / "Downloads" / "folder" / "c", 10)
    items = collect_downloads(make_ctx(home)).items
    assert {i.name: i.details["file_type"] for i in items} == {
        "a.pdf": "document",
        "b.mov": "video",
        "folder": "folder",
    }


def test_missing_downloads_is_empty(tmp_path: Path) -> None:
    assert collect_downloads(make_ctx(tmp_path / "h")).items == ()
