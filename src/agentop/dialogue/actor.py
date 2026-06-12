"""Actor: one participant in a dialogue, owning its data and tmux operations."""

from __future__ import annotations

import logging
import re
import subprocess
import threading
import time

from agentop.dialogue.capturer import AgentCapturer, get_capturer, CodexCapturer
from agentop.tmux import CapturePane

LOG = logging.getLogger(__name__)

# Sentinel returned by receive() when the agent signals dialogue completion
DIALOGUE_COMPLETE = "<<DIALOGUE_COMPLETE>>"

_DELIMITER_RE = re.compile(r"---\s*output from \w+\s+turn:\d+\s*---", re.IGNORECASE)
_COMPLETE_RE = re.compile(r"---\s*complete from \w+\s+turn:\d+\s*---", re.IGNORECASE)


class Actor:
    def __init__(
        self,
        id: str,
        session: str,
        name: str = "",
        capturer: AgentCapturer | None = None,
        tool: str = "claude",
    ):
        self.id = id  # "a" or "b"
        self.session = session
        self.name = name or id.upper()
        self.tool = tool
        self._capturer = capturer or get_capturer(tool)
        self._stop: threading.Event | None = None

    def attach(self, stop_event: threading.Event) -> Actor:
        self._stop = stop_event
        return self

    def send(self, text: str) -> None:
        LOG.info("[%s] send start", self.name)
        start_time = time.monotonic()
        subprocess.run(["tmux", "load-buffer", "-"], input=text.encode(), capture_output=True, timeout=5)
        subprocess.run(["tmux", "paste-buffer", "-t", self.session], capture_output=True, timeout=5)
        
        if self.tool == "codex":
            # Wait a bit for the first chunk to paste
            time.sleep(2.0)
            
            # Send a "kick" Escape key to ensure the pty doesn't get stuck
            LOG.info("[%s] sending kick Escape for Codex", self.name)
            subprocess.run(["tmux", "send-keys", "-t", self.session, "Escape"], capture_output=True, timeout=5)
            
            # Now wait for screen stability (meaning paste has finished)
            last_content = CapturePane.screen(self.session)
            stable_since = time.monotonic()
            while time.monotonic() - stable_since < 1.0:
                time.sleep(0.1)
                cur = CapturePane.screen(self.session)
                if cur != last_content:
                    last_content = cur
                    stable_since = time.monotonic()
            LOG.info("[%s] screen stable. total paste wait: %.3f s", self.name, time.monotonic() - start_time)
            
            # Send Escape and Enter to submit the prompt
            LOG.info("[%s] sending Escape for Codex submission", self.name)
            subprocess.run(["tmux", "send-keys", "-t", self.session, "Escape"], capture_output=True, timeout=5)
            time.sleep(0.5)
            LOG.info("[%s] sending Enter for Codex submission", self.name)
            subprocess.run(["tmux", "send-keys", "-t", self.session, "Enter"], capture_output=True, timeout=5)
        else:
            # Wait until the terminal screen starts changing (pasting begins)
            original_content = CapturePane.screen(self.session)
            paste_start_time = time.monotonic()
            while time.monotonic() - paste_start_time < 2.0:
                time.sleep(0.05)
                if CapturePane.screen(self.session) != original_content:
                    LOG.info("[%s] paste detected after %.3f s", self.name, time.monotonic() - paste_start_time)
                    break
            else:
                LOG.info("[%s] paste start timeout reached", self.name)

            # Wait until the terminal screen stops changing (pasting finished)
            last_content = CapturePane.screen(self.session)
            stable_since = time.monotonic()
            while time.monotonic() - stable_since < 0.5:
                time.sleep(0.1)
                cur = CapturePane.screen(self.session)
                if cur != last_content:
                    last_content = cur
                    stable_since = time.monotonic()
            LOG.info("[%s] screen stable. total paste wait: %.3f s", self.name, time.monotonic() - start_time)
            
            LOG.info("[%s] sending Enter", self.name)
            subprocess.run(["tmux", "send-keys", "-t", self.session, "Enter"], capture_output=True, timeout=5)

    def receive(self) -> str | None:
        snapshot = CapturePane.scrollback(self.session)
        content = self._capturer.wait_for_idle(self.session, self._stop)
        if content is None:
            return None
        raw = self._capturer.extract_response(snapshot, content)
        if not raw:
            return None
        msg = self._parse_output(raw)
        if msg:
            LOG.info("[%s]: %s", self.name, msg)
        return msg or None

    def _parse_output(self, raw: str) -> str:
        """Return content after the delimiter, DIALOGUE_COMPLETE, or raw if absent."""
        lines = raw.splitlines()
        for i, line in enumerate(lines):
            if _COMPLETE_RE.search(line):
                LOG.info("[%s] complete delimiter found on line %d", self.name, i)
                return DIALOGUE_COMPLETE
            if _DELIMITER_RE.search(line):
                extracted = "\n".join(lines[i + 1 :]).strip()
                LOG.info("[%s] output delimiter found on line %d", self.name, i)
                return extracted
        LOG.warning("[%s] no delimiter found — using full raw response", self.name)
        return raw

    def to_dict(self) -> dict:
        return {"id": self.id, "session": self.session, "name": self.name, "tool": self.tool}

    @classmethod
    def from_dict(cls, d: dict) -> Actor:
        return cls(id=d["id"], session=d["session"], name=d.get("name", ""), tool=d.get("tool", "claude"))
