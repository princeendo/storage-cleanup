from storage_cleanup.actions.trash import EMPTY_TRASH_SCRIPT, empty_trash
from storage_cleanup.scan.context import CommandResult
from tests.helpers import FakeRunner, ok

KEY = ("osascript", "-e", EMPTY_TRASH_SCRIPT)


def test_success() -> None:
    runner = FakeRunner({KEY: ok("")})
    assert empty_trash(runner) == (True, "The Trash is empty.")
    assert runner.calls == [KEY]


def test_automation_denied_explains_what_to_do() -> None:
    denied = CommandResult(
        1, "", "execution error: Not authorized to send Apple events to Finder. (-1743)"
    )
    done, message = empty_trash(FakeRunner({KEY: denied}))
    assert not done
    assert "Privacy & Security → Automation" in message
    assert "empty the Trash in Finder" in message


def test_other_errors_are_reported() -> None:
    done, message = empty_trash(FakeRunner({KEY: CommandResult(1, "", "boom")}))
    assert (done, message) == (False, "boom")
