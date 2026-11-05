import pytest

from storage_cleanup import __version__
from storage_cleanup.cli import main


def test_version_flag(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_no_args_is_a_no_op() -> None:
    assert main([]) == 0
