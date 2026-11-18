"""What every /api/v2 handler gets: the service's Context, and the signed-in client, as FastAPI dependencies."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated

from fastapi import Depends, Header, HTTPException
from starlette.requests import HTTPConnection

from .. import auth
from ..config import Settings
from ..identity import Identity
from ..plugins import PluginHost
from ..store import SessionStore

REPO_ROOT = Path(__file__).resolve().parents[4]


@dataclass
class Context:
    settings: Settings
    identity: Identity
    store: SessionStore
    tickets: auth.TicketStore = field(default_factory=auth.TicketStore)
    clients: auth.ClientStore = field(default_factory=auth.ClientStore)
    started_at: float = field(default_factory=time.time)
    background: set = field(default_factory=set)
    # Set when the identity came from service.json. Then a token rotated by the
    # CLI takes effect at once, with no restart, and the old one stops working.
    identity_file: Path | None = None
    _identity_mtime: int | None = None
    plugins: PluginHost | None = None
    # The tools found on this host when the service started (host.prepare). None
    # until then, and in tests that build the app directly: found on first ask.
    available: list[str] | None = None

    def current_identity(self) -> Identity:
        if self.identity_file is None:
            return self.identity
        try:
            mtime = self.identity_file.stat().st_mtime_ns
        except OSError:
            return self.identity  # keep the last good one rather than locking everyone out
        if mtime != self._identity_mtime:
            try:
                data = json.loads(self.identity_file.read_text())
                self.identity = Identity(data["token"], data["instance_id"], data["name"])
            except (OSError, ValueError, KeyError):
                return self.identity
            self._identity_mtime = mtime
        return self.identity


def get_ctx(conn: HTTPConnection) -> Context:
    return conn.app.state.ctx


Ctx = Annotated[Context, Depends(get_ctx)]


def require_client(ctx: Ctx, authorization: Annotated[str | None, Header()] = None) -> auth.Client:
    client = ctx.clients.authenticate(auth.bearer_value(authorization))
    if client is None:
        raise HTTPException(401, "Missing, expired or revoked token", {"WWW-Authenticate": "Bearer"})
    return client


Me = Annotated[auth.Client, Depends(require_client)]
AUTHED = [Depends(require_client)]
