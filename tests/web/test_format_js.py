"""Run the pure functions of format.js under macOS JavaScriptCore."""

import json
import subprocess
from pathlib import Path

import pytest

JSC = "/System/Library/Frameworks/JavaScriptCore.framework/Versions/Current/Helpers/jsc"
FORMAT_JS = Path(__file__).parents[2] / "src/storage_cleanup/web/static/format.js"


def run_js(tmp_path: Path, expression: str) -> object:
    if not Path(JSC).exists():
        pytest.skip("jsc (JavaScriptCore) is not available")
    driver = tmp_path / "driver.mjs"
    driver.write_text(
        f'import * as f from "{FORMAT_JS}";\nprint(JSON.stringify({expression}));\n'
    )
    done = subprocess.run([JSC, "-m", str(driver)], capture_output=True, text=True)
    assert done.returncode == 0, done.stdout + done.stderr
    return json.loads(done.stdout.strip().splitlines()[-1])


@pytest.mark.parametrize(
    ("days", "text"),
    [(0.2, "today"), (1.5, "1 day ago"), (40, "40 days ago"), (None, "unknown")],
)
def test_format_ago(tmp_path: Path, days: float | None, text: str) -> None:
    assert run_js(tmp_path, f"f.formatAgo({json.dumps(days)})") == text


def test_describe_age_today(tmp_path: Path) -> None:
    assert run_js(tmp_path, 'f.describeAge("last-commit", 0.5)') == "Last commit today"


def test_no_page_appends_ago_to_format_age() -> None:
    static = FORMAT_JS.parent
    offenders = [
        p.name
        for p in static.glob("*.js")
        if "formatAge(" in p.read_text()
        and "} ago`" in p.read_text()
        and p.name != "format.js"
    ]
    assert offenders == []
