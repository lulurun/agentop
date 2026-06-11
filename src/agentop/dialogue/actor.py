"""Actor: one participant in a dialogue, owning its data and tmux operations."""

from __future__ import annotations

import logging
import subprocess
import threading

from agentop.dialogue.capture import capture_pane, wait_for_idle

LOG = logging.getLogger(__name__)


class Actor:
    def __init__(self, id: str, session: str, agent: str, cwd: str):
        self.id = id  # "a" or "b"
        self.session = session  # tmux session name
        self.agent = agent
        self.cwd = cwd
        self._stop: threading.Event | None = None

    def attach(self, stop_event: threading.Event) -> Actor:
        self._stop = stop_event
        return self

    def send(self, text: str) -> None:
        subprocess.run(["tmux", "load-buffer", "-"], input=text.encode(), capture_output=True, timeout=5)
        subprocess.run(["tmux", "paste-buffer", "-t", self.session], capture_output=True, timeout=5)
        subprocess.run(["tmux", "send-keys", "-t", self.session, "", "Enter"], capture_output=True, timeout=5)

    def receive(self) -> str | None:
        snapshot = capture_pane(self.session)
        content = wait_for_idle(self.session, self._stop)
        if content is None:
            return None
        start = len(snapshot.splitlines())
        new_lines = content.splitlines()[start:]
        msg = "\n".join(new_lines).strip() or None
        if msg:
            LOG.info("[%s:%s]\n%s", self.id.upper(), self.agent, msg)
        return msg

    def to_dict(self) -> dict:
        return {"id": self.id, "session": self.session, "agent": self.agent, "cwd": self.cwd}

    @classmethod
    def from_dict(cls, d: dict) -> Actor:
        return cls(id=d["id"], session=d["session"], agent=d["agent"], cwd=d["cwd"])
