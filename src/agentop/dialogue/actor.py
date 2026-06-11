"""Actor: one participant in a dialogue, owning its data and tmux operations."""
from __future__ import annotations

import os
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path

from agentop.dialogue.capture import capture_pane, extract_new_content, wait_for_idle
from agentop.tmux import send_to_session

_RELAY = "Please read and respond to the message in: {path}"


def _write_file(content: str) -> str:
    fd, path = tempfile.mkstemp(prefix="agentop_relay_", suffix=".txt")
    with os.fdopen(fd, "w") as f:
        f.write(content)
    return path


class Actor:
    def __init__(self, id: str, session: str, agent: str, cwd: str):
        self.id = id            # "a" or "b"
        self.session = session  # tmux session name
        self.agent = agent
        self.cwd = cwd
        self._stop: threading.Event | None = None
        self._log: Path | None = None
        self._baseline = ""

    def attach(self, stop_event: threading.Event, log: Path) -> Actor:
        """Wire up runtime dependencies before entering the loop."""
        self._stop = stop_event
        self._log = log
        return self

    def send(self, text: str) -> None:
        path = _write_file(text)
        send_to_session(self.session, _RELAY.format(path=path))
        time.sleep(1.0)
        self._baseline = capture_pane(self.session)

    def receive(self) -> str | None:
        content = wait_for_idle(self.session, self._stop)
        if content is None:
            return None
        msg = extract_new_content(self._baseline, content).strip() or None
        if msg:
            self._append_log(msg)
        return msg

    def _append_log(self, msg: str) -> None:
        ts = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        with open(self._log, "a") as f:
            f.write(f"[{ts}] [{self.id.upper()}:{self.agent}]\n{msg}\n\n")

    def to_dict(self) -> dict:
        return {"id": self.id, "session": self.session, "agent": self.agent, "cwd": self.cwd}

    @classmethod
    def from_dict(cls, d: dict) -> Actor:
        return cls(id=d["id"], session=d["session"], agent=d["agent"], cwd=d["cwd"])
