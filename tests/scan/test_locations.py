from pathlib import Path

import pytest

from storage_cleanup.scan.locations import location_label


@pytest.mark.parametrize(
    ("path", "label"),
    [
        ("/h/Downloads/a.dmg", "Downloads"),
        ("/h/workspace/personal/x", "Your personal data"),
        ("/h/workspace/edo/x", "Workspace"),
        ("/h/Movies/a.mov", "Movies"),
        ("/h/.cache/uv", ".cache"),
        ("/Applications/Slack.app", "Applications"),
        ("/opt/homebrew/Cellar/jq", "Homebrew"),
        ("/usr/local/Cellar/jq", "Homebrew"),
        ("/var/tmp/x", None),
        ("/h", None),
    ],
)
def test_location_label(path: str, label: str | None) -> None:
    assert location_label(Path(path), Path("/h")) == label
