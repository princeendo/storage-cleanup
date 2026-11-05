import json
from pathlib import Path
from typing import Any

import pytest

from storage_cleanup.cli import main
from storage_cleanup.findings import (
    findings_path_for,
    parse_findings,
    validate_findings,
)
from storage_cleanup.scan.model import Scan, scan_to_json
from tests.helpers import path_item
from tests.web.test_views import scan_of

GIB = 1024**3
SCAN_NAME = "20261105T221259Z.json"
MOVIES = path_item("/h/Movies", 2 * GIB)
OLD = path_item("/h/Downloads/old.dmg", GIB, ("downloads",), kind="file")
MAIL = path_item("/h/Library/Mail", GIB)


def scan() -> Scan:
    return scan_of([MOVIES, OLD, MAIL], used=100 * GIB)


def doc(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "schema_version": 1,
        "scan": SCAN_NAME,
        "created_at": "2026-11-05T23:00:00Z",
        "agent": "Claude Code (claude-opus-5-5)",
        "headlines": [
            {
                "title": "Old installers",
                "body": "1 installer, 1 GiB.",
                "item_ids": [OLD.id],
            }
        ],
        "categories": [
            {
                "id": "media",
                "name": "Media",
                "description": "Videos.",
                "item_ids": [MOVIES.id],
            }
        ],
        "recommendations": [
            {
                "item_id": OLD.id,
                "action": "delete",
                "confidence": 0.9,
                "reason": "Installer already used.",
            }
        ],
    }
    data.update(overrides)
    return data


def problems(data: dict[str, Any]) -> list[str]:
    return validate_findings(parse_findings(json.dumps(data)), scan(), SCAN_NAME)


def test_round_trip_parse() -> None:
    findings = parse_findings(json.dumps(doc()))
    assert findings.agent == "Claude Code (claude-opus-5-5)"
    assert findings.headlines[0].item_ids == (OLD.id,)
    assert findings.categories[0].name == "Media"
    [rec] = findings.recommendations
    assert (rec.item_id, rec.action, rec.confidence, rec.source) == (
        OLD.id,
        "delete",
        0.9,
        "agent",
    )


@pytest.mark.parametrize("missing", ["recommendations", "headlines", "scan"])
def test_parse_reports_missing_field(missing: str) -> None:
    data = doc()
    del data[missing]
    with pytest.raises(ValueError, match=missing):
        parse_findings(json.dumps(data))


@pytest.mark.parametrize("text", ["not json", "[]", "null"])
def test_parse_rejects_non_objects(text: str) -> None:
    with pytest.raises(ValueError):
        parse_findings(text)


def test_valid_findings_have_no_problems() -> None:
    assert problems(doc()) == []


def rec(**overrides: Any) -> dict[str, Any]:
    base = {"item_id": OLD.id, "action": "delete", "confidence": 0.9, "reason": "x"}
    base.update(overrides)
    return base


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"schema_version": 2}, "schema_version"),
        ({"scan": "other.json"}, "These findings are for scan other.json"),
        ({"recommendations": [rec(item_id="file:nope")]}, "Unknown item ID: file:nope"),
        (
            {"headlines": [{"title": "t", "body": "b", "item_ids": ["x:1"]}]},
            "Unknown item ID: x:1",
        ),
        ({"recommendations": [rec(item_id=MAIL.id)]}, "is protected (Mail)"),
        ({"recommendations": [rec(action="shred")]}, "action"),
        ({"recommendations": [rec(confidence=1.5)]}, "confidence"),
        ({"recommendations": [rec(confidence="high")]}, "confidence"),
        ({"recommendations": [rec(reason="  ")]}, "reason"),
        ({"recommendations": [rec(reason="x" * 201)]}, "reason"),
        ({"headlines": [{"title": "t" * 81, "body": "b", "item_ids": []}]}, "title"),
        ({"headlines": [{"title": "t", "body": "b" * 301, "item_ids": []}]}, "body"),
        (
            {
                "headlines": [
                    {"title": f"t{i}", "body": "b", "item_ids": []} for i in range(9)
                ]
            },
            "at most 8 headlines",
        ),
        (
            {
                "categories": [
                    {"id": "Bad ID", "name": "n", "description": "", "item_ids": []}
                ]
            },
            "category ID",
        ),
        (
            {
                "categories": [
                    {"id": "downloads", "name": "n", "description": "", "item_ids": []}
                ]
            },
            "built-in",
        ),
        ({"recommendations": [rec(), rec(reason="again")]}, "more than once"),
    ],
)
def test_each_rule_has_a_message(overrides: dict[str, Any], message: str) -> None:
    found = problems(doc(**overrides))
    assert any(message in p for p in found), found


def test_findings_path_for_scan(tmp_path: Path) -> None:
    scan_path = tmp_path / "reports" / "scans" / SCAN_NAME
    assert findings_path_for(scan_path) == tmp_path / "reports" / "findings" / SCAN_NAME


def write_reports(tmp_path: Path, findings: dict[str, Any]) -> tuple[Path, Path]:
    scan_path = tmp_path / "reports" / "scans" / SCAN_NAME
    scan_path.parent.mkdir(parents=True)
    scan_path.write_text(scan_to_json(scan()))
    findings_path = findings_path_for(scan_path)
    findings_path.parent.mkdir(parents=True)
    findings_path.write_text(json.dumps(findings))
    return scan_path, findings_path


def test_cli_findings_check_valid(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _, findings_path = write_reports(tmp_path, doc())
    assert main(["findings", "check", str(findings_path)]) == 0
    assert (
        "Findings are valid: 1 headlines, 1 recommendations." in capsys.readouterr().out
    )


def test_cli_findings_check_invalid(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bad = doc(recommendations=[rec(item_id=MAIL.id), rec(item_id="file:nope")])
    _, findings_path = write_reports(tmp_path, bad)
    assert main(["findings", "check", str(findings_path)]) == 1
    out = capsys.readouterr().out
    assert "protected" in out
    assert "Unknown item ID: file:nope" in out


def test_cli_findings_check_unreadable(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "broken.json"
    path.write_text("{oops")
    assert main(["findings", "check", str(path)]) == 1
    assert "Cannot read findings" in capsys.readouterr().out


def test_cli_findings_path(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    scan_path, findings_path = write_reports(tmp_path, doc())
    assert main(["findings", "path", "--output-dir", str(scan_path.parent)]) == 0
    assert capsys.readouterr().out.strip() == str(findings_path)
