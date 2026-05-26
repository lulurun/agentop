"""Tmux session scanning and control."""

from __future__ import annotations

import subprocess
import time
from typing import Optional

from agentop.agents import get_agent
from agentop.process import get_ancestor_and_child_pids


def scan_tmux() -> list[dict]:
    """Return list of tmux pane dicts."""
    try:
        result = subprocess.run(
            [
                "tmux",
                "list-panes",
                "-a",
                "-F",
                "#{session_name}|#{window_name}|#{pane_index}|#{pane_pid}|#{pane_current_path}|#{pane_current_command}",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return []
        panes = []
        for line in result.stdout.strip().splitlines():
            parts = line.split("|")
            if len(parts) == 6:
                pane_pid = int(parts[3]) if parts[3].isdigit() else None
                panes.append(
                    {
                        "session_name": parts[0],
                        "window_name": parts[1],
                        "pane_index": parts[2],
                        "pane_pid": pane_pid,
                        "pane_current_path": parts[4],
                        "pane_current_command": parts[5],
                    }
                )
        return panes
    except (FileNotFoundError, subprocess.TimeoutExpired, subprocess.SubprocessError):
        return []


def map_process_to_tmux(pid: int, tmux_panes: list[dict]) -> Optional[dict]:
    """Find the tmux pane that contains a process by ancestry matching."""
    if not tmux_panes:
        return None
    related_pids = get_ancestor_and_child_pids(pid)
    for pane in tmux_panes:
        if pane["pane_pid"] and pane["pane_pid"] in related_pids:
            return {
                "session": pane["session_name"],
                "window": pane["window_name"],
                "pane": pane["pane_index"],
            }
    return None


def send_to_session(tmux_session: str, text: str) -> dict:
    """Send text followed by Enter to the given tmux session."""
    try:
        r = subprocess.run(
            ["tmux", "send-keys", "-t", tmux_session, text, "Enter"],
            capture_output=True,
            timeout=5,
        )
        if r.returncode != 0:
            return {"ok": False, "error": r.stderr.decode().strip() or "tmux send-keys failed"}
        return {"ok": True}
    except FileNotFoundError:
        return {"ok": False, "error": "tmux is not installed"}
    except (subprocess.TimeoutExpired, subprocess.SubprocessError) as e:
        return {"ok": False, "error": str(e)}


def stop_session(cwd: str, tmux_name: str) -> dict:
    """Send /exit to the agent session via tmux, then kill the tmux session."""
    sent_exit = False
    if tmux_name:
        try:
            r = subprocess.run(["tmux", "has-session", "-t", tmux_name], capture_output=True, timeout=3)
            if r.returncode == 0:
                subprocess.run(
                    ["tmux", "send-keys", "-t", tmux_name, "/exit", "Enter"],
                    capture_output=True,
                    timeout=5,
                )
                sent_exit = True
                time.sleep(3)
        except (FileNotFoundError, subprocess.TimeoutExpired, subprocess.SubprocessError):
            pass

    tmux_killed = False
    if tmux_name:
        try:
            r = subprocess.run(["tmux", "has-session", "-t", tmux_name], capture_output=True, timeout=3)
            if r.returncode == 0:
                subprocess.run(["tmux", "kill-session", "-t", tmux_name], timeout=5)
                tmux_killed = True
        except (FileNotFoundError, subprocess.TimeoutExpired, subprocess.SubprocessError):
            pass

    return {"sent_exit": sent_exit, "tmux_killed": tmux_killed}


def start_session_with_tool(tool: str, cwd: str, short_name: str = "") -> dict:
    """Delegate to the agent's start_session() method."""
    agent = get_agent(tool)
    if not agent:
        return {"ok": False, "error": f"Unknown tool: {tool}"}
    return agent.start_session(cwd, short_name)
