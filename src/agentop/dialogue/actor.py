"""Actor: one participant in a dialogue, owning its data and tmux operations."""

from __future__ import annotations

import logging
import re
import subprocess
import threading

from agentop.dialogue.capturer import AgentCapturer, _capture_pane, get_capturer

LOG = logging.getLogger(__name__)

# Matches lines like:  --- output from Alex turn:3 ---
_DELIMITER_RE = re.compile(r"---\s*output from (\w+)\s+turn:\d+\s*---", re.IGNORECASE)


class Actor:
    def __init__(
        self,
        id: str,
        session: str,
        name: str = "",
        capturer: AgentCapturer | None = None,
    ):
        self.id = id        # "a" or "b"
        self.session = session
        self.name = name or id.upper()
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
        snapshot = _capture_pane(self.session)
        content = self._capturer.wait_for_idle(self.session, self._stop)
        if content is None:
            return None
        raw = self._capturer.extract_response(snapshot, content)
        if not raw:
            return None
        msg = self._parse_output(raw)
        if msg:
            LOG.info("[%s]: %s", self.name, msg)
        return msg or None

    def _parse_output(self, raw: str) -> str:
        """Return only the content after the delimiter line, or raw if absent."""
        lines = raw.splitlines()
        for i, line in enumerate(lines):
            if _DELIMITER_RE.search(line):
                extracted = "\n".join(lines[i + 1 :]).strip()
                LOG.info("[%s] delimiter found on line %d", self.name, i)
                return extracted
        LOG.warning("[%s] no delimiter found — using full raw response", self.name)
        return raw

    def to_dict(self) -> dict:
        return {"id": self.id, "session": self.session, "name": self.name}

    @classmethod
    def from_dict(cls, d: dict) -> Actor:
        return cls(id=d["id"], session=d["session"], name=d.get("name", ""))
