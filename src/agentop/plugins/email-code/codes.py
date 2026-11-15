"""Sign-in codes: one pending at a time, short-lived, single use, few tries.

A code is good only with the request ID that came back to the browser that
asked for it, so a code read off a mail can't be used anywhere else, and a
stranger guessing has to ask for a code first, which is limited.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

CODE_TTL_SECONDS = 600.0
CODE_DIGITS = 6
MAX_TRIES = 5
# Asking for codes, from anyone: behind a proxy every caller looks alike.
SEND_INTERVAL_SECONDS = 60.0
SENDS_PER_HOUR = 5


class TooSoon(Exception):
    def __init__(self, retry_after: float):
        super().__init__(f"Wait {int(retry_after) + 1}s")
        self.retry_after = retry_after


@dataclass
class _Pending:
    request: str
    code_hash: str
    expires_at: float
    tries: int = 0


def _hash(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


class Codes:
    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._lock = threading.Lock()
        self._pending: _Pending | None = None
        self._sent: deque[float] = deque()

    def issue(self) -> tuple[str, str]:
        """A new request ID and code, replacing any pending one. Raises TooSoon past the limits."""
        with self._lock:
            now = self._clock()
            while self._sent and self._sent[0] <= now - 3600:
                self._sent.popleft()
            if self._sent and now - self._sent[-1] < SEND_INTERVAL_SECONDS:
                raise TooSoon(SEND_INTERVAL_SECONDS - (now - self._sent[-1]))
            if len(self._sent) >= SENDS_PER_HOUR:
                raise TooSoon(self._sent[0] + 3600 - now)
            self._sent.append(now)
            request = secrets.token_urlsafe(24)
            code = f"{secrets.randbelow(10**CODE_DIGITS):0{CODE_DIGITS}d}"
            self._pending = _Pending(request, _hash(code), now + CODE_TTL_SECONDS)
            return request, code

    def check(self, request: str, code: str) -> bool:
        """Whether this is the pending code, for this request. A right one is used up; too many wrong ones end it."""
        code = "".join(code.split())
        with self._lock:
            pending = self._pending
            if pending is None or not hmac.compare_digest(pending.request.encode(), request.encode()):
                return False
            if self._clock() >= pending.expires_at:
                self._pending = None
                return False
            if hmac.compare_digest(pending.code_hash, _hash(code)):
                self._pending = None
                return True
            pending.tries += 1
            if pending.tries >= MAX_TRIES:
                self._pending = None
            return False

    def cancel(self) -> None:
        with self._lock:
            self._pending = None
