import shlex
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from storage_cleanup.actions.archive_config import ArchiveConfig
from storage_cleanup.actions.remote import (
    Remote,
    RemoteError,
    check_storage,
    connect,
)
from storage_cleanup.scan.context import CommandResult

UUID = "00000000-0000-0000-0000-000000000000"


def make_config(key: Path = Path("/keys/k")) -> ArchiveConfig:
    return ArchiveConfig(
        hosts=("one.example", "two.example"),
        user="archiver",
        key=key,
        expected_hostname="nas",
        mount_point="/mnt/HD/Disk1",
        fs_uuid=UUID,
        allowed_root="/mnt/HD/Disk1/mac-archive",
        location="/mnt/HD/Disk1/mac-archive/laptop",
        owner="archiver",
        group="users",
    )


@dataclass
class FakeSsh:
    """Answers by (host, remote command line); records every call."""

    answers: dict[tuple[str, str], CommandResult] = field(default_factory=dict)
    calls: list[tuple[list[str], float]] = field(default_factory=list)

    def __call__(self, args: Sequence[str], timeout: float) -> CommandResult:
        self.calls.append((list(args), timeout))
        host = args[args.index("-i") + 2].split("@", 1)[1]
        return self.answers.get(
            (host, args[-1]), CommandResult(127, "", "unexpected call")
        )


def remote_for(fake: FakeSsh, host: str = "one.example") -> Remote:
    return Remote(make_config(), host, fake)


def line(*argv: str, sudo: bool = True) -> str:
    return shlex.join(["sudo", "-n", *argv] if sudo else list(argv))


def test_ssh_args_exact() -> None:
    remote = remote_for(FakeSsh())
    assert remote.ssh_args(["mkdir", "-p", "/x"]) == [
        "/usr/bin/ssh",
        "-o",
        "IdentitiesOnly=yes",
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        "ConnectTimeout=10",
        "-o",
        "ServerAliveInterval=30",
        "-o",
        "ServerAliveCountMax=4",
        "-i",
        "/keys/k",
        "archiver@one.example",
        "--",
        "sudo -n mkdir -p /x",
    ]


def test_hostname_goes_without_sudo() -> None:
    assert remote_for(FakeSsh()).ssh_args(["hostname"])[-1] == "hostname"


def test_awkward_paths_round_trip() -> None:
    awkward = ["a b", "it's", "$HOME", "-rf", 'q"uote', "semi;colon"]
    last = remote_for(FakeSsh()).ssh_args(["mkdir", "--", *awkward])[-1]
    assert shlex.split(last) == ["sudo", "-n", "mkdir", "--", *awkward]


def test_call_refuses_other_programs() -> None:
    fake = FakeSsh()
    remote = remote_for(fake)
    for argv in (["rm", "-rf", "/"], ["sh", "-c", "other"], [], ["sudo", "ls"]):
        with pytest.raises(ValueError):
            remote.call(argv)
    assert fake.calls == []


def test_call_passes_timeout_and_returns_result() -> None:
    fake = FakeSsh({("one.example", line("df")): CommandResult(0, "out", "")})
    result = remote_for(fake).call(["df"], timeout=5)
    assert result.stdout == "out"
    assert fake.calls[0][1] == 5


def test_rsync_args_exact() -> None:
    assert remote_for(FakeSsh()).rsync_args("/Users/me/Big", "/mnt/HD/Disk1/a") == [
        "/usr/bin/rsync",
        "-a",
        "--rsync-path=sudo -n rsync",
        "-e",
        "/usr/bin/ssh -o IdentitiesOnly=yes -o BatchMode=yes"
        " -o StrictHostKeyChecking=yes -o ConnectTimeout=10"
        " -o ServerAliveInterval=30 -o ServerAliveCountMax=4 -i /keys/k",
        "--",
        "/Users/me/Big",
        "archiver@one.example:/mnt/HD/Disk1/a/",
    ]


