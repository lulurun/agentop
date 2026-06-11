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
    """Base class: subclasses implement idle detection and response extraction."""

    idle_seconds: float = 10.0

    def indicator_line(self, pane_lines: list[str]) -> str:
        """Return the line used to detect idle state (watched for stability)."""
        raise NotImplementedError

    def content_end(self, lines: list[str]) -> int:
        """Return the index where UI chrome begins (content lives before this index)."""
        return len(lines)

    def wait_for_idle(self, session: str, stop_event: threading.Event) -> str | None:
        """Poll until the indicator line is stable; return full pane content or None."""
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
        """Extract content added since snapshot by comparing content areas.

        The snapshot's chrome lines (from content_end onwards) get preserved
        verbatim at the same indices in the current scrollback.  Skip them so
        only truly new content is returned.
        """
        snap_lines = snapshot.splitlines()
        cur_lines = current.splitlines()
        snap_end = self.content_end(snap_lines)
        cur_end = self.content_end(cur_lines)

        # Skip snapshot chrome lines that were carried over into current scrollback
        skip = 0
        for s, c in zip(snap_lines[snap_end:], cur_lines[snap_end:]):
            if s == c:
                skip += 1
            else:
                break

        new_lines = cur_lines[snap_end + skip : cur_end]
        return "\n".join(new_lines).strip()


class ClaudeCodeCapturer(AgentCapturer):
    """Capturer for Claude Code (claude CLI) sessions running in tmux.

    Observed pane layout when idle (from bottom, trailing blanks omitted):
      ✻ Worked for Xs          ← activity indicator; searched dynamically
      (blank)
      ──── separator ────
      ❯ (empty or echoed input)
      ──── separator ────
      ⏵⏵ hint line
      (blank lines)

    Layout in initial/fresh state (no ✻ line):
      ──── separator ────
      ❯ suggestion text
      ──── separator ────
      ⏵⏵ hint line
      ...

    content_end() finds the ✻ line (or falls back to the separator before ❯),
    so extract_response compares content areas rather than raw line counts.
    """

    idle_seconds: float = 10.0

    def indicator_line(self, pane_lines: list[str]) -> str:
        """Search last 25 lines for the ✻ activity indicator."""
        for line in reversed(pane_lines[-25:]):
            if line.strip().startswith("✻"):  # ✻
                return line
        return ""

    def content_end(self, lines: list[str]) -> int:
        """Index where chrome begins: the ✻ line, or the separator before ❯."""
        # Primary: find ✻ indicator
        for i in range(len(lines) - 1, max(len(lines) - 25, -1), -1):
            if lines[i].strip().startswith("✻"):
                return i
        # Fallback: find the separator (───) that immediately precedes the ❯ prompt
        for i in range(len(lines) - 1, max(len(lines) - 25, -1), -1):
            s = lines[i].strip()
            if s.startswith("❯") and i > 0 and "─" in lines[i - 1]:  # ❯ and ─
                return i - 1
        return max(0, len(lines) - 8)


def get_capturer(agent: str) -> AgentCapturer:
    """Return the appropriate capturer for the given agent name."""
    if "claude" in agent.lower():
        return ClaudeCodeCapturer()
    # Fallback: whole-pane stability, no chrome stripping
    return AgentCapturer()
