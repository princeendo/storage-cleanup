"""Empty the Trash through Finder (reading ~/.Trash needs Full Disk Access)."""

from typing import Final

from storage_cleanup.scan.context import Runner

EMPTY_TRASH_SCRIPT: Final = 'tell application "Finder" to empty trash'
AUTOMATION_DENIED: Final = "-1743"
AUTOMATION_HINT: Final = (
    "macOS blocked this. Allow your terminal to control Finder in System "
    "Settings → Privacy & Security → Automation, or empty the Trash in Finder."
)


def empty_trash(runner: Runner) -> tuple[bool, str]:
    """Ask Finder to empty the Trash. The tool never changes the permission itself."""
    result = runner(["osascript", "-e", EMPTY_TRASH_SCRIPT])
    if result.returncode == 0:
        return True, "The Trash is empty."
    if AUTOMATION_DENIED in result.stderr:
        return False, AUTOMATION_HINT
    return False, result.stderr.strip() or f"osascript exited {result.returncode}"
