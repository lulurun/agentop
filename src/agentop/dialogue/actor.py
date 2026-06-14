"""Actor: one participant in a dialogue, owning its data and tmux operations."""

from __future__ import annotations

import logging
import re
import threading

from agentop.dialogue.capturer import Capturer
from agentop.tmux import Session

LOG = logging.getLogger(__name__)

# Receive status values — embedded in BEGIN/END delimiter lines as status:VALUE
RECEIVE_CONTINUE = "continue"
RECEIVE_COMPLETE = "complete"
RECEIVE_PARSE_FAILURE = "parse_failure"  # no valid block found; orchestrator may retry
RECEIVE_PARSE_ERROR = "parse_error"  # retries exhausted; orchestrator halts


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

    def receive(self, nonce: str) -> tuple[str, str] | None:
        """Wait for the agent to become idle, then extract and validate the message.

        Returns:
            (body, RECEIVE_CONTINUE | RECEIVE_COMPLETE) on success
            ("", RECEIVE_PARSE_FAILURE) when no valid block found after waiting
            None on timeout or stop signal
        """
        content = self._capturer.wait_for_idle(self.session, self._stop)
        if content is None:
            return None
        self._turn += 1
        return self._parse_output(content, self._turn, nonce)

    def _parse_output(self, raw: str, turn: int, nonce: str) -> tuple[str, str]:
        """Extract the last BEGIN/END delimited block matching turn, name, and nonce.

        Uses rfind on the BEGIN line (which embeds the unique nonce) so that a
        re-sent block after a recovery prompt takes precedence over an earlier one.
        The END delimiter is optional: terminal UI spinners can overwrite the last
        line(s) of output in the tmux scrollback; when END is absent we fall back
        to taking content to the end of the buffer.
        """
        begin_prefix = f"--- BEGIN {self.name} turn:{turn} nonce:{nonce}"
        end_prefix = f"--- END {self.name} turn:{turn} nonce:{nonce}"
        raw_lower = raw.lower()

        idx = raw_lower.rfind(begin_prefix.lower())
        if idx == -1:
            LOG.warning("[%s] turn:%d nonce:%s — BEGIN delimiter not found", self.name, turn, nonce)
            return ("", RECEIVE_PARSE_FAILURE)

        # Extract the full BEGIN line to read the status
        line_end = raw.find("\n", idx)
        begin_line = raw[idx : line_end if line_end >= 0 else len(raw)]
        sm = re.search(r"status:(\w+)", begin_line, re.IGNORECASE)
        receive_status = RECEIVE_COMPLETE if (sm and sm.group(1).lower() == RECEIVE_COMPLETE) else RECEIVE_CONTINUE

        # Body starts on the line after BEGIN
        body_start = line_end + 1 if line_end >= 0 else len(raw)
        rest = raw[body_start:]

        end_idx = rest.lower().find(end_prefix.lower())
        if end_idx >= 0:
            body = rest[:end_idx].strip()
        else:
            # END overwritten by terminal spinner — fall back to end of buffer
            LOG.debug("[%s] turn:%d END delimiter missing, using content to end of buffer", self.name, turn)
            body = rest.strip()

        LOG.info("[%s] turn:%d receive_status:%s body_len:%d", self.name, turn, receive_status, len(body))
        return (body, receive_status)
