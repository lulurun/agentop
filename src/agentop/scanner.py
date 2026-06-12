"""Session orchestration: combines process, tmux, git, and file scanning.

Public API is re-exported here so existing callers (api.py, ops.py) need no changes.
"""

from __future__ import annotations

from agentop.agents import AGENTS, get_agent
from agentop.files import get_recent_project_files, scan_agent_dirs
from agentop.git import get_git_info
from agentop.process import get_ancestor_and_child_pids, get_process_tree, scan_processes
from agentop.tmux import scan_tmux, send_to_session, stop_session

__all__ = [
    "build_sessions",
    "get_process_tree",
    "get_recent_project_files",
    "map_process_to_tmux",
    "scan_agent_dirs",
    "send_to_session",
    "stop_session",
]


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
        git = get_git_info(proc["cwd"]) if proc["cwd"] else None

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
                "git": git,
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
