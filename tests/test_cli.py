from pathlib import Path
from typing import Any

import pytest

from storage_cleanup import __version__, cli
from storage_cleanup.cli import main
from storage_cleanup.scan.context import ScanContext
from tests.helpers import make_ctx, make_file

MIB = 1024**2


def test_version_flag(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_no_subcommand_prints_help(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 0
    assert "scan" in capsys.readouterr().out


@pytest.fixture
def fake_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "h"
    make_file(home / "Downloads" / "Slack-4.1.dmg", 3 * MIB)
    make_file(home / "Movies" / "clip.mov", 2 * MIB)

    def fake_context(**kwargs: Any) -> ScanContext:
        return make_ctx(home, **kwargs)

    monkeypatch.setattr(cli, "default_context", fake_context)
    return home


def test_cli_scan_writes_file_and_summary(
    tmp_path: Path, fake_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "out"
    assert main(["scan", "--output-dir", str(out)]) == 0
    files = list(out.glob("*.json"))
    assert len(files) == 1
    text = capsys.readouterr().out
    assert str(files[0]) in text
    assert "free" in text
    assert "Downloads" in text
    assert "Skipped" in text


def test_cli_targeted_scan_adds_items(
    tmp_path: Path, fake_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "out"
    main(["scan", "--output-dir", str(out)])
    [scan_file] = list(out.glob("*.json"))
    before = scan_file.read_text()
    assert (
        main(["scan", "--path", str(fake_home / "Movies"), "--output-dir", str(out)])
        == 0
    )
    after = scan_file.read_text()
    assert "clip.mov" not in before
    assert "clip.mov" in after


def test_cli_targeted_without_scan_exits_2(
    tmp_path: Path, fake_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["scan", "--path", str(fake_home), "--output-dir", str(tmp_path / "x")])
    assert code == 2
    assert 'No scan found. Run "storage-cleanup scan" first.' in capsys.readouterr().err


def test_cli_serve_without_scan_exits_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["serve", "--no-open", "--output-dir", str(tmp_path / "none")])
    assert code == 2
    assert 'No scan found. Run "storage-cleanup scan" first.' in capsys.readouterr().err


def test_cli_serve_corrupt_scan_exits_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("{oops")
    assert main(["serve", "--no-open", "--scan", str(bad)]) == 2
    assert f"Cannot read scan {bad}" in capsys.readouterr().err
