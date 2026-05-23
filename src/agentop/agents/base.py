from __future__ import annotations

import random
import string
import subprocess
import time
from typing import Optional

import psutil


class BaseAgent:
    """Interface and shared implementation for every supported agent tool."""

    name: str = ""  # "claude", "codex", "gemini", …

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

    def post_start_hook(self, tmux_session: str) -> None:
        """Called once after the agent process appears in tmux.

        Override to handle first-run prompts, auth flows, etc.
        """
        pass

    def start_session(self, cwd: str, short_name: str = "") -> dict:
        """Create a tmux session, launch the agent, wait for its PID, then
        rename the session to {short_name}-{pid}.

        Returns {"ok": True, "name": …, "pid": …, "tmux_session": …}
             or {"ok": False, "error": …}.
        """
        rand_id = "".join(random.choices(string.ascii_lowercase + string.digits, k=6))
        temp_name = f"agentop_tmp_{rand_id}"

        try:
            r = subprocess.run(
                ["tmux", "new-session", "-d", "-s", temp_name, "-c", cwd],
                capture_output=True, timeout=5,
            )
            if r.returncode != 0:
                return {"ok": False, "error": r.stderr.decode().strip() or "tmux failed"}
        except FileNotFoundError:
            return {"ok": False, "error": "tmux is not installed"}
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": "tmux timed out"}

        subprocess.run(
            ["tmux", "send-keys", "-t", temp_name, self.launch_cmd, "Enter"],
            capture_output=True, timeout=5,
        )

        # Resolve the shell PID of the new pane
        pane_pid: Optional[int] = None
        try:
            r = subprocess.run(
                ["tmux", "list-panes", "-t", temp_name, "-F", "#{pane_pid}"],
                capture_output=True, text=True, timeout=3,
            )
            pid_str = r.stdout.strip()
            if pid_str.isdigit():
                pane_pid = int(pid_str)
        except subprocess.TimeoutExpired:
            pass

        # Poll up to 5 s for the agent process to appear as a child of the pane
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
                    capture_output=True, timeout=3,
                )
            except subprocess.TimeoutExpired:
                final_name = temp_name
            self.post_start_hook(final_name)
            return {"ok": True, "name": final_name, "pid": tool_pid, "tmux_session": final_name}

        self.post_start_hook(temp_name)
        return {"ok": True, "name": temp_name, "pid": None, "tmux_session": temp_name}

    # ------------------------------------------------------------------ metadata

    def get_ai_title(self, pid: int, cwd: str) -> Optional[str]:
        """Return the AI-generated conversation title for this session, or None."""
        return None

    def get_extra_meta(self, pid: int, cwd: str) -> dict:
        """Return additional tool-specific fields to include in the session dict."""
        return {}

    def get_session_meta(self, pid: int, cwd: str) -> dict:
        """Merge get_ai_title() and get_extra_meta() into one dict."""
        meta = self.get_extra_meta(pid, cwd)
        title = self.get_ai_title(pid, cwd)
        if title:
            meta["ai_title"] = title
        return meta
