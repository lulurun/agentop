"""Actor: one participant in a dialogue, owning its data and tmux operations."""

from __future__ import annotations

import logging
import re
import threading

from agentop.dialogue.capturer import Capturer
from agentop.tmux import Session

LOG = logging.getLogger(__name__)

# <agentop_status>VALUE</agentop_status>
_STATUS_RE = re.compile(
    r"<agentop_status>(.*?)</agentop_status>",
    re.DOTALL | re.IGNORECASE,
)

# Receive status values — carried inside <agentop_status> tags
RECEIVE_OK = "ok"
RECEIVE_COMPLETE = "complete"
RECEIVE_PARSE_FAILURE = "parse_failure"  # no valid block found; orchestrator may retry
RECEIVE_PARSE_ERROR = "parse_error"  # retries exhausted; orchestrator halts

_CLOSE_TAG = "</agentop_message>"


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
        """Extract the last <agentop_message> block matching turn, name, and nonce.

        Searches by opening tag (which embeds the unique nonce) using rfind so
        that a re-sent block after a recovery prompt takes precedence.  The
        closing tag is optional: terminal UI spinners (e.g. Claude Code's
        '✻ Brewed for Ns') use cursor-up sequences that can overwrite the last
        line(s) of the agent's output, causing the closing tag to disappear
        from the tmux scrollback buffer.  When the closing tag is absent we
        fall back to taking content to the end of the buffer.
        """
        open_tag = f'<agentop_message turn="{turn}" from="{self.name}" nonce="{nonce}">'
        raw_lower = raw.lower()

        idx = raw_lower.rfind(open_tag.lower())
        if idx == -1:
            LOG.warning("[%s] turn:%d nonce:%s — opening tag not found", self.name, turn, nonce)
            return ("", RECEIVE_PARSE_FAILURE)

        content_start = idx + len(open_tag)
        rest = raw[content_start:]

        close_idx = rest.lower().find(_CLOSE_TAG.lower())
        if close_idx >= 0:
            body = rest[:close_idx].strip()
        else:
            # Closing tag overwritten by terminal spinner — use content to end
            LOG.debug("[%s] turn:%d closing tag missing, falling back to end of buffer", self.name, turn)
            body = rest.strip()

        receive_status = RECEIVE_OK
        sm = _STATUS_RE.search(body)
        if sm:
            receive_status = sm.group(1).strip().lower()
            body = _STATUS_RE.sub("", body).strip()

        LOG.info("[%s] turn:%d receive_status:%s body_len:%d", self.name, turn, receive_status, len(body))
        return (body, receive_status)
