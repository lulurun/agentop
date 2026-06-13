"""Actor: one participant in a dialogue, owning its data and tmux operations."""

from __future__ import annotations

import logging
import re
import threading

from agentop.dialogue.capturer import Capturer
from agentop.tmux import Session

LOG = logging.getLogger(__name__)

# <agentop_message turn="N" from="NAME" nonce="HEX">...</agentop_message>
_MSG_RE = re.compile(
    r'<agentop_message\s+turn="(\d+)"\s+from="([^"]+)"\s+nonce="([0-9a-f]+)"\s*>'
    r"(.*?)"
    r"</agentop_message>",
    re.DOTALL | re.IGNORECASE,
)

# <agentop_status>VALUE</agentop_status>
_STATUS_RE = re.compile(
    r"<agentop_status>(.*?)</agentop_status>",
    re.DOTALL | re.IGNORECASE,
)

# Receive status values — carried inside <agentop_status> tags
RECEIVE_OK = "ok"
RECEIVE_COMPLETE = "complete"
RECEIVE_PARSE_FAILURE = "parse_failure"  # no valid block found; orchestrator may retry
RECEIVE_PARSE_ERROR = "parse_error"      # retries exhausted; orchestrator halts


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
            (body, RECEIVE_OK | RECEIVE_COMPLETE) on success
            ("", RECEIVE_PARSE_FAILURE) when no valid block found after waiting
            None on timeout or stop signal
        """
        content = self._capturer.wait_for_idle(self.session, self._stop)
        if content is None:
            return None
        self._turn += 1
        return self._parse_output(content, self._turn, nonce)

    def _parse_output(self, raw: str, turn: int, nonce: str) -> tuple[str, str]:
        """Extract the last valid <agentop_message> block matching turn, name, and nonce."""
        body = None
        receive_status = RECEIVE_OK

        for m in _MSG_RE.finditer(raw):
            msg_turn = int(m.group(1))
            msg_from = m.group(2)
            msg_nonce = m.group(3)
            msg_body = m.group(4).strip()

            if msg_turn != turn:
                continue
            if msg_from.lower() != self.name.lower():
                continue
            if msg_nonce != nonce:
                continue

            body = msg_body

            # Look for a status tag inside this message block
            sm = _STATUS_RE.search(msg_body)
            if sm:
                receive_status = sm.group(1).strip().lower()
                # Remove the status tag from the body
                body = _STATUS_RE.sub("", msg_body).strip()

        if body is None:
            LOG.warning("[%s] turn:%d nonce:%s — no valid message found", self.name, turn, nonce)
            return ("", RECEIVE_PARSE_FAILURE)

        LOG.info("[%s] turn:%d receive_status:%s body_len:%d", self.name, turn, receive_status, len(body))
        return (body, receive_status)
