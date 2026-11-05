import json
from pathlib import Path

from storage_cleanup.scan.collectors.homebrew import collect_homebrew
from storage_cleanup.scan.context import CommandResult
from storage_cleanup.scan.model import AgeSignal
from tests.helpers import FakeRunner, make_ctx, make_file, ok

# Trimmed from real `brew info --json=v2 --installed` output (Homebrew, 2026-11).
BREW_JSON = {
    "formulae": [
        {
            "name": "jq",
            "installed": [
                {"version": "1.8.1", "time": 1776881395, "installed_on_request": True}
            ],
        },
        {
            "name": "oniguruma",
            "installed": [
                {"version": "6.9.10", "time": 1776881300, "installed_on_request": False}
            ],
        },
    ],
    "casks": [
        {
            "token": "affine",
            "installed": "0.20.5",
            "installed_time": 1742866287,
            "artifacts": [
                {"app": ["AFFiNE.app"], "target": "/Applications/AFFiNE.app"},
                {"zap": [{"trash": ["~/Library/Logs/AFFiNE"]}]},
            ],
        }
    ],
}


def brew_runner(
    prefix: Path, cache: Path, info: CommandResult | None = None
) -> FakeRunner:
    brew = str(prefix / "bin" / "brew")
    return FakeRunner(
        {
            (brew, "info", "--json=v2", "--installed"): info
            or ok(json.dumps(BREW_JSON)),
            (brew, "leaves"): ok("jq\n"),
            (brew, "--cache"): ok(f"{cache}\n"),
        }
    )


def setup_prefix(tmp_path: Path) -> tuple[Path, Path]:
    prefix = tmp_path / "brew"
    make_file(prefix / "bin" / "brew", 10)
    make_file(prefix / "Cellar" / "jq" / "1.8.1" / "bin" / "jq", 300_000)
    make_file(prefix / "Cellar" / "oniguruma" / "6.9.10" / "lib" / "x", 100_000)
    make_file(prefix / "Caskroom" / "affine" / "0.20.5" / "x", 50_000)
    cache = tmp_path / "Caches" / "Homebrew"
    make_file(cache / "downloads" / "a.tar.gz", 200_000)
    return prefix, cache


def test_formulae_and_casks_from_brew_json(tmp_path: Path) -> None:
    prefix, cache = setup_prefix(tmp_path)
    ctx = make_ctx(tmp_path / "h", brew_runner(prefix, cache), brew_prefix=prefix)
    items = {i.name: i for i in collect_homebrew(ctx).items}

    jq = items["jq"]
    assert jq.kind == "brew-formula"
    assert jq.path == str(prefix / "Cellar" / "jq")
    assert jq.size_bytes >= 300_000
    assert jq.categories == ("homebrew",)
    assert jq.age_signal is AgeSignal.INSTALLED
    assert jq.age_timestamp == 1776881395
    assert jq.details == {
        "version": "1.8.1",
        "installed_on_request": True,
        "leaf": True,
    }
    assert items["oniguruma"].details["leaf"] is False

    affine = items["affine"]
    assert affine.kind == "brew-cask"
    assert affine.path == str(prefix / "Caskroom" / "affine")
    assert affine.age_timestamp == 1742866287
    assert affine.details == {"version": "0.20.5", "apps": ["AFFiNE.app"]}

    [cache_item] = [i for i in items.values() if i.kind == "cache"]
    assert cache_item.path == str(cache)
    assert cache_item.categories == ("homebrew", "dev-caches")
    assert "regenerates" in cache_item.details


def test_brew_absent_is_skipped(tmp_path: Path) -> None:
    result = collect_homebrew(make_ctx(tmp_path / "h"))
    assert result.items == ()
    assert [s.reason for s in result.skipped] == ["Homebrew not installed"]


def test_brew_info_failure_still_reports_cache(tmp_path: Path) -> None:
    prefix, cache = setup_prefix(tmp_path)
    runner = brew_runner(prefix, cache, info=CommandResult(1, "", "boom"))
    result = collect_homebrew(make_ctx(tmp_path / "h", runner, brew_prefix=prefix))
    assert [i.kind for i in result.items] == ["cache"]
    assert any("brew info" in s.reason for s in result.skipped)
