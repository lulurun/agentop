"""Clients, their expiring tokens, and single-use WebSocket tickets.

The service token (in service.json) only adds a client. Each client then gets
its own token, which expires after `client_ttl` unless the client renews it,
and which can be revoked on its own. Only a hash of a client's token is kept.
See docs/v2-design.md, decision D7.

Browsers cannot set headers on a WebSocket request, and a token in a URL ends
up in logs. So a client spends its token on a ticket, which is short-lived,
single-use, and good for one purpose.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import tempfile
import time
import uuid
from collections import deque
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from agentop.paths import state_dir

TICKET_TTL_SECONDS = 30.0
EVENTS_SCOPE = "events"
# A replaced token keeps working this long, so requests already on their way don't fail.
RENEW_GRACE_SECONDS = 60.0
# Failed tries with the service token, across all callers: behind a proxy they all look alike.
ADD_FAILURES_ALLOWED = 10
ADD_FAILURE_WINDOW_SECONDS = 60.0


def terminal_scope(session_id: str) -> str:
    return f"terminal:{session_id}"


def bearer_value(authorization: str | None) -> str | None:
    scheme, _, value = (authorization or "").partition(" ")
    value = value.strip()
    return value if scheme.lower() == "bearer" and value else None


def bearer_matches(authorization: str | None, token: str) -> bool:
    value = bearer_value(authorization)
    return value is not None and hmac.compare_digest(value.encode(), token.encode())


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def clients_path() -> Path:
    return state_dir() / "clients.json"


@dataclass(frozen=True)
class Client:
    id: str
    name: str
    created_at: float
    last_seen: float
    # When the token runs out unless renewed. Never past `ends_at`.
    expires_at: float
    # When the client must be added again with the service token. None: never.
    ends_at: float | None
    token_hash: str
    previous_hash: str | None = None
    previous_until: float = 0.0


class ClientStore:
    """The clients, in clients.json (mode 0600). Changes made by the CLI are picked up on the next request."""

    def __init__(self, path: Path | None = None, clock: Callable[[], float] = time.time):
        self._path = path
        self._clock = clock
        self._clients: dict[str, Client] = {}
        self._mtime: int | None = None
        self._failures: deque[float] = deque()
        self._reload()

    # ------------------------------------------------------------- storage

    def _reload(self) -> None:
        if self._path is None:
            return
        try:
            mtime = self._path.stat().st_mtime_ns
        except FileNotFoundError:
            if self._mtime is not None:  # removed: everyone is signed out
                self._clients, self._mtime = {}, None
            return
        except OSError:
            return
        if mtime == self._mtime:
            return
        try:
            data = json.loads(self._path.read_text())
            self._clients = {c["id"]: Client(**c) for c in data.get("clients", [])}
        except (OSError, ValueError, TypeError, KeyError):
            return  # keep what we had rather than signing everyone out
        self._mtime = mtime

    def _save(self) -> None:
        now = self._clock()
        self._clients = {k: c for k, c in self._clients.items() if self._live(c, now)}
        if self._path is None:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self._path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump({"clients": [asdict(c) for c in self._clients.values()]}, f, indent=2)
            os.replace(tmp, self._path)
        except Exception:
            Path(tmp).unlink(missing_ok=True)
            raise
        self._mtime = self._path.stat().st_mtime_ns

    @staticmethod
    def _live(client: Client, now: float) -> bool:
        return now < client.expires_at

    # ------------------------------------------------------------- service token

    def add_blocked(self) -> bool:
        """Too many wrong service tokens lately. Clients already added are not affected."""
        cutoff = self._clock() - ADD_FAILURE_WINDOW_SECONDS
        while self._failures and self._failures[0] < cutoff:
            self._failures.popleft()
        return len(self._failures) >= ADD_FAILURES_ALLOWED

    def add_failed(self) -> None:
        self._failures.append(self._clock())

    # ------------------------------------------------------------- clients

    def add(self, name: str, ttl: float, max_age: float | None) -> tuple[Client, str]:
        self._reload()
        now = self._clock()
        token = secrets.token_urlsafe(32)
        ends_at = now + max_age if max_age else None
        client = Client(
            id=uuid.uuid4().hex[:12],
            name=name,
            created_at=now,
            last_seen=now,
            expires_at=min(now + ttl, ends_at) if ends_at else now + ttl,
            ends_at=ends_at,
            token_hash=_hash(token),
        )
        self._clients[client.id] = client
        self._save()
        return client, token

    def authenticate(self, token: str | None) -> Client | None:
        """The client this token belongs to, if it is still good. Its previous token counts for a minute after a renewal."""
        if not token:
            return None
        self._reload()
        now = self._clock()
        digest = _hash(token)
        for client in self._clients.values():
            current = hmac.compare_digest(client.token_hash, digest)
            previous = client.previous_hash is not None and hmac.compare_digest(client.previous_hash, digest)
            if (current or (previous and now < client.previous_until)) and self._live(client, now):
                client = replace(client, last_seen=now)
                self._clients[client.id] = client  # last_seen is written with the next change
                return client
        return None

    def renew(self, client_id: str, ttl: float) -> tuple[Client, str]:
        """A new token, good for another `ttl`. The old one stops working after a short grace."""
        self._reload()
        client = self._clients[client_id]
        now = self._clock()
        token = secrets.token_urlsafe(32)
        expires = now + ttl
        if client.ends_at is not None:
            expires = min(expires, client.ends_at)
        client = replace(
            client,
            token_hash=_hash(token),
            previous_hash=client.token_hash,
            previous_until=now + RENEW_GRACE_SECONDS,
            expires_at=expires,
            last_seen=now,
        )
        self._clients[client.id] = client
        self._save()
        return client, token

    def revoke(self, client_id: str) -> bool:
        self._reload()
        if self._clients.pop(client_id, None) is None:
            return False
        self._save()
        return True

    def revoke_all(self) -> int:
        self._reload()
        count = len(self._clients)
        self._clients = {}
        self._save()
        return count

    def is_active(self, client_id: str) -> bool:
        self._reload()
        client = self._clients.get(client_id)
        return client is not None and self._live(client, self._clock())

    def list(self) -> list[Client]:
        self._reload()
        now = self._clock()
        return sorted((c for c in self._clients.values() if self._live(c, now)), key=lambda c: c.created_at)


class TicketStore:
    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._tickets: dict[str, tuple[str, str, float]] = {}

    def issue(self, scope: str, client_id: str) -> str:
        now = self._clock()
        self._tickets = {k: v for k, v in self._tickets.items() if v[2] > now}
        ticket = secrets.token_urlsafe(32)
        self._tickets[ticket] = (scope, client_id, now + TICKET_TTL_SECONDS)
        return ticket

    def consume(self, ticket: str | None, scope: str) -> str | None:
        """Spend a ticket, returning the client it was issued to. Any attempt uses it up, even for the wrong scope."""
        entry = self._tickets.pop(ticket, None) if ticket else None
        if entry is None:
            return None
        ticket_scope, client_id, expires = entry
        if hmac.compare_digest(ticket_scope, scope) and self._clock() < expires:
            return client_id
        return None
