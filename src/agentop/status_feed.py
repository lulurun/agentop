"""Per-session, in-memory status subscriptions for service components."""
from __future__ import annotations

import hashlib
import os
import queue
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SessionStatus:
    session_id: str
    state: str
    since: float
    source: str
    event: str = ""


class StatusFeed:
    def __init__(self):
        self._lock = threading.Lock()
        self._latest: dict[str, SessionStatus] = {}
        self._subscribers: dict[str, set[queue.Queue]] = {}

    def subscribe(self, session_id: str) -> queue.Queue:
        subscription = queue.Queue(maxsize=1)
        with self._lock:
            self._subscribers.setdefault(session_id, set()).add(subscription)
            if session_id in self._latest:
                subscription.put_nowait(self._latest[session_id])
        return subscription

    def unsubscribe(self, session_id: str, subscription: queue.Queue) -> None:
        with self._lock:
            listeners = self._subscribers.get(session_id)
            if listeners is not None:
                listeners.discard(subscription)
                if not listeners:
                    del self._subscribers[session_id]

    def publish(self, status: SessionStatus) -> None:
        with self._lock:
            if self._latest.get(status.session_id) == status:
                return
            self._latest[status.session_id] = status
            for subscription in self._subscribers.get(status.session_id, ()):
                try:
                    subscription.get_nowait()
                except queue.Empty:
                    pass
                subscription.put_nowait(status)

    def clear(self) -> None:
        with self._lock:
            self._latest.clear()
            for sid, listeners in self._subscribers.items():
                for subscription in listeners:
                    try:
                        subscription.get_nowait()
                    except queue.Empty:
                        pass
                    subscription.put_nowait(SessionStatus(sid, "unknown", 0.0, "unavailable"))


def socket_path(state: Path) -> Path:
    path = state / "attention" / "feed.sock"
    if len(os.fsencode(path)) < 100:
        return path
    key = hashlib.sha256(os.fsencode(state.resolve())).hexdigest()[:20]
    temporary = Path("/tmp") if Path("/tmp").is_dir() else Path(tempfile.gettempdir())
    return temporary / f"agentop-{os.getuid()}-{key}" / "feed.sock"
