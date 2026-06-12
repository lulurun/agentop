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
    def __init__(self, id: str, session: str, name: str):
        self.id = id  # "a" or "b"
        self.session = session
        self.name = name or id.upper()
        self._capturer = Capturer()
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
        name = re.escape(self.name)
        begin_re = re.compile(rf"--- BEGIN .+ from {name} turn:{turn} ---$", re.IGNORECASE)
        end_re = re.compile(rf"--- END .+ from {name} turn:{turn} ---$", re.IGNORECASE)

        in_block = False
        buffer = []
        begin_line = None
        for line in reversed(raw.splitlines()):
            stripped = line.strip()
            if not in_block:
                if end_re.search(stripped):
                    in_block = True
            elif begin_re.search(stripped):
                begin_line = stripped
                break
            else:
                buffer.append(line)

        if begin_line is None:
            LOG.warning("[%s] turn:%d delimiters not found", self.name, turn)
            return raw

        content = "\n".join(reversed(buffer)).strip()
        if "complete" in begin_line.lower():
            LOG.info("[%s] turn:%d complete", self.name, turn)
            return DIALOGUE_COMPLETE
        return content