@pytest.mark.parametrize("dest", ["/a b", "/a'b", "/a;b", "/a\nb", "", "/a$b"])
def test_rsync_refuses_unsafe_destination(dest: str) -> None:
    with pytest.raises(ValueError):
        remote_for(FakeSsh()).rsync_args("/src", dest)


def hostname_ok(host: str, name: str = "nas") -> tuple[tuple[str, str], CommandResult]:
    return (host, "hostname"), CommandResult(0, name + "\n", "")


def test_connect_falls_through_on_dns_failure() -> None:
    dns = CommandResult(255, "", "ssh: Could not resolve hostname one.example")
    fake = FakeSsh(
        {("one.example", "hostname"): dns, **dict([hostname_ok("two.example")])}
    )
    remote = connect(make_config(), fake)
    assert remote.host == "two.example"
    assert [c[0][-3] for c in fake.calls] == [
        "archiver@one.example",
        "archiver@two.example",
    ]


@pytest.mark.parametrize(
    "stderr",
    [
        "Connection refused",
        "Connection timed out",
        "No route to host",
        "Network is unreachable",
    ],
)
def test_connect_unreachable_variants_try_next(stderr: str) -> None:
    fake = FakeSsh(
        {
            ("one.example", "hostname"): CommandResult(255, "", stderr),
            **dict([hostname_ok("two.example")]),
        }
    )
    assert connect(make_config(), fake).host == "two.example"


def test_connect_all_unreachable() -> None:
    down = CommandResult(255, "", "Connection refused")
    fake = FakeSsh(
        {("one.example", "hostname"): down, ("two.example", "hostname"): down}
    )
    with pytest.raises(RemoteError, match="reach"):
        connect(make_config(), fake)


def test_connect_auth_failure_does_not_try_next() -> None:
    denied = CommandResult(
        255, "", "archiver@one.example: Permission denied (publickey)."
    )
    fake = FakeSsh(
        {("one.example", "hostname"): denied, **dict([hostname_ok("two.example")])}
    )
    with pytest.raises(RemoteError) as exc:
        connect(make_config(), fake)
    assert exc.value.message == "The homelab refused the SSH key."
    assert len(fake.calls) == 1


@pytest.mark.parametrize(
    "stderr",
    [
        "Host key verification failed.",
        "WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED!",
    ],
)
def test_connect_host_key_problem(stderr: str) -> None:
    fake = FakeSsh({("one.example", "hostname"): CommandResult(255, "", stderr)})
    with pytest.raises(RemoteError) as exc:
        connect(make_config(), fake)
    assert exc.value.message == (
        "The homelab's host key is unknown or changed; "
        "check it by hand before archiving."
    )
    assert len(fake.calls) == 1


def test_connect_wrong_hostname() -> None:
    fake = FakeSsh(dict([hostname_ok("one.example", "other")]))
    with pytest.raises(RemoteError) as exc:
        connect(make_config(), fake)
    assert exc.value.message == "Unexpected host other"


def test_connect_other_failure_is_reported() -> None:
    fake = FakeSsh({("one.example", "hostname"): CommandResult(1, "", "boom")})
    with pytest.raises(RemoteError, match="boom"):
        connect(make_config(), fake)


def storage_fake(findmnt: str, avail: str) -> FakeSsh:
    return FakeSsh(
        {
            (
                "one.example",
                line("findmnt", "-no", "UUID", "--mountpoint", "/mnt/HD/Disk1"),
            ): CommandResult(0, findmnt, ""),
            (
                "one.example",
                line("df", "-B1", "--output=avail", "--", "/mnt/HD/Disk1"),
            ): CommandResult(0, avail, ""),
        }
    )


def test_check_storage_ok_returns_quietly() -> None:
    fake = storage_fake(UUID + "\n", "Avail\n5000000000\n")
    check_storage(remote_for(fake), 1000)


def test_check_storage_not_mounted() -> None:
    fake = storage_fake("", "Avail\n5\n")
    with pytest.raises(RemoteError, match="not mounted"):
        check_storage(remote_for(fake), 0)


