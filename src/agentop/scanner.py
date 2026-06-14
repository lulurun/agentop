"""Agent process detection and session orchestration."""

from __future__ import annotations

import subprocess
import time
from typing import Optional

import psutil

from agentop.agentclis import AGENTS, get_agent

__all__ = [
    "build_sessions",
    "get_process_tree",
    "map_process_to_tmux",
]

IGNORE_CMDLINE_PATTERNS = [
    "grep",
    "pgrep",
    "scanner.py",
    "dashboard/main.py",
    "agentop",
    "uvicorn",
]


def _detect_tool(name: str, cmdline: str) -> Optional[str]:
    for agent in AGENTS:
        if agent.matches(name, cmdline):
            return agent.name
    return None


def _should_ignore(cmdline: str) -> bool:
    return any(pat in cmdline for pat in IGNORE_CMDLINE_PATTERNS)


def scan_processes() -> list[dict]:
    """Return list of detected agent process dicts."""
    results = []
    for proc in psutil.process_iter(["pid", "ppid", "name", "cmdline", "create_time", "memory_info"]):
        try:
            info = proc.info
            cmdline_parts = info["cmdline"] or []
            cmdline = " ".join(cmdline_parts)
            name = info["name"] or ""

            if _should_ignore(cmdline):
                continue

            tool = _detect_tool(name, cmdline)
            if tool is None:
                continue

            try:
                cwd = proc.cwd()
            except (psutil.AccessDenied, psutil.NoSuchProcess):
                cwd = None

            mem_info = info["memory_info"]
            memory_mb = round(mem_info.rss / (1024 * 1024), 1) if mem_info else 0
            runtime_seconds = int(time.time() - info["create_time"])

            results.append(
                {
                    "pid": info["pid"],
                    "ppid": info["ppid"],
                    "tool": tool,
                    "cmdline": cmdline,
                    "cwd": cwd,
                    "runtime_seconds": runtime_seconds,
                    "memory_mb": memory_mb,
                    "status": "running",
                }
            )
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
    return results


def get_process_tree(pid: int) -> list[dict]:
    """Return process ancestry chain from root to the given process."""
    chain = []
    try:
        proc = psutil.Process(pid)
        current = proc
        while True:
            try:
                chain.append(
                    {"pid": current.pid, "name": current.name(), "cmdline": " ".join(current.cmdline() or [])}
                )
                parent = current.parent()
                if parent is None or parent.pid == current.pid or parent.pid == 0:
                    break
                current = parent
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                break
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        pass
    chain.reverse()
    return chain


def get_ancestor_and_child_pids(pid: int) -> set[int]:
    """Collect all ancestor + child PIDs of a process."""
    pids: set[int] = set()
    try:
        proc = psutil.Process(pid)
        pids.add(pid)
        current = proc
        while True:
            try:
                parent = current.parent()
                if parent is None or parent.pid in pids:
                    break
                pids.add(parent.pid)
                current = parent
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                break
        try:
            for child in proc.children(recursive=True):
                pids.add(child.pid)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        pass
    return pids


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
    """Derive session list from live processes and agent metadata. Fully stateless."""
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

    # Deduplicate by name: keep only the oldest (lowest PID) entry per name.
    seen: dict[str, dict] = {}
    for s in sessions:
        n = s["name"]
        if n not in seen or s["pid"] < seen[n]["pid"]:
            seen[n] = s
    return list(seen.values())
