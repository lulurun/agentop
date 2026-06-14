"""Session orchestration: combines process, tmux, git, and file scanning.

Public API is re-exported here so existing callers (api.py, ops.py) need no changes.
"""

from __future__ import annotations

import subprocess

from agentop.agentclis import AGENTS, get_agent
from agentop.process import (
    get_ancestor_and_child_pids,
    get_process_tree,
    scan_processes,
)
from agentop.tmux import Session

__all__ = [
    "build_sessions",
    "get_process_tree",
    "map_process_to_tmux",
    "send_to_session",
]


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


def send_to_session(tmux_session: str, text: str) -> dict:
    """Send text followed by Enter to the given tmux session."""
    Session.send_keys(tmux_session, text, "Enter")
    return {"ok": True}



def map_process_to_tmux(pid: int, tmux_panes: list[dict]) -> dict | None:
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


def build_sessions(managed_names: set | None = None) -> list[dict]:
    """Derive session list from live processes and agent metadata. Fully stateless.

    managed_names:  set of session names registered as managed in the registry.
    """
    if managed_names is None:
        managed_names = set()
    procs = scan_processes()
    tmux_panes = scan_tmux()

    sessions = []
    for proc in procs:
        tmux = map_process_to_tmux(proc["pid"], tmux_panes)
        tmux_session = (tmux or {}).get("session", "")
        managed = bool(tmux_session.startswith("agentop_") or tmux_session in managed_names)

        if managed:
            name = tmux["session"]
        elif tmux:
            tmux_name = tmux["session"]
            name = tmux_name if any(a.name in tmux_name.lower() for a in AGENTS) else f"{proc['tool']}-{proc['pid']}"
        else:
            name = f"{proc['tool']}-{proc['pid']}"

        agent = get_agent(proc["tool"])
        session_meta = agent.get_session_meta(proc["pid"], proc["cwd"] or "") if agent and proc.get("cwd") else {}

        sessions.append(
            {
                **proc,
                "name": name,
                "tmux": tmux,
                "cwd": proc.get("cwd") or "",
                "managed": managed,
                **session_meta,
            }
        )

    # Deduplicate by name: tools like Gemini spawn a child node process that also
    # matches; keep only the oldest (lowest PID) entry per name.
    seen: dict[str, dict] = {}
    for s in sessions:
        n = s["name"]
        if n not in seen or s["pid"] < seen[n]["pid"]:
            seen[n] = s
    return list(seen.values())
