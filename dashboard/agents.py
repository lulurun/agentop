"""Agent definitions: base class + per-tool implementations.

Each agent encapsulates the behaviour specific to one CLI tool:
  - process detection (matches)
  - launch command
  - session metadata (ai title, remote info, …)
  - post-start hook (e.g. accepting trust prompts)

Add a new tool by subclassing BaseAgent and appending an instance to AGENTS.
"""

from __future__ import annotations

import json
import socket
import subprocess
import time
from pathlib import Path
from typing import Optional


class BaseAgent:
    """Interface every agent tool must implement."""

    name: str = ""  # e.g. "claude", "codex", "gemini"

    def matches(self, proc_name: str, cmdline: str) -> bool:
        """Return True if this process belongs to this agent type."""
        pattern = self.name
        name_lower = proc_name.lower()
        cmd_lower = cmdline.lower()
        if name_lower == pattern or name_lower.startswith(pattern + "-"):
            return True
        tokens = cmd_lower.split()
        if tokens:
            first = tokens[0].split("/")[-1]
            if first == pattern or first.startswith(pattern):
                return True
        return False

    @property
    def launch_cmd(self) -> str:
        """Shell command to launch the agent inside a tmux pane."""
        return self.name

    def get_session_meta(self, pid: int, cwd: str) -> dict:
        """Return agent-specific fields to merge into the session dict.

        Common keys: ai_title, bridge_session_id, bridge_url, …
        Return {} if this agent has no extra metadata.
        """
        return {}

    def post_start_hook(self, tmux_session: str) -> None:
        """Called after the agent process starts in tmux.

        Override to handle first-run prompts, auth flows, etc.
        """
        pass


class ClaudeAgent(BaseAgent):
    name = "claude"

    @property
    def launch_cmd(self) -> str:
        return "claude --dangerously-skip-permissions --remote-control"

    def get_session_meta(self, pid: int, cwd: str) -> dict:
        meta: dict = {}
        title = self._ai_title(pid, cwd)
        if title:
            meta["ai_title"] = title
        meta.update(self._remote_meta(pid))
        return meta

    def post_start_hook(self, tmux_session: str) -> None:
        """Accept Claude's trust / safety-check prompt automatically."""
        for _ in range(40):  # poll up to 10 s
            time.sleep(0.25)
            try:
                r = subprocess.run(
                    ["tmux", "capture-pane", "-t", tmux_session, "-p"],
                    capture_output=True, text=True, timeout=3,
                )
                content = r.stdout.lower()
                if "trust" in content or "safety check" in content:
                    time.sleep(0.1)
                    subprocess.run(
                        ["tmux", "send-keys", "-t", tmux_session, "Enter"],
                        capture_output=True, timeout=3,
                    )
                    return
            except (subprocess.TimeoutExpired, subprocess.SubprocessError):
                continue

    # ------------------------------------------------------------------ helpers

    def _ai_title(self, pid: int, cwd: str) -> Optional[str]:
        session_file = Path(f"~/.claude/sessions/{pid}.json").expanduser()
        if not session_file.exists():
            return None
        try:
            with open(session_file) as f:
                meta = json.load(f)
            session_id = meta.get("sessionId")
            if not session_id:
                return None
            slug = cwd.replace("/", "-")
            jsonl_path = Path(f"~/.claude/projects/{slug}/{session_id}.jsonl").expanduser()
            if not jsonl_path.exists():
                return None
            last_title = None
            with open(jsonl_path) as f:
                for line in f:
                    try:
                        obj = json.loads(line)
                        if obj.get("type") == "ai-title":
                            last_title = obj.get("aiTitle")
                    except json.JSONDecodeError:
                        continue
            return last_title
        except (OSError, json.JSONDecodeError):
            return None

    def _remote_meta(self, pid: int) -> dict:
        session_file = Path(f"~/.claude/sessions/{pid}.json").expanduser()
        if not session_file.exists():
            return {}
        try:
            with open(session_file) as f:
                meta = json.load(f)
            bridge_id = meta.get("bridgeSessionId")
            if not bridge_id:
                return {}
            hostname = socket.gethostname()
            raw = bridge_id.replace("session_", "")
            remote_name = (
                f"{hostname}-{raw[:4]}-{raw[4:8]}" if len(raw) >= 8 else f"{hostname}-{raw}"
            )
            return {
                "bridge_session_id": bridge_id,
                "bridge_url": f"https://claude.ai/code/{bridge_id}",
                "remote_name": remote_name,
                "claude_status": meta.get("status"),
            }
        except (OSError, json.JSONDecodeError):
            return {}


class CodexAgent(BaseAgent):
    name = "codex"

    @property
    def launch_cmd(self) -> str:
        return "codex"


class GeminiAgent(BaseAgent):
    name = "gemini"

    def matches(self, proc_name: str, cmdline: str) -> bool:
        name_lower = proc_name.lower()
        cmd_lower = cmdline.lower()
        if name_lower in ("gemini", "gemini-cli") or name_lower.startswith("gemini"):
            return True
        tokens = cmd_lower.split()
        if tokens:
            first = tokens[0].split("/")[-1]
            if first in ("gemini", "gemini-cli") or first.startswith("gemini"):
                return True
        return False

    @property
    def launch_cmd(self) -> str:
        return "gemini"


class OpenClawAgent(BaseAgent):
    name = "openclaw"


# ---------------------------------------------------------------------------
# Registry — order matters: more specific agents first
# ---------------------------------------------------------------------------

AGENTS: list[BaseAgent] = [
    ClaudeAgent(),
    CodexAgent(),
    GeminiAgent(),
    OpenClawAgent(),
]


def get_agent(name: str) -> Optional[BaseAgent]:
    """Return the registered agent with the given name, or None."""
    return next((a for a in AGENTS if a.name == name), None)
