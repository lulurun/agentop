"""Agent output capturer: idle detection and response extraction."""

from __future__ import annotations

import logging
import threading
import time

from agentop.tmux import CapturePane

LOG = logging.getLogger(__name__)

_POLL_INTERVAL = 2.0
_TIMEOUT = 600.0


class Capturer:
    """Poll the visible screen until stable, then return the full scrollback."""

    idle_seconds: float = 5.0

    def wait_for_idle(self, session: str, stop_event: threading.Event) -> str | None:
        """Poll screen until stable for idle_seconds; return full scrollback or None."""
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

    def extract_response(self, snapshot: str, current: str) -> str:
        """Return scrollback lines added after the snapshot."""
        snap_len = len(snapshot.splitlines())
        return "\n".join(current.splitlines()[snap_len:]).strip()
