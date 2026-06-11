"""Agent output capturers: idle detection and response extraction per agent type."""

from __future__ import annotations

import logging
import subprocess
import threading
import time

LOG = logging.getLogger(__name__)

_POLL_INTERVAL = 2.0
_TIMEOUT = 600.0


def _capture_pane(session: str) -> str:
    try:
        r = subprocess.run(
            ["tmux", "capture-pane", "-t", session, "-p", "-S", "-"],
            capture_output=True, text=True, timeout=5,
        )
        return r.stdout if r.returncode == 0 else ""
    except (subprocess.TimeoutExpired, subprocess.SubprocessError, FileNotFoundError):
        return ""


class AgentCapturer:
    """Base class: subclasses implement idle detection and chrome stripping for a specific agent."""

    #: Seconds the indicator must be stable before declaring idle
    idle_seconds: float = 10.0

    def indicator_line(self, pane_lines: list[str]) -> str:
        """Return the line used to detect idle state."""
        raise NotImplementedError

    def strip_chrome(self, lines: list[str]) -> list[str]:
        """Remove UI chrome from the bottom of the captured lines."""
        return lines

    # ------------------------------------------------------------------
    # Public API used by Actor

    def wait_for_idle(self, session: str, stop_event: threading.Event) -> str | None:
        """Poll until agent is idle; return full pane content or None if cancelled."""
        deadline = time.monotonic() + _TIMEOUT
        last_content = _capture_pane(session)
        last_indicator = self.indicator_line(last_content.splitlines())
        last_change = time.monotonic()

        while not stop_event.is_set() and time.monotonic() < deadline:
            time.sleep(_POLL_INTERVAL)
            if stop_event.is_set():
                return None
            current = _capture_pane(session)
            indicator = self.indicator_line(current.splitlines())
            if indicator != last_indicator:
                last_indicator = indicator
                last_content = current
                last_change = time.monotonic()
            elif time.monotonic() - last_change >= self.idle_seconds:
                return current

        return None

    def extract_response(self, snapshot: str, current: str) -> str:
        """Extract new content added since snapshot, with chrome stripped."""
        snap_lines = snapshot.splitlines()
        cur_lines = current.splitlines()
        new_lines = cur_lines[len(snap_lines):]
        clean = self.strip_chrome(new_lines)
        return "\n".join(clean).strip()


class ClaudeCodeCapturer(AgentCapturer):
    """Capturer for Claude Code sessions running in tmux.

    Claude Code pane layout (from bottom):
      [-1] UI hint line  (e.g. "⏵⏵ bypass permissions …")
      [-2] separator ────
      [-3] echoed input  (❯ <our last message>)
      [-4] separator ────
      [-5] empty line
      [-6] activity indicator  (✻ Sautéed for 2m 10s)  ← watched for idle detection
    """

    idle_seconds: float = 10.0
    _CHROME_LINES = 5  # number of bottom lines to strip

    def indicator_line(self, pane_lines: list[str]) -> str:
        if len(pane_lines) >= 6:
            return pane_lines[-6]
        return pane_lines[-1] if pane_lines else ""

    def strip_chrome(self, lines: list[str]) -> list[str]:
        if len(lines) > self._CHROME_LINES:
            return lines[: -self._CHROME_LINES]
        return lines


def get_capturer(agent: str) -> AgentCapturer:
    """Return the right capturer for the given agent name."""
    if "claude" in agent.lower():
        return ClaudeCodeCapturer()
    # Fallback: whole-pane stability, no chrome stripping
    return AgentCapturer()
