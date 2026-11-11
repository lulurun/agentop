"""Service settings, from ~/.agentop/config.toml and command-line overrides."""

from __future__ import annotations

import ipaddress
import tomllib
from dataclasses import dataclass, fields, replace

from agentop.paths import data_dir

DEFAULT_PORT = 9775


@dataclass(frozen=True)
class Settings:
    host: str = "127.0.0.1"
    port: int = DEFAULT_PORT
    name: str | None = None
    tls_cert: str | None = None
    tls_key: str | None = None

    @property
    def is_loopback(self) -> bool:
        if self.host == "localhost":
            return True
        try:
            return ipaddress.ip_address(self.host).is_loopback
        except ValueError:
            return False

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
