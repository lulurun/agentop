"""Actor: sends text to a tmux session and captures output after idle detection."""

from __future__ import annotations

import logging
import threading
import time

from agentop.tmux import CapturePane, Session

LOG = logging.getLogger(__name__)

_POLL_INTERVAL = 2.0
_TIMEOUT = 1800.0


class Actor:
    def __init__(self, session: str, name: str, idle_seconds: float = 5.0, use_bracketed_paste: bool = False):
        self.session = session
        self.name = name
        self.idle_seconds = idle_seconds
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
        """Wait for the screen to stabilise for idle_seconds, then return the full scrollback."""
        deadline = time.monotonic() + _TIMEOUT
        last_screen: str | None = None
        last_change = time.monotonic()

        LOG.info("[%s] wait_for_idle started", self.session)

        while not self._stop.is_set() and time.monotonic() < deadline:
            time.sleep(_POLL_INTERVAL)
            if self._stop.is_set():
                return None
            screen = CapturePane.screen(self.session)
            if screen != last_screen:
                last_screen = screen
                last_change = time.monotonic()
                LOG.info("[%s] screen changed", self.session)
            elif last_screen is not None and time.monotonic() - last_change >= self.idle_seconds:
                LOG.info("[%s] idle detected", self.session)
                return CapturePane.scrollback(self.session)

        LOG.info("[%s] wait_for_idle timeout/stopped", self.session)
        return None
