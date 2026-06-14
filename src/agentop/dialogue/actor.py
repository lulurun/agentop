"""Actor: one participant in a dialogue, owning its data and tmux operations."""

from __future__ import annotations

import logging
import re
import threading

from agentop.dialogue.capturer import Capturer
from agentop.dialogue.status import ReceiveStatus
from agentop.tmux import Session

LOG = logging.getLogger(__name__)


class Actor:
    def __init__(self, id: str, session: str, name: str):
        self.id = id  # "a" or "b"
        self.session = session
        self.name = name or id.upper()
        self._capturer = Capturer()
        self._stop: threading.Event | None = None
        self._turn = 0  # incremented each receive()

    def attach(self, stop_event: threading.Event) -> Actor:
        self._stop = stop_event
        return self

    def send(self, text: str) -> None:
        Session.paste_text(self.session, text)
        Session.send_keys(self.session, "Enter")

    def receive(self, nonce: str) -> tuple[str, ReceiveStatus] | None:
        """Wait for the agent to become idle, then extract and validate the message.

        Returns:
            (body, ReceiveStatus.CONTINUE | ReceiveStatus.COMPLETE) on success
            ("", ReceiveStatus.DELIMITER_NOT_FOUND_WILL_RETRY) when no block found
            None on timeout or stop signal
        """
        content = self._capturer.wait_for_idle(self.session, self._stop)
        if content is None:
            return None
        self._turn += 1
        return self._parse_output(content, self._turn, nonce)

    def _parse_output(self, raw: str, turn: int, nonce: str) -> tuple[str, ReceiveStatus]:
        """Extract the last BEGIN/END delimited block matching turn, name, and nonce.

        Scans lines from the bottom: collect lines once END is found, stop and
        return once BEGIN is found.  Scanning from the bottom means the last
        complete block wins (handles retries naturally).  Delimiter matching uses
        'in' rather than startswith so that a leading '●' from Claude Code's output
        renderer does not break the match.
        """
        begin_tag = f"--- BEGIN {self.name} turn:{turn} nonce:{nonce}"
        end_tag = f"--- END {self.name} turn:{turn} nonce:{nonce}"

        buffer = []
        in_block = False

        for line in reversed(raw.splitlines()):
            stripped = line.strip()
            if not in_block:
                if end_tag.lower() in stripped.lower():
                    in_block = True
            else:
                if begin_tag.lower() in stripped.lower():
                    sm = re.search(r"status:(\w+)", stripped, re.IGNORECASE)
                    receive_status = (
                        ReceiveStatus.COMPLETE
                        if (sm and sm.group(1).lower() == ReceiveStatus.COMPLETE)
                        else ReceiveStatus.CONTINUE
                    )
                    body = "\n".join(reversed(buffer)).strip()
                    LOG.info("[%s] turn:%d status:%s body_len:%d", self.name, turn, receive_status, len(body))
                    return (body, receive_status)
                else:
                    buffer.append(line)

        LOG.warning("[%s] turn:%d nonce:%s — delimiters not found", self.name, turn, nonce)
        return ("", ReceiveStatus.DELIMITER_NOT_FOUND_WILL_RETRY)
