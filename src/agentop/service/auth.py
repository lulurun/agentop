"""Token checks and single-use WebSocket tickets.

Browsers cannot set headers on a WebSocket request, and a token in a URL ends
up in logs. So a client spends its token on a ticket, which is short-lived,
single-use, and good for one purpose.
"""

from __future__ import annotations

import hmac
import secrets
import time
from collections.abc import Callable

TICKET_TTL_SECONDS = 30.0
EVENTS_SCOPE = "events"


def terminal_scope(session_id: str) -> str:
    return f"terminal:{session_id}"


def bearer_matches(authorization: str | None, token: str) -> bool:
    scheme, _, value = (authorization or "").partition(" ")
    return scheme.lower() == "bearer" and hmac.compare_digest(value.strip().encode(), token.encode())


class TicketStore:
    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._tickets: dict[str, tuple[str, float]] = {}

    def issue(self, scope: str) -> str:
        now = self._clock()
        self._tickets = {k: v for k, v in self._tickets.items() if v[1] > now}
        ticket = secrets.token_urlsafe(32)
        self._tickets[ticket] = (scope, now + TICKET_TTL_SECONDS)
        return ticket

    def consume(self, ticket: str | None, scope: str) -> bool:
        """Spend a ticket. Any attempt uses it up, even for the wrong scope."""
        entry = self._tickets.pop(ticket, None) if ticket else None
        if entry is None:
            return False
        ticket_scope, expires = entry
        return hmac.compare_digest(ticket_scope, scope) and self._clock() < expires
