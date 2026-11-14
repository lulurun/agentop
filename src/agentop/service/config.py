"""Service settings, from ~/.agentop/config.toml and command-line overrides."""

from __future__ import annotations

import ipaddress
import re
import tomllib
from dataclasses import dataclass, fields, replace

from agentop.paths import data_dir

DEFAULT_PORT = 9775
_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}
MIN_CLIENT_TTL_SECONDS = 60


def parse_duration(value: str | int | float | None) -> float | None:
    """`90`, `"90s"`, `"30m"`, `"12h"` or `"30d"`, in seconds. `0`, `""` or `"none"` mean no limit (None)."""
    if value is None or value == 0:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if value < 0:
            raise ValueError(f"Not a duration: {value!r}")
        return float(value)
    text = str(value).strip().lower()
    if text in ("", "0", "none", "never"):
        return None
    match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*([smhd]?)", text)
    if not match:
        raise ValueError(f"Not a duration: {value!r}. Use a number of seconds, or 30m, 12h, 30d.")
    return float(match[1]) * _UNITS[match[2] or "s"]


@dataclass(frozen=True)
class Settings:
    host: str = "127.0.0.1"
    port: int = DEFAULT_PORT
    name: str | None = None
    tls_cert: str | None = None
    tls_key: str | None = None
    # How long a client stays signed in without renewing, and how long at most.
    client_ttl: str | int = "12h"
    client_max_age: str | int = "30d"

    @property
    def is_loopback(self) -> bool:
        if self.host == "localhost":
            return True
        try:
            return ipaddress.ip_address(self.host).is_loopback
        except ValueError:
            return False

    @property
    def client_ttl_seconds(self) -> float:
        seconds = parse_duration(self.client_ttl)
        if seconds is None or seconds < MIN_CLIENT_TTL_SECONDS:
            raise ValueError(f"client_ttl must be at least {MIN_CLIENT_TTL_SECONDS} seconds, not {self.client_ttl!r}")
        return seconds

    @property
    def client_max_age_seconds(self) -> float | None:
        return parse_duration(self.client_max_age)

    @property
    def tls(self) -> bool:
        return bool(self.tls_cert and self.tls_key)


def load(**overrides) -> Settings:
    """Read config.toml, then apply overrides that are not None. Unknown keys are ignored."""
    path = data_dir() / "config.toml"
    known = {f.name for f in fields(Settings)}
    values = {}
    if path.exists():
        with open(path, "rb") as f:
            values = {k: v for k, v in tomllib.load(f).items() if k in known}
    settings = Settings(**values)
    return replace(settings, **{k: v for k, v in overrides.items() if v is not None})
