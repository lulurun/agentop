"""Actor: sends text to a tmux session and captures output after idle detection."""

from __future__ import annotations

import logging
import threading

from agentop.capturer import Capturer
from agentop.tmux import Session

LOG = logging.getLogger(__name__)


class Actor:
    def __init__(self, session: str, name: str, idle_seconds: float = 5.0, use_bracketed_paste: bool = False):
        self.session = session
        self.name = name
        self._capturer = Capturer(idle_seconds=idle_seconds)
        self._use_bracketed_paste = use_bracketed_paste
        self._stop: threading.Event | None = None

    def attach(self, stop_event: threading.Event) -> Actor:
        self._stop = stop_event
        return self

    def send(self, text: str) -> None:
        if self._use_bracketed_paste:
            Session.paste_text_bracketed(self.session, text)
        else:
            Session.paste_text(self.session, text)
        Session.send_keys(self.session, "Enter")

    def receive(self) -> str | None:
        """Wait for the agent to become idle and return the full scrollback, or None on timeout/stop."""
        return self._capturer.wait_for_idle(self.session, self._stop)
