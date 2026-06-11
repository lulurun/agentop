"""Actor: one participant in a dialogue, owning its data and tmux operations."""

from __future__ import annotations

import logging
import subprocess
import threading

from agentop.dialogue.capturer import AgentCapturer, get_capturer

LOG = logging.getLogger(__name__)


class Actor:
    def __init__(self, id: str, session: str, capturer: AgentCapturer | None = None):
        self.id = id        # "a" or "b"
        self.session = session
        self._capturer = capturer or get_capturer("claude")
        self._stop: threading.Event | None = None

    def attach(self, stop_event: threading.Event) -> Actor:
        self._stop = stop_event
        return self

    def send(self, text: str) -> None:
        subprocess.run(["tmux", "load-buffer", "-"], input=text.encode(), capture_output=True, timeout=5)
        subprocess.run(["tmux", "paste-buffer", "-t", self.session], capture_output=True, timeout=5)
        subprocess.run(["tmux", "send-keys", "-t", self.session, "", "Enter"], capture_output=True, timeout=5)

    def receive(self) -> str | None:
        from agentop.dialogue.capturer import _capture_pane
        snapshot = _capture_pane(self.session)
        content = self._capturer.wait_for_idle(self.session, self._stop)
        if content is None:
            return None
        msg = self._capturer.extract_response(snapshot, content) or None
        if msg:
            LOG.info("[%s]: %s", self.id.upper(), msg)
        return msg

    def to_dict(self) -> dict:
        return {"id": self.id, "session": self.session}

    @classmethod
    def from_dict(cls, d: dict) -> Actor:
        return cls(id=d["id"], session=d["session"])
