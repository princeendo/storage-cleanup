from pathlib import Path

import pytest

from storage_cleanup.actions.archive_config import (
    ArchiveConfig,
    check_location,
    load_config,
)

VALID = {
    "hosts": '["nas.example", "192.0.2.10"]',
    "user": '"archiver"',
    "key": '"~/.ssh/archive_key"',
    "expected_hostname": '"nas"',
    "mount_point": '"/mnt/HD/Disk1"',
    "fs_uuid": '"00000000-0000-0000-0000-000000000000"',
    "allowed_root": '"/mnt/HD/Disk1/mac-archive"',
    "location": '"/mnt/HD/Disk1/mac-archive/laptop"',
    "owner": '"archiver"',
    "group": '"users"',
}


def write(tmp_path: Path, **overrides: str | None) -> Path:
    fields = {**VALID, **overrides}
    text = "\n".join(f"{k} = {v}" for k, v in fields.items() if v is not None)
    path = tmp_path / "archive.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_valid_file_loads(tmp_path: Path) -> None:
    config = load_config(write(tmp_path))
    assert isinstance(config, ArchiveConfig)
    assert config.hosts == ("nas.example", "192.0.2.10")
    assert config.key == Path.home() / ".ssh" / "archive_key"
    assert config.location == "/mnt/HD/Disk1/mac-archive/laptop"
    assert config.group == "users"


@pytest.mark.parametrize("field", list(VALID))
def test_each_missing_field_is_named(tmp_path: Path, field: str) -> None:
    with pytest.raises(ValueError, match=field):
        load_config(write(tmp_path, **{field: None}))


def test_missing_file_and_bad_toml(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Cannot read"):
        load_config(tmp_path / "nope.toml")
    bad = tmp_path / "bad.toml"
    bad.write_text("hosts = [", encoding="utf-8")
    with pytest.raises(ValueError, match="not valid TOML"):
        load_config(bad)


@pytest.mark.parametrize("hosts", ["[]", '"nas"', "[1]"])
def test_hosts_must_be_a_list_of_strings(tmp_path: Path, hosts: str) -> None:
    with pytest.raises(ValueError, match="hosts"):
        load_config(write(tmp_path, hosts=hosts))


def test_location_outside_allowed_root_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="location"):
        load_config(write(tmp_path, location='"/mnt/HD/Disk1/elsewhere"'))


def test_allowed_root_outside_mount_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="allowed_root"):
        load_config(
            write(
                tmp_path,
                allowed_root='"/srv/other"',
                location='"/srv/other/laptop"',
            )
        )


@pytest.mark.parametrize(
    "bad",
    [
        "/mnt/HD/Disk1/mac-archive/../x",
        "/mnt/HD/Disk1/mac-archive/./x",
        "/mnt/HD/Disk1/mac-archive//x",
        "mac-archive/x",
        "/mnt/HD/Disk1/mac-archive/x/",
    ],
)
def test_unnormalized_location_is_refused(tmp_path: Path, bad: str) -> None:
    with pytest.raises(ValueError, match="location"):
        load_config(write(tmp_path, location=f'"{bad}"'))


def test_sibling_prefix_is_not_under_root(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="location"):
        load_config(write(tmp_path, location='"/mnt/HD/Disk1/mac-archive-two/x"'))


def test_check_location(tmp_path: Path) -> None:
    config = load_config(write(tmp_path))
    ok_path = "/mnt/HD/Disk1/mac-archive/laptop/2026"
    assert check_location(config, ok_path) is None
    for bad in (
        "/mnt/HD/Disk1/mac-archive/it's",
        "/mnt/HD/Disk1/mac-archive/a\nb",
        "/mnt/HD/Disk1/mac-archive/a b",
        "/mnt/HD/Disk1/mac-archive/../x",
        "/mnt/HD/Disk1/other",
        "/mnt/HD/Disk1/mac-archive/x/",
        "relative/path",
    ):
        assert check_location(config, bad) is not None, bad
