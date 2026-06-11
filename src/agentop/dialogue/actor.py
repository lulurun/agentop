"""Actor: one participant in a dialogue, owning its data and tmux operations."""

from __future__ import annotations

import logging
import subprocess
import threading
import time

LOG = logging.getLogger(__name__)

_POLL_INTERVAL = 2.0
_IDLE_SECONDS = 12.0
_TIMEOUT = 600.0


def _capture_pane(session: str) -> str:
    try:
        r = subprocess.run(
            ["tmux", "capture-pane", "-t", session, "-p", "-S", "-"],
            capture_output=True, text=True, timeout=5,
        )
        return r.stdout if r.returncode == 0 else ""
    except (subprocess.TimeoutExpired, subprocess.SubprocessError, FileNotFoundError):
        return ""


def _wait_for_idle(session: str, stop_event: threading.Event) -> str | None:
    deadline = time.monotonic() + _TIMEOUT
    last_content = _capture_pane(session)
    last_change = time.monotonic()

    while not stop_event.is_set() and time.monotonic() < deadline:
        time.sleep(_POLL_INTERVAL)
        if stop_event.is_set():
            return None
        current = _capture_pane(session)
        if current != last_content:
            last_content = current
            last_change = time.monotonic()
        elif time.monotonic() - last_change >= _IDLE_SECONDS:
            return last_content

    return None


class Actor:
    def __init__(self, id: str, session: str):
        self.id = id        # "a" or "b"
        self.session = session
        self._stop: threading.Event | None = None

    def attach(self, stop_event: threading.Event) -> Actor:
        self._stop = stop_event
        return self

    def send(self, text: str) -> None:
        subprocess.run(["tmux", "load-buffer", "-"], input=text.encode(), capture_output=True, timeout=5)
        subprocess.run(["tmux", "paste-buffer", "-t", self.session], capture_output=True, timeout=5)
        subprocess.run(["tmux", "send-keys", "-t", self.session, "", "Enter"], capture_output=True, timeout=5)

    def receive(self) -> str | None:
        snapshot = _capture_pane(self.session)
        content = _wait_for_idle(self.session, self._stop)
        if content is None:
            return None
        new_lines = content.splitlines()[len(snapshot.splitlines()):]
        msg = "\n".join(new_lines).strip() or None
        if msg:
            LOG.info("[%s]: %s", self.id.upper(), msg)
        return msg

    def to_dict(self) -> dict:
        return {"id": self.id, "session": self.session}

    @classmethod
    def from_dict(cls, d: dict) -> Actor:
        return cls(id=d["id"], session=d["session"])
