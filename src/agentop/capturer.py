"""Capturer: polls a tmux session for screen stability, returns scrollback on idle."""

from __future__ import annotations

import logging
import threading
import time

from agentop.tmux import CapturePane

LOG = logging.getLogger(__name__)

_POLL_INTERVAL = 2.0
_TIMEOUT = 1800.0


class Capturer:
    def __init__(self, idle_seconds: float = 5.0):
        self.idle_seconds = idle_seconds

    def wait_for_idle(self, session: str, stop_event: threading.Event) -> str | None:
        deadline = time.monotonic() + _TIMEOUT
        last_screen: str | None = None
        last_change = time.monotonic()

        LOG.info("[%s] wait_for_idle started", session)

        while not stop_event.is_set() and time.monotonic() < deadline:
            time.sleep(_POLL_INTERVAL)
            if stop_event.is_set():
                return None
            screen = CapturePane.screen(session)
            if screen != last_screen:
                last_screen = screen
                last_change = time.monotonic()
                LOG.info("[%s] screen changed", session)
            elif last_screen is not None and time.monotonic() - last_change >= self.idle_seconds:
                LOG.info("[%s] idle detected", session)
                return CapturePane.scrollback(session)

        LOG.info("[%s] wait_for_idle timeout/stopped", session)
        return None
