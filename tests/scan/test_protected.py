from pathlib import Path

import pytest

from storage_cleanup.scan.protected import protected_reason


@pytest.mark.parametrize(
    ("path", "reason"),
    [
        ("/System/Library/x", "system location"),
        ("/usr/bin/ls", "system location"),
        ("/bin", "system location"),
        ("/private/var/db/x", "system location"),
        ("/Library/Apple/x", "system location"),
        ("/h/Pictures/Photos Library.photoslibrary/db", "Photos library"),
        ("/h/Library/Mail/V10", "Mail"),
        ("/h/library/messages", "Messages"),
        ("/h/Library/Keychains/login.keychain-db", "Keychains"),
        ("/h/Library/Mobile Documents/com~apple~CloudDocs/a", "iCloud Drive"),
        ("/Volumes/B/Backups.backupdb/x", "Time Machine"),
        ("/Volumes/B/mac.sparsebundle", "Time Machine"),
        ("/h/workspace/repo/.git", "inside a .git folder"),
        ("/h/workspace/repo/.git/objects/ab", "inside a .git folder"),
    ],
)
def test_protected(path: str, reason: str) -> None:
    assert protected_reason(Path(path), Path("/h")) == reason


@pytest.mark.parametrize(
    "path",
    [
        "/usr/local/bin/x",
        "/h/workspace/repo",
        "/h/Downloads/a.dmg",
        "/h/Library/Caches/x",
        "/h/workspace/repo/.github/workflows",
        "/h/Library/Mailbox-notes",
    ],
)
def test_not_protected(path: str) -> None:
    assert protected_reason(Path(path), Path("/h")) is None