def test_check_storage_findmnt_failure_means_not_mounted() -> None:
    fake = FakeSsh(
        {
            (
                "one.example",
                line("findmnt", "-no", "UUID", "--mountpoint", "/mnt/HD/Disk1"),
            ): CommandResult(1, "", ""),
        }
    )
    with pytest.raises(RemoteError, match="not mounted"):
        check_storage(remote_for(fake), 0)


def test_check_storage_wrong_uuid() -> None:
    fake = storage_fake("11111111-1111-1111-1111-111111111111\n", "Avail\n5\n")
    with pytest.raises(RemoteError, match="not the expected one"):
        check_storage(remote_for(fake), 0)


def test_check_storage_low_space() -> None:
    fake = storage_fake(UUID + "\n", "Avail\n1000\n")
    with pytest.raises(
        RemoteError,
        match=r"Not enough free space on the homelab \(.* free, .* needed\)",
    ):
        check_storage(remote_for(fake), 5000)


def test_check_storage_unparseable_df() -> None:
    fake = storage_fake(UUID + "\n", "garbage")
    with pytest.raises(RemoteError):
        check_storage(remote_for(fake), 0)


@pytest.mark.parametrize(
    "argv",
    [
        ["find", "/mnt", "-delete"],
        ["mkdir", "-delete"],
        ["find", "/", "-exec", "rm", "-rf", "{}", "+"],
        ["find", "/", "-execdir", "sh", "-c", "x", ";"],
        ["find", "/", "-ok", "rm", "{}", ";"],
        ["find", "/", "-okdir", "rm", "{}", ";"],
        ["find", "/", "-exec"],
        ["find", "/", "-fprint", "/etc/x"],
        ["find", "/", "-fprintf", "/etc/x", "%p"],
        ["find", "/", "-fls", "/etc/x"],
    ],
)
def test_dangerous_argv_refused_by_call_and_ssh_args(argv: list[str]) -> None:
    fake = FakeSsh()
    remote = remote_for(fake)
    with pytest.raises(ValueError):
        remote.call(argv)
    with pytest.raises(ValueError):
        remote.ssh_args(argv)
    assert fake.calls == []


def test_ssh_args_refuses_unlisted_programs() -> None:
    with pytest.raises(ValueError):
        remote_for(FakeSsh()).ssh_args(["rm", "-rf", "/x"])


def test_find_exec_chmod_still_works() -> None:
    argv = [
        "find", "/b", "(", "-type", "d", "-exec", "chmod", "2770", "{}", "+", ")",
        "-o", "(", "-type", "f", "-exec", "chmod", "660", "{}", "+", ")",
    ]  # fmt: skip
    last = remote_for(FakeSsh()).ssh_args(argv)[-1]
    assert shlex.split(last) == ["sudo", "-n", *argv]


def test_check_target_accepts_the_archive_disk() -> None:
    from storage_cleanup.actions.remote import check_target

    fake = FakeSsh(
        {
            ("one.example", line("findmnt", "-no", "UUID", "--target", "/a b")): (
                CommandResult(0, f"{UUID}\n", "")
            )
        }
    )
    check_target(remote_for(fake), "/a b")
    assert len(fake.calls) == 1


@pytest.mark.parametrize(
    "answer",
    [
        CommandResult(0, "11111111-0000-0000-0000-000000000000\n", ""),
        CommandResult(0, "", ""),
        CommandResult(1, "", "findmnt: oops"),
        CommandResult(255, "", "Connection closed"),
    ],
)
def test_check_target_refuses_anything_else(answer: CommandResult) -> None:
    from storage_cleanup.actions.remote import check_target

    fake = FakeSsh(
        {("one.example", line("findmnt", "-no", "UUID", "--target", "/x")): answer}
    )
    with pytest.raises(RemoteError) as caught:
        check_target(remote_for(fake), "/x")
    assert caught.value.message == "The archive disk is not the expected one"
