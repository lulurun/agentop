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
        """Return scrollback lines added after the snapshot.

        BEGIN/END delimiters in the output mean content_end() is no longer
        needed for extraction — _parse_output() locates the content exactly.
        """
        snap_len = len(snapshot.splitlines())
        return "\n".join(current.splitlines()[snap_len:]).strip()


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


def get_capturer(agent: str) -> AgentCapturer:
    """Return the appropriate capturer for the given agent name."""
    if "claude" in agent.lower():
        return ClaudeCodeCapturer()
    if "codex" in agent.lower():
        return CodexCapturer()
    if "antigravity" in agent.lower() or "agy" in agent.lower():
        return AntigravityCapturer()
    return AgentCapturer()
