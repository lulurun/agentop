"""Process, tmux, git, and file scanning for agent sessions."""

import os
import subprocess
import time
from pathlib import Path
from typing import Optional

import psutil

from dashboard.agents import AGENTS, get_agent

IGNORE_CMDLINE_PATTERNS = [
    "grep",
    "pgrep",
    "scanner.py",
    "dashboard/main.py",
    "agentop",
    "uvicorn",
]

AGENT_DIRS = [
    "~/.claude",
    "~/.codex",
    "~/.config/claude",
    "~/.config/codex",
    "~/.local/share/claude",
    "~/.local/share/codex",
]


def _detect_tool(name: str, cmdline: str) -> Optional[str]:
    for agent in AGENTS:
        if agent.matches(name, cmdline):
            return agent.name
    return None


def _should_ignore(cmdline: str) -> bool:
    for pat in IGNORE_CMDLINE_PATTERNS:
        if pat in cmdline:
            return True
    return False


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


def _get_ancestor_and_child_pids(pid: int) -> set[int]:
    """Collect all ancestor + child PIDs of a process."""
    pids: set[int] = set()
    try:
        proc = psutil.Process(pid)
        pids.add(pid)
        # Ancestors
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
        # Children
        try:
            for child in proc.children(recursive=True):
                pids.add(child.pid)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        pass
    return pids


def map_process_to_tmux(pid: int, tmux_panes: list[dict]) -> Optional[dict]:
    """Find the tmux pane that contains a process by ancestry matching."""
    if not tmux_panes:
        return None
    related_pids = _get_ancestor_and_child_pids(pid)
    for pane in tmux_panes:
        if pane["pane_pid"] and pane["pane_pid"] in related_pids:
            return {
                "session": pane["session_name"],
                "window": pane["window_name"],
                "pane": pane["pane_index"],
            }
    return None


def get_git_info(cwd: str) -> Optional[dict]:
    """Return git metadata for a directory, or None if not a git repo."""
    if not cwd or not os.path.isdir(cwd):
        return None
    try:
        r = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=cwd,
        )
        if r.returncode != 0:
            return None
        repo_root = r.stdout.strip()

        branch_r = subprocess.run(
            ["git", "branch", "--show-current"],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=cwd,
        )
        branch = branch_r.stdout.strip() if branch_r.returncode == 0 else None

        status_r = subprocess.run(
            ["git", "status", "--short"],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=cwd,
        )
        dirty = bool(status_r.stdout.strip()) if status_r.returncode == 0 else False

        log_r = subprocess.run(
            ["git", "log", "-1", "--oneline"],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=cwd,
        )
        latest_commit = log_r.stdout.strip() if log_r.returncode == 0 else None

        return {
            "repo_root": repo_root,
            "branch": branch,
            "dirty": dirty,
            "latest_commit": latest_commit,
        }
    except (subprocess.TimeoutExpired, subprocess.SubprocessError, FileNotFoundError):
        return None


def scan_agent_dirs(limit: int = 60) -> list[dict]:
    """Scan hidden agent directories and return recently modified file metadata."""
    files = []
    for dir_pattern in AGENT_DIRS:
        dir_path = Path(dir_pattern).expanduser()
        if not dir_path.exists():
            continue
        tool = "claude" if "claude" in str(dir_path) else "codex"
        try:
            for f in dir_path.rglob("*"):
                if not f.is_file():
                    continue
                try:
                    stat = f.stat()
                    suffix = f.suffix.lower()
                    if suffix == ".jsonl":
                        ftype = "jsonl"
                    elif suffix == ".json":
                        ftype = "json"
                    elif suffix in (".db", ".sqlite", ".sqlite3"):
                        ftype = "sqlite"
                    elif suffix == ".log":
                        ftype = "log"
                    else:
                        ftype = suffix[1:] if suffix else "file"
                    files.append(
                        {
                            "tool": tool,
                            "path": str(f),
                            "modified_time": stat.st_mtime,
                            "size_bytes": stat.st_size,
                            "type": ftype,
                        }
                    )
                except (PermissionError, OSError):
                    continue
        except (PermissionError, OSError):
            continue
    files.sort(key=lambda x: x["modified_time"], reverse=True)
    return files[:limit]


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


def start_session_with_tool(tool: str, cwd: str) -> dict:
    """Delegate to the agent's start_session() method."""
    agent = get_agent(tool)
    if not agent:
        return {"ok": False, "error": f"Unknown tool: {tool}"}
    return agent.start_session(cwd)


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
    """Send /exit to the agent session via tmux, then kill the tmux session. Returns a summary."""
    import time

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


def get_recent_project_files(cwd: str, limit: int = 10) -> list[str]:
    """Return paths of recently modified files in a project directory."""
    if not cwd or not os.path.isdir(cwd):
        return []
    try:
        result = subprocess.run(
            ["find", cwd, "-type", "f", "-not", "-path", "*/.git/*", "-printf", "%T@ %p\n"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return []
        entries = []
        for line in result.stdout.splitlines():
            parts = line.split(" ", 1)
            if len(parts) == 2:
                try:
                    entries.append((float(parts[0]), parts[1]))
                except ValueError:
                    continue
        entries.sort(reverse=True)
        return [p for _, p in entries[:limit]]
    except (subprocess.TimeoutExpired, subprocess.SubprocessError):
        return []


def build_sessions(descriptions: dict | None = None) -> list[dict]:
    """Derive session list from live processes and .claude/.codex files. Fully stateless.

    descriptions: optional {name: description} for user-saved notes on managed sessions.
    """
    if descriptions is None:
        descriptions = {}
    procs = scan_processes()
    tmux_panes = scan_tmux()

    sessions = []
    for proc in procs:
        tmux = map_process_to_tmux(proc["pid"], tmux_panes)
        git = get_git_info(proc["cwd"]) if proc["cwd"] else None

        # A session is managed by agentop when its tmux session name starts with "agentop_"
        managed = bool(tmux and tmux["session"].startswith("agentop_"))

        # Name: managed → tmux session name; otherwise tmux name (if agent-like) or {tool}-{pid}
        if managed:
            name = tmux["session"]
        elif tmux:
            tmux_name = tmux["session"]
            if any(a.name in tmux_name.lower() for a in AGENTS):
                name = tmux_name
            else:
                name = f"{proc['tool']}-{proc['pid']}"
        else:
            name = f"{proc['tool']}-{proc['pid']}"

        agent = get_agent(proc["tool"])
        session_meta = agent.get_session_meta(proc["pid"], proc["cwd"] or "") if agent and proc.get("cwd") else {}

        description = descriptions.get(name) or ""

        sessions.append(
            {
                **proc,
                "name": name,
                "tmux": tmux,
                "git": git,
                "description": description,
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
