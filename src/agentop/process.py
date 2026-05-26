"""Live agent process detection and scanning."""

from __future__ import annotations

import time
from typing import Optional

import psutil

from agentop.agents import AGENTS, get_agent

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
