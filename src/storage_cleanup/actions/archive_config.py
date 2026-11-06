"""Read and check the homelab settings used by archiving."""

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Final

DEFAULT_CONFIG_PATH: Final = Path("local/archive.toml")
_SAFE_PATH: Final = re.compile(r"^[A-Za-z0-9._/-]+$")
_STRING_FIELDS: Final = (
    "user",
    "expected_hostname",
    "mount_point",
    "fs_uuid",
    "allowed_root",
    "location",
    "owner",
    "group",
)


@dataclass(frozen=True)
class ArchiveConfig:
    hosts: tuple[str, ...]
    user: str
    key: Path
    expected_hostname: str
    mount_point: str
    fs_uuid: str
    allowed_root: str
    location: str
    owner: str
    group: str


def _path_problem(path: str) -> str | None:
    """Why this is not an absolute, normalized path; None when it is."""
    if not path.startswith("/"):
        return "must be an absolute path"
    if path != "/" and path.endswith("/"):
        return "must not end with /"
    if "//" in path:
        return "must not contain //"
    if any(part in (".", "..") for part in path.split("/")):
        return "must not contain . or .. parts"
    return None


def _strictly_under(path: str, root: str) -> bool:
    return path.startswith(root.rstrip("/") + "/")


def load_config(path: Path) -> ArchiveConfig:
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"Cannot read {path}: {exc.strerror or exc}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"{path} is not valid TOML: {exc}") from exc

    for name in (*_STRING_FIELDS, "hosts", "key"):
        if name not in raw:
            raise ValueError(f"{path} is missing the setting {name}.")
    hosts = raw["hosts"]
    if (
        not isinstance(hosts, list)
        or not hosts
        or not all(isinstance(h, str) and h for h in hosts)
    ):
        raise ValueError("hosts must be a non-empty list of host names.")
    for name in (*_STRING_FIELDS, "key"):
        if not isinstance(raw[name], str) or not raw[name]:
            raise ValueError(f"{name} must be a non-empty text value.")

    for name in ("mount_point", "allowed_root", "location"):
        problem = _path_problem(raw[name])
        if problem:
            raise ValueError(f"{name} {problem}.")
    if not _strictly_under(raw["allowed_root"], raw["mount_point"]):
        raise ValueError("allowed_root must lie under mount_point.")
    if not _strictly_under(raw["location"], raw["allowed_root"]):
        raise ValueError("location must lie under allowed_root.")

    return ArchiveConfig(
        hosts=tuple(hosts),
        key=Path(raw["key"]).expanduser(),
        **{name: raw[name] for name in _STRING_FIELDS},
    )


def check_location(config: ArchiveConfig, location: str) -> str | None:
    """None when ``location`` is safe to use as a destination, else the reason."""
    problem = _path_problem(location)
    if problem:
        return f"The archive location {problem}."
    if not _SAFE_PATH.fullmatch(location):
        return "The archive location may only use letters, digits, . _ - and /."
    if not _strictly_under(location, config.allowed_root):
        return f"The archive location must lie under {config.allowed_root}."
    return None
