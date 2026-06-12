"""Actor: one participant in a dialogue, owning its data and tmux operations."""

from __future__ import annotations

import logging
import re
import threading

from agentop.dialogue.capturer import Capturer
from agentop.tmux import Session

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
        Session.paste_text(self.session, text)
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
        begin_re = re.compile(rf"BEGIN.+turn:{turn}", re.IGNORECASE)
        end_re = re.compile(rf"END.+turn:{turn}", re.IGNORECASE)

        content = ""
        begin_line = None
        for line in raw.splitlines():
            if begin_line is None:
                if begin_re.search(line):
                    begin_line = line
            elif end_re.search(line):
                if "complete" in begin_line.lower():
                    return DIALOGUE_COMPLETE
                return content.strip()
            else:
                content += line + "\n"

        if begin_line is None:
            LOG.warning("[%s] turn:%d delimiters not found", self.name, turn)
        else:
            LOG.warning("[%s] turn:%d BEGIN found but no END", self.name, turn)
        return raw

