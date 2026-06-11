"""Actor: one participant in a dialogue, owning its tmux session and log."""
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
    def __init__(self, id: str, session: str, agent: str, stop_event: threading.Event, log: Path):
        self.id = id            # "a" or "b"
        self.session = session  # tmux session name
        self.agent = agent      # "claude", "gemini", etc.
        self._stop = stop_event
        self._log = log
        self._baseline = ""

    def send(self, text: str) -> None:
        """Write text to a temp file, tell the agent to read it, snapshot baseline."""
        path = _write_file(text)
        send_to_session(self.session, _RELAY.format(path=path))
        time.sleep(1.0)
        self._baseline = capture_pane(self.session)

    def receive(self) -> str | None:
        """Wait until idle, extract new content, log it, and return it."""
        content = wait_for_idle(self.session, self._stop)
        if content is None:
            return None
        msg = extract_new_content(self._baseline, content).strip() or None
        if msg:
            self._log_message(msg)
        return msg

    def _log_message(self, msg: str) -> None:
        ts = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        with open(self._log, "a") as f:
            f.write(f"[{ts}] [{self.id.upper()}:{self.agent}]\n{msg}\n\n")
