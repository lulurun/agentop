"""The service's stable identity and access token, in service.json (mode 0600)."""

from __future__ import annotations

import json
import secrets
import socket
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

from agentop.jsonfile import write_json_atomic
from agentop.paths import data_dir


@dataclass(frozen=True)
class Identity:
    token: str
    instance_id: str
    name: str


def identity_path() -> Path:
    return data_dir() / "service.json"


def _write(identity: Identity) -> None:
    # 0600, so the token is never readable by others.
    write_json_atomic(identity_path(), asdict(identity))


def load_or_create(name: str | None = None) -> Identity:
    """Return the identity, creating it on first run. A given name replaces the stored one."""
    path = identity_path()
    if path.exists():
        path.chmod(0o600)
        with open(path) as f:
            data = json.load(f)
        identity = Identity(token=data["token"], instance_id=data["instance_id"], name=data["name"])
        if name and name != identity.name:
            identity = Identity(identity.token, identity.instance_id, name)
            _write(identity)
        return identity
    identity = Identity(
        token=secrets.token_urlsafe(32),
        instance_id=str(uuid.uuid4()),
        name=name or socket.gethostname(),
    )
    _write(identity)
    return identity


def rotate_token() -> Identity:
    """Replace the token. Every saved connection stops working until it is given the new one."""
    current = load_or_create()
    identity = Identity(secrets.token_urlsafe(32), current.instance_id, current.name)
    _write(identity)
    return identity
