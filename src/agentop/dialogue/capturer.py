"""Agent output capturers: idle detection and response extraction per agent type."""

from __future__ import annotations

import logging
import threading
import time

from agentop.tmux import CapturePane

LOG = logging.getLogger(__name__)

_POLL_INTERVAL = 2.0
_TIMEOUT = 600.0


class AgentCapturer:
    """Base class: subclasses implement content_end and extract_response.

    Idle detection is shared: poll the visible screen every 2 s and declare
    idle once the content has been unchanged for idle_seconds.  No agent-
    specific indicator parsing — whole-screen stability is the signal.
    """

    idle_seconds: float = 5.0

    def content_end(self, lines: list[str]) -> int:
        """Return the index where UI chrome begins (content lives before this index)."""
        return len(lines)

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
        return "\n".join(cur_lines[start:cur_end]).strip()


class ClaudeCodeCapturer(AgentCapturer):
    """Capturer for Claude Code (claude CLI) sessions.

    content_end: structural search for the empty anchor above sep2 to exclude
                 the chrome region without cutting into response content.
    """

    def _anchor(self, lines: list[str]) -> int:
        """Index of the empty line above sep2 (stable structural anchor), or -1."""
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

    def content_end(self, lines: list[str]) -> int:
        anchor = self._anchor(lines)
        if anchor >= 0:
            return max(0, anchor - 1)
        return max(0, len(lines) - 8)


class AntigravityCapturer(AgentCapturer):
    """Capturer for Antigravity-cli (agy) sessions.

    content_end: the upper separator line above the input prompt.
    """

    def content_end(self, lines: list[str]) -> int:
        for i in range(len(lines) - 1, max(len(lines) - 8, -1), -1):
            if lines[i].startswith("─"):
                upper = i - 2
                if upper >= 0 and lines[upper].startswith("─"):
                    return upper
                break
        return max(0, len(lines) - 4)


class CodexCapturer(AgentCapturer):
    """Capturer for Codex (codex-cli) sessions.

    content_end: the last input prompt line starting with '›'.
    """

    def content_end(self, lines: list[str]) -> int:
        start_idx = max(0, len(lines) - 15)
        for i in range(len(lines) - 1, start_idx - 1, -1):
            if lines[i].strip().startswith("›"):
                return i
        return len(lines)

    def extract_response(self, snapshot: str, current: str) -> str:
        raw = super().extract_response(snapshot, current)
        # Codex echoes the submitted prompt at the start of its output area — strip it
        lines = raw.splitlines()
        if lines and lines[0].strip().startswith("›"):
            lines = lines[1:]
        return "\n".join(lines).strip()


def get_capturer(agent: str) -> AgentCapturer:
    """Return the appropriate capturer for the given agent name."""
    if "claude" in agent.lower():
        return ClaudeCodeCapturer()
    if "codex" in agent.lower():
        return CodexCapturer()
    if "antigravity" in agent.lower() or "agy" in agent.lower():
        return AntigravityCapturer()
    return AgentCapturer()
