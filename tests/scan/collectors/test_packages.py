from pathlib import Path

from storage_cleanup.scan.collectors.packages import collect_packages
from storage_cleanup.scan.model import AgeSignal
from tests.helpers import make_ctx, make_file


def test_package_managers(tmp_path: Path) -> None:
    home = tmp_path / "h"
    prefix = tmp_path / "brew"
    make_file(home / ".local" / "pipx" / "venvs" / "httpie" / "bin" / "x", 1000)
    make_file(home / ".local" / "share" / "uv" / "tools" / "ruff" / "bin" / "x", 1000)
    make_file(prefix / "lib" / "node_modules" / "@scope" / "pkg" / "index.js", 1000)
    make_file(prefix / "lib" / "node_modules" / "typescript" / "a.js", 1000)
    make_file(prefix / "lib" / "node_modules" / "npm" / "a.js", 1000)
    make_file(prefix / "lib" / "node_modules" / "corepack" / "a.js", 1000)
    make_file(home / ".cargo" / "bin" / "ripgrep-tool", 1000, mtime=4000)

    items = collect_packages(make_ctx(home, brew_prefix=prefix)).items
    found = {(i.details["manager"], i.name) for i in items}
    assert found == {
        ("pipx", "httpie"),
        ("uv", "ruff"),
        ("npm", "@scope/pkg"),
        ("npm", "typescript"),
        ("cargo", "ripgrep-tool"),
    }
    assert all(i.kind == "package" and i.categories == ("packages",) for i in items)
    cargo = next(i for i in items if i.name == "ripgrep-tool")
    assert cargo.age_signal is AgeSignal.LAST_MODIFIED
    assert cargo.age_timestamp == 4000


def test_nothing_installed(tmp_path: Path) -> None:
    assert collect_packages(make_ctx(tmp_path / "h")).items == ()
