"""Actor: one participant in a dialogue, owning its data and tmux operations."""

from __future__ import annotations

import logging
import re
import threading
import time

from agentop.dialogue.capturer import Capturer
from agentop.tmux import CapturePane, Session

LOG = logging.getLogger(__name__)

# Sentinel returned by receive() when the agent signals dialogue completion
DIALOGUE_COMPLETE = "<<DIALOGUE_COMPLETE>>"


class Actor:
    def __init__(
        self,
        id: str,
        session: str,
        name: str = "",
        capturer: Capturer | None = None,
        tool: str = "claude",
    ):
        self.id = id  # "a" or "b"
        self.session = session
        self.name = name or id.upper()
        self.tool = tool
        self._capturer = capturer or Capturer()
        self._stop: threading.Event | None = None
        self._turn = 0  # incremented each receive(); matches turn:N in delimiters

    def attach(self, stop_event: threading.Event) -> Actor:
        self._stop = stop_event
        return self

    def send(self, text: str) -> None:
        LOG.info("[%s] send start", self.name)
        start_time = time.monotonic()
        Session.paste_text(self.session, text)

        # Wait until the terminal screen starts changing (pasting begins)
        original_content = CapturePane.screen(self.session)
        paste_start_time = time.monotonic()
        while time.monotonic() - paste_start_time < 2.0:
            time.sleep(0.05)
            if CapturePane.screen(self.session) != original_content:
                LOG.info("[%s] paste detected after %.3f s", self.name, time.monotonic() - paste_start_time)
                break
        else:
            LOG.info("[%s] paste start timeout reached", self.name)

        # Wait until the terminal screen stops changing (pasting finished)
        last_content = CapturePane.screen(self.session)
        stable_since = time.monotonic()
        while time.monotonic() - stable_since < 0.5:
            time.sleep(0.1)
            cur = CapturePane.screen(self.session)
            if cur != last_content:
                last_content = cur
                stable_since = time.monotonic()
        LOG.info("[%s] screen stable, sending Enter. total paste wait: %.3f s", self.name, time.monotonic() - start_time)
        Session.send_keys(self.session, "Enter")

    def receive(self) -> str | None:
        content = self._capturer.wait_for_idle(self.session, self._stop)
        if content is None:
            return None
        self._turn += 1
        msg = self._parse_output(content, self._turn)
        if msg:
            LOG.info("[%s]: %s", self.name, msg)
        return msg or None

    def _parse_output(self, raw: str, turn: int) -> str:
        """Find the exact BEGIN/END delimiter block for this turn and return its content."""
        name = re.escape(self.name)
        begin_re = re.compile(rf"---\s*BEGIN\s+(output|complete)\s+from\s+{name}\s+turn:{turn}\s*---", re.IGNORECASE)
        end_re = re.compile(rf"---\s*END\s+(output|complete)\s+from\s+{name}\s+turn:{turn}\s*---", re.IGNORECASE)

        lines = raw.splitlines()
        for i, line in enumerate(lines):
            m = begin_re.search(line)
            if m:
                kind = m.group(1).lower()
                for j in range(i + 1, len(lines)):
                    if end_re.search(lines[j]):
                        content = "\n".join(lines[i + 1:j]).strip()
                        if kind == "complete":
                            LOG.info("[%s] turn:%d complete delimiters found", self.name, turn)
                            return DIALOGUE_COMPLETE
                        LOG.info("[%s] turn:%d output delimiters found", self.name, turn)
                        return content
                LOG.warning("[%s] turn:%d BEGIN found but no END — using partial content", self.name, turn)
                return "\n".join(lines[i + 1:]).strip()

        LOG.warning("[%s] turn:%d delimiters not found — using full raw response", self.name, turn)
        return raw

    def to_dict(self) -> dict:
        return {"id": self.id, "session": self.session, "name": self.name, "tool": self.tool}

    @classmethod
    def from_dict(cls, d: dict) -> Actor:
        return cls(id=d["id"], session=d["session"], name=d.get("name", ""), tool=d.get("tool", "claude"))
