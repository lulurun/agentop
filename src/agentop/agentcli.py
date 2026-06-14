from __future__ import annotations

import random
import string
import subprocess
import time
from typing import Optional

import psutil


class AgentCli:
    """Interface and shared implementation for every supported agent CLI tool."""

    name: str = ""  # "claude", "codex", "gemini", …

    # ------------------------------------------------------------------ actor behaviour
    idle_seconds: float = 5.0

    # ------------------------------------------------------------------ detection

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

    # ------------------------------------------------------------------ session lifecycle

    @property
    def launch_cmd(self) -> str:
        """Shell command to launch the agent inside a tmux pane."""
        return self.name

    def get_resume_cmd(self, session_id: str) -> Optional[str]:
        """Return the shell command to resume a saved session, or None if unsupported."""
        return None

    def post_start_hook(self, tmux_session: str) -> None:
        """Called once after the agent process appears in tmux."""
        pass

    def _build_cmd(self, base_cmd: str, params: dict) -> str:
        """Append agent-specific CLI flags derived from params."""
        cmd = base_cmd
        if "model" in params:
            cmd += f" --model {params['model']}"
        return cmd

    def _run_in_tmux(self, cmd: str, cwd: str, short_name: str = "", params: dict | None = None) -> dict:
        """Create a tmux session, run cmd, wait for the agent PID, rename the session."""
        if params:
            cmd = self._build_cmd(cmd, params)
        from agentop.tmux import Session

        rand_id = "".join(random.choices(string.ascii_lowercase + string.digits, k=6))
        temp_name = f"agentop_tmp_{rand_id}"

        if not Session.new(temp_name, cwd):
            return {"ok": False, "error": "tmux new-session failed"}

        Session.send_keys(temp_name, cmd, "Enter")

        pane_pid: Optional[int] = None
        lines = Session.list_panes(temp_name, "#{pane_pid}")
        if lines and lines[0].isdigit():
            pane_pid = int(lines[0])

        tool_pid: Optional[int] = None
        if pane_pid:
            for _ in range(20):
                time.sleep(0.25)
                try:
                    for child in psutil.Process(pane_pid).children(recursive=True):
                        try:
                            if self.matches(child.name(), " ".join(child.cmdline() or [])):
                                tool_pid = child.pid
                                break
                        except (psutil.NoSuchProcess, psutil.AccessDenied):
                            continue
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    break
                if tool_pid:
                    break

        if tool_pid:
            base = short_name if short_name else f"agentop_{self.name}"
            final_name = f"{base}-{tool_pid}"
            try:
                subprocess.run(
                    ["tmux", "rename-session", "-t", temp_name, final_name],
                    capture_output=True,
                    timeout=3,
                )
            except subprocess.TimeoutExpired:
                final_name = temp_name
            self.post_start_hook(final_name)
            return {"ok": True, "name": final_name, "pid": tool_pid, "tmux_session": final_name}

        self.post_start_hook(temp_name)
        return {"ok": True, "name": temp_name, "pid": None, "tmux_session": temp_name}

    def start_session(self, cwd: str, short_name: str = "", params: dict | None = None) -> dict:
        """Create a tmux session and launch the agent."""
        return self._run_in_tmux(self.launch_cmd, cwd, short_name, params)

    def resume_session(self, session_id: str, cwd: str, short_name: str = "") -> dict:
        """Resume a saved session in a new tmux window."""
        cmd = self.get_resume_cmd(session_id)
        if not cmd:
            return {"ok": False, "error": f"{self.name} does not support session resume"}
        return self._run_in_tmux(cmd, cwd, short_name)

    # ------------------------------------------------------------------ saved sessions

    def get_saved_sessions(self, limit: int = 20) -> list[dict]:
        """Return a list of saved/historical sessions that can be resumed."""
        return []

    def delete_session(self, session_id: str) -> None:
        """Permanently remove a saved session from its source storage."""
        raise NotImplementedError(f"{self.name} does not support session deletion")

    # ------------------------------------------------------------------ metadata

    def get_ai_title(self, pid: int, cwd: str) -> Optional[str]:
        return None

    def get_extra_meta(self, pid: int, cwd: str) -> dict:
        return {}

    def get_session_meta(self, pid: int, cwd: str) -> dict:
        meta = self.get_extra_meta(pid, cwd)
        title = self.get_ai_title(pid, cwd)
        if title:
            meta["ai_title"] = title
        return meta
