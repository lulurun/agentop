"""Actor: one participant in a dialogue, owning its data and tmux operations."""

from __future__ import annotations

import subprocess
import threading
from datetime import datetime
from pathlib import Path

from agentop.dialogue.capture import capture_pane, wait_for_idle


class Actor:
    def __init__(self, id: str, session: str, agent: str, cwd: str):
        self.id = id  # "a" or "b"
        self.session = session  # tmux session name
        self.agent = agent
        self.cwd = cwd
        self._stop: threading.Event | None = None
        self._log: Path | None = None

    def attach(self, stop_event: threading.Event, log: Path) -> Actor:
        self._stop = stop_event
        self._log = log
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
