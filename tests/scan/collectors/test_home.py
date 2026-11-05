from pathlib import Path

import pytest

from storage_cleanup.scan.collectors import home as home_mod
from storage_cleanup.scan.collectors.home import collect_home
from storage_cleanup.scan.context import PRIVACY_SKIP_REASON
from storage_cleanup.scan.model import AgeSignal
from tests.helpers import make_ctx, make_file


def test_every_top_level_entry_is_an_item(tmp_path: Path) -> None:
    home = tmp_path / "h"
    make_file(home / "Downloads" / "a", 1000, mtime=5000)
    make_file(home / ".cache" / "b", 1000)
    make_file(home / "notes.txt", 10)
    result = collect_home(make_ctx(home))
    kinds = {Path(i.path or "").name: i.kind for i in result.items}
    assert kinds == {"Downloads": "folder", ".cache": "folder", "notes.txt": "file"}
    downloads = next(i for i in result.items if i.name == "Downloads")
    assert downloads.categories == ("home",)
    assert downloads.age_signal is AgeSignal.LAST_MODIFIED
    assert downloads.age_timestamp == 5000
    assert downloads.file_count == 1


def test_deep_items_only_above_threshold(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(home_mod, "HOME_MIN_ITEM_BYTES", 1_000_000)
    home = tmp_path / "h"
    make_file(home / "Movies" / "big", 2_000_000)
    make_file(home / "Movies" / "small", 10)
    paths = {i.path for i in collect_home(make_ctx(home)).items}
    assert str(home / "Movies" / "big") in paths
    assert str(home / "Movies" / "small") not in paths


def test_containers_skipped_unless_opted_in(tmp_path: Path) -> None:
    home = tmp_path / "h"
    make_file(home / "Library" / "Containers" / "com.x" / "data", 100_000)
    default = collect_home(make_ctx(home))
    assert [s.reason for s in default.skipped] == [PRIVACY_SKIP_REASON]
    library = next(i for i in default.items if i.name == "Library")
    assert library.file_count == 0

    opted = collect_home(make_ctx(home, include_app_data=True))
    library = next(i for i in opted.items if i.name == "Library")
    assert library.file_count == 1
