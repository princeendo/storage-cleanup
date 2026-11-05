import json
from dataclasses import replace
from pathlib import Path

import pytest

from storage_cleanup.cli import main
from storage_cleanup.inspect_cmd import (
    Query,
    format_rows,
    parse_size,
    run_query,
    summary,
)
from storage_cleanup.scan.model import AgeSignal, Item, Scan, scan_to_json
from tests.helpers import path_item
from tests.web.test_views import scan_of

GIB = 1024**3
MIB = 1024**2
DAY = 86_400.0
NOW = 1_800_000_000.0


def aged(item: Item, days: float) -> Item:
    return replace(
        item, age_signal=AgeSignal.LAST_MODIFIED, age_timestamp=NOW - days * DAY
    )


OLD_DMG = aged(
    path_item("/h/Downloads/old.dmg", 2 * GIB, ("downloads",), kind="file"), 400
)
NEW_DMG = aged(
    path_item("/h/Downloads/new.dmg", 3 * GIB, ("downloads",), kind="file"), 5
)
SMALL = aged(path_item("/h/Downloads/a.txt", MIB, ("downloads",), kind="file"), 900)
MAIL = aged(path_item("/h/Library/Mail", 5 * GIB), 1)


def scan() -> Scan:
    return scan_of([OLD_DMG, NEW_DMG, SMALL, MAIL], used=100 * GIB)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1234", 1234),
        ("500M", 500 * MIB),
        ("2G", 2 * GIB),
        ("10GiB", 10 * GIB),
        ("1.5g", int(1.5 * GIB)),
        ("100k", 100 * 1024),
    ],
)
def test_parse_size(text: str, expected: int) -> None:
    assert parse_size(text) == expected


def test_parse_size_rejects_nonsense() -> None:
    with pytest.raises(ValueError):
        parse_size("lots")


def test_query_filters_combine() -> None:
    found = run_query(
        scan(),
        Query(category="downloads", min_bytes=GIB, older_than_days=365),
        NOW,
    )
    assert [i.id for i in found] == [OLD_DMG.id]


def test_location_and_under_filters() -> None:
    assert {i.id for i in run_query(scan(), Query(location="Downloads"), NOW)} == {
        OLD_DMG.id,
        NEW_DMG.id,
        SMALL.id,
    }
    assert [
        i.id for i in run_query(scan(), Query(path_prefix="/h/Downloads/o"), NOW)
    ] == [OLD_DMG.id]


def test_protected_hidden_by_default() -> None:
    assert MAIL.id not in {i.id for i in run_query(scan(), Query(), NOW)}
    assert MAIL.id in {
        i.id for i in run_query(scan(), Query(include_protected=True), NOW)
    }


def test_sort_by_age() -> None:
    found = run_query(scan(), Query(sort="age"), NOW)
    assert [i.id for i in found] == [SMALL.id, OLD_DMG.id, NEW_DMG.id]


def test_output_is_bounded_and_says_how_many_more() -> None:
    items = [path_item(f"/h/f{i}", (i + 1) * MIB, kind="file") for i in range(500)]
    big = scan_of(items, used=100 * GIB)
    found = run_query(big, Query(limit=10_000), NOW)
    text = format_rows(found, NOW, limit=50)
    lines = text.splitlines()
    assert len(lines) == 52
    assert lines[0].startswith("id\tsize")
    assert lines[-1] == "… 450 more; narrow the query or raise --limit."
    assert "/h/f499" in lines[1]


def test_summary_mentions_totals() -> None:
    text = summary(scan())
    assert "Disk:" in text
    assert "Downloads" in text
    assert "Skipped paths: 0" in text


def write_scan(tmp_path: Path) -> Path:
    path = tmp_path / "reports" / "scans" / "20261105T221259Z.json"
    path.parent.mkdir(parents=True)
    path.write_text(scan_to_json(scan()))
    return path


def test_inspect_cli_table_and_json(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = write_scan(tmp_path)
    assert main(["inspect", "--scan", str(path), "--category", "downloads"]) == 0
    out = capsys.readouterr().out
    assert "/h/Downloads/new.dmg" in out
    assert main(["inspect", "--scan", str(path), "--min-size", "2G", "--json"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert {r["path"] for r in rows} == {"/h/Downloads/new.dmg", "/h/Downloads/old.dmg"}


def test_inspect_id_prints_details(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = write_scan(tmp_path)
    assert main(["inspect", "--scan", str(path), "--id", OLD_DMG.id]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["path"] == "/h/Downloads/old.dmg"
    assert data["location"] == "Downloads"


def test_inspect_unknown_id_exits_1(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = write_scan(tmp_path)
    assert main(["inspect", "--scan", str(path), "--id", "file:nope"]) == 1
    assert "No item with ID file:nope" in capsys.readouterr().err


def test_inspect_summary_cli(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = write_scan(tmp_path)
    assert main(["inspect", "--scan", str(path), "--summary"]) == 0
    assert "Disk:" in capsys.readouterr().out
