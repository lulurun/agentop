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
            capture_output=True,
            text=True,
            timeout=5,
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

        LOG.info("[%s] wait_for_idle start indicator: [%s]", session, last_indicator)

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
                LOG.info("[%s] indicator change: [%s]", session, last_indicator)
            elif time.monotonic() - last_change >= self.idle_seconds:
                LOG.info("[%s] idle detected, last indicator: [%s]", session, last_indicator)
                return current

        LOG.info("[%s] wait_for_idle timeout/stopped, last indicator: [%s]", session, last_indicator)
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

        start = snap_end + skip
        new_lines = cur_lines[start:cur_end]
        return "\n".join(new_lines).strip()


class ClaudeCodeCapturer(AgentCapturer):
    """Capturer for Claude Code (claude CLI) sessions running in tmux.

    Claude Code pane structure (from bottom, searching upward):
      (blank lines)
      ⏵⏵ hint line
      ──── first separator ────
      ❯ prompt / echoed input   (ignored)
      ──── second separator ────
      (empty line)
      indicator line            ← watched for idle detection; content ends here
      (response content above)
    """

    idle_seconds: float = 5.0

    def _indicator_idx(self, lines: list[str]) -> int:
        """Return index of the indicator line via structural search, or -1."""
        sep_count = 0
        for i in range(len(lines) - 1, max(len(lines) - 25, -1), -1):
            if lines[i].startswith("─"):
                sep_count += 1
                if sep_count == 2:
                    # i is the second (upper) separator.
                    # Layout: [i-1] empty, [i-2] indicator.
                    if i >= 2 and lines[i - 1].strip() == "":
                        return i - 2
                    break
        return -1

    def indicator_line(self, pane_lines: list[str]) -> str:
        idx = self._indicator_idx(pane_lines)
        return pane_lines[idx] if idx >= 0 else ""

    def content_end(self, lines: list[str]) -> int:
        idx = self._indicator_idx(lines)
        return idx if idx >= 0 else max(0, len(lines) - 8)


def get_capturer(agent: str) -> AgentCapturer:
    """Return the appropriate capturer for the given agent name."""
    if "claude" in agent.lower():
        return ClaudeCodeCapturer()
    # Fallback: whole-pane stability, no chrome stripping
    return AgentCapturer()
