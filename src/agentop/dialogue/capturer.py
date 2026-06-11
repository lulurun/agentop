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
      ──── first separator ────     ← sep1
      ❯ prompt / echoed input
      ──── second separator ────    ← sep2
      (empty line)                  ← stable anchor  [sep2 - 1]
      indicator line(s)             ← last one is at anchor - 1; may wrap upward
      (response content above)

    idle detection : watch a 5-line buffer above the anchor (handles wrapping)
    content_end    : anchor - 1  (the last indicator line; content lives above it)
    """

    idle_seconds: float = 5.0
    _INDICATOR_BUF = 5  # lines above anchor watched as indicator block

    def _anchor(self, lines: list[str]) -> int:
        """Return the index of the empty line above sep2 (stable anchor), or -1."""
        sep_count = 0
        for i in range(len(lines) - 1, max(len(lines) - 40, -1), -1):
            if lines[i].startswith("─"):
                sep_count += 1
                if sep_count == 2:
                    empty_idx = i - 1
                    if empty_idx >= 0 and lines[empty_idx].strip() == "":
                        return empty_idx
                    return -1
        return -1

    def indicator_line(self, pane_lines: list[str]) -> str:
        """5-line block above the anchor, joined — stable when agent is idle."""
        anchor = self._anchor(pane_lines)
        if anchor < 0:
            return ""
        start = max(0, anchor - self._INDICATOR_BUF)
        return "\n".join(pane_lines[start:anchor])

    def content_end(self, lines: list[str]) -> int:
        """Cut just above the indicator: anchor - 1 excludes the indicator line(s)."""
        anchor = self._anchor(lines)
        if anchor < 0:
            return max(0, len(lines) - 8)
        return max(0, anchor - 1)


def get_capturer(agent: str) -> AgentCapturer:
    """Return the appropriate capturer for the given agent name."""
    if "claude" in agent.lower():
        return ClaudeCodeCapturer()
    # Fallback: whole-pane stability, no chrome stripping
    return AgentCapturer()
