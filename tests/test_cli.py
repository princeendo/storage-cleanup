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


def test_cli_serve_findings_flag_missing_file_exits_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from storage_cleanup.scan.model import scan_to_json
    from tests.web.test_views import scan_of

    scan_path = tmp_path / "scan.json"
    scan_path.write_text(scan_to_json(scan_of([])))
    missing = tmp_path / "nope.json"
    code = main(
        ["serve", "--no-open", "--scan", str(scan_path), "--findings", str(missing)]
    )
    assert code == 2
    assert f"Cannot read findings {missing}" in capsys.readouterr().err


def _archive_config(tmp_path: Path) -> Path:
    path = tmp_path / "archive.toml"
    path.write_text(
        'hosts = ["nas.example"]\nuser = "u"\nkey = "/keys/k"\n'
        'expected_hostname = "nas"\nmount_point = "/mnt/d"\n'
        'fs_uuid = "uuid-1"\nallowed_root = "/mnt/d/a"\n'
        'location = "/mnt/d/a/x"\nowner = "u"\ngroup = "g"\n',
        encoding="utf-8",
    )
    return path


def test_archive_check_prints_host_and_free_space(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import shlex

    from storage_cleanup.scan.context import CommandResult

    answers = {
        "hostname": "nas\n",
        shlex.join(
            ["sudo", "-n", "findmnt", "-no", "UUID", "--mountpoint", "/mnt/d"]
        ): "uuid-1\n",
        shlex.join(
            ["sudo", "-n", "df", "-B1", "--output=avail", "--", "/mnt/d"]
        ): "Avail\n2147483648\n",
    }

    def fake(args: Any, timeout: float) -> CommandResult:
        out = answers.get(args[-1])
        return CommandResult(0, out, "") if out else CommandResult(127, "", "?")

    monkeypatch.setattr(cli, "ssh_runner", fake)
    assert main(["archive", "check", "--config", str(_archive_config(tmp_path))]) == 0
    out = capsys.readouterr().out
    assert "nas.example" in out
    assert "2.0 GiB" in out


def test_archive_check_without_config_says_how_to_make_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["archive", "check", "--config", str(tmp_path / "none.toml")]) == 2
    err = capsys.readouterr().err
    assert "archive.example.toml" in err
    assert "local/archive.toml" in err


def test_archive_check_reports_remote_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from storage_cleanup.scan.context import CommandResult

    monkeypatch.setattr(
        cli,
        "ssh_runner",
        lambda args, timeout: CommandResult(255, "", "Permission denied"),
    )
    assert main(["archive", "check", "--config", str(_archive_config(tmp_path))]) == 2
    assert "refused the SSH key" in capsys.readouterr().err
