"""Tmux pane capture and idle detection."""
from __future__ import annotations

import subprocess
import threading
import time

POLL_INTERVAL = 2.0
DEFAULT_IDLE_SECONDS = 12.0
DEFAULT_TIMEOUT = 600.0


def capture_pane(tmux_session: str) -> str:
    """Capture the full scrollback buffer of a tmux pane."""
    try:
        r = subprocess.run(
            ["tmux", "capture-pane", "-t", tmux_session, "-p", "-S", "-"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return r.stdout if r.returncode == 0 else ""
    except (subprocess.TimeoutExpired, subprocess.SubprocessError, FileNotFoundError):
        return ""


def wait_for_idle(
    tmux_session: str,
    stop_event: threading.Event,
    idle_seconds: float = DEFAULT_IDLE_SECONDS,
    timeout: float = DEFAULT_TIMEOUT,
) -> str | None:
    """
    Poll pane until content is stable for idle_seconds.
    Returns final content, or None if stop_event fires or timeout exceeded.
    """
    deadline = time.monotonic() + timeout
    last_content = capture_pane(tmux_session)
    last_change = time.monotonic()

    while not stop_event.is_set() and time.monotonic() < deadline:
        time.sleep(POLL_INTERVAL)
        if stop_event.is_set():
            return None
        current = capture_pane(tmux_session)
        if current != last_content:
            last_content = current
            last_change = time.monotonic()
        elif time.monotonic() - last_change >= idle_seconds:
            return last_content

    return None


