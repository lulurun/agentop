"""Process, tmux, git, and file scanning for agent sessions."""

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Optional

import psutil

AGENT_PATTERNS = ["claude", "codex", "openclaw"]

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
    name_lower = name.lower()
    cmd_lower = cmdline.lower()
    for pattern in AGENT_PATTERNS:
        if name_lower == pattern or name_lower.startswith(pattern + "-"):
            return pattern
        # Match when the pattern is the first token or a path component ending in the pattern
        tokens = cmd_lower.split()
        if tokens:
            first = tokens[0].split("/")[-1]
            if first == pattern or first.startswith(pattern):
                return pattern
    return None


def _should_ignore(cmdline: str) -> bool:
    for pat in IGNORE_CMDLINE_PATTERNS:
        if pat in cmdline:
            return True
    return False


def scan_processes() -> list[dict]:
    """Return list of detected agent process dicts."""
    results = []
    for proc in psutil.process_iter(
        ["pid", "ppid", "name", "cmdline", "create_time", "cpu_percent", "memory_info"]
    ):
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
                    "cpu_percent": info.get("cpu_percent") or 0.0,
                    "memory_mb": memory_mb,
                    "status": "running",
                }
            )
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
    return results


def refresh_cpu_percent(pids: list[int]) -> dict[int, float]:
    """Non-blocking CPU percent for a set of PIDs (returns 0 on first call per PID)."""
    result = {}
    for pid in pids:
        try:
            proc = psutil.Process(pid)
            result[pid] = proc.cpu_percent(interval=None)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            result[pid] = 0.0
    return result


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
                chain.append({"pid": current.pid, "name": current.name(), "cmdline": " ".join(current.cmdline() or [])})
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


def get_claude_ai_title(pid: int, cwd: str) -> Optional[str]:
    """Read the AI-generated conversation title for a Claude Code session."""
    sessions_dir = Path("~/.claude/sessions").expanduser()
    session_file = sessions_dir / f"{pid}.json"
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


def start_tmux_session(name: str, cwd: str) -> bool:
    """Create a detached tmux session. Returns True if created, False if already exists or unavailable."""
    try:
        r = subprocess.run(["tmux", "has-session", "-t", name], capture_output=True, timeout=3)
        if r.returncode == 0:
            return False  # already exists
        subprocess.run(["tmux", "new-session", "-d", "-s", name, "-c", cwd], timeout=5)
        return True
    except (FileNotFoundError, subprocess.TimeoutExpired, subprocess.SubprocessError):
        return False


def start_session_with_tool(tool: str, cwd: str) -> dict:
    """Start a tmux session, launch the tool inside it, wait for its PID, then rename to {tool}-{pid}.

    Returns {"ok": True, "name": ..., "pid": ..., "tmux_session": ...} or {"ok": False, "error": ...}.
    """
    import random
    import string
    import time

    rand_id = "".join(random.choices(string.ascii_lowercase + string.digits, k=6))
    temp_name = f"{tool}-tmp-{rand_id}"

    # Create detached tmux session
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

    # Send the tool command
    subprocess.run(
        ["tmux", "send-keys", "-t", temp_name, tool, "Enter"],
        capture_output=True, timeout=5,
    )

    # Get the pane's shell PID
    pane_pid = None
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

    # Poll up to 5 s for the tool process to appear as a child of the pane
    tool_pid = None
    if pane_pid:
        for _ in range(20):
            time.sleep(0.25)
            try:
                for child in psutil.Process(pane_pid).children(recursive=True):
                    try:
                        pname = child.name().lower()
                        cmd = " ".join(child.cmdline() or []).lower()
                        if pname == tool or pname.startswith(tool + "-") or cmd.startswith(tool):
                            tool_pid = child.pid
                            break
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        continue
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                break
            if tool_pid:
                break

    if tool_pid:
        final_name = f"{tool}-{tool_pid}"
        try:
            subprocess.run(
                ["tmux", "rename-session", "-t", temp_name, final_name],
                capture_output=True, timeout=3,
            )
        except subprocess.TimeoutExpired:
            final_name = temp_name  # keep temp name if rename fails
        return {"ok": True, "name": final_name, "pid": tool_pid, "tmux_session": final_name}

    # Couldn't detect PID — leave tmux session alive with temp name
    return {"ok": True, "name": temp_name, "pid": None, "tmux_session": temp_name}


def stop_session(cwd: str, tmux_name: str) -> dict:
    """Kill agent processes in cwd and the named tmux session. Returns a summary."""
    import signal
    import time

    killed_pids: list[int] = []
    targets = []

    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            if proc.cwd() != cwd:
                continue
            cmd = " ".join(proc.info["cmdline"] or [])
            pname = (proc.info["name"] or "").lower()
            if any(p in pname or p in cmd.lower() for p in AGENT_PATTERNS):
                targets.append(proc)
        except (psutil.AccessDenied, psutil.NoSuchProcess):
            continue

    for proc in targets:
        try:
            proc.send_signal(signal.SIGINT)
            time.sleep(0.3)
            proc.send_signal(signal.SIGINT)
            killed_pids.append(proc.pid)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    if killed_pids:
        time.sleep(2)
        for proc in targets:
            try:
                if proc.is_running() and proc.status() != psutil.STATUS_ZOMBIE:
                    proc.send_signal(signal.SIGTERM)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        time.sleep(1)
        for proc in targets:
            try:
                if proc.is_running() and proc.status() != psutil.STATUS_ZOMBIE:
                    proc.kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
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

    return {"killed_pids": killed_pids, "tmux_killed": tmux_killed}


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


def build_sessions(registry: dict) -> list[dict]:
    """Combine process scan + tmux + git + registry into unified session list."""
    procs = scan_processes()
    tmux_panes = scan_tmux()
    reg_sessions = registry.get("sessions", {})

    auto_sessions = []
    matched_registry_keys: set[str] = set()

    for proc in procs:
        tmux = map_process_to_tmux(proc["pid"], tmux_panes)
        git = get_git_info(proc["cwd"]) if proc["cwd"] else None

        # Find matching registry entry
        matched_key = None
        for key, entry in reg_sessions.items():
            # Match by tmux session name
            if tmux and (tmux["session"] == key or entry.get("tmux_session") == tmux["session"]):
                matched_key = key
                break
            # Match by cwd
            reg_cwd = entry.get("cwd", "")
            if reg_cwd:
                expanded = os.path.expanduser(reg_cwd)
                if proc["cwd"] and os.path.normpath(expanded) == os.path.normpath(proc["cwd"]):
                    matched_key = key
                    break

        reg_entry = reg_sessions.get(matched_key, {}) if matched_key else {}
        if matched_key:
            matched_registry_keys.add(matched_key)

        # Determine display name
        if matched_key:
            name = matched_key
        elif tmux:
            tmux_name = tmux["session"]
            if any(p in tmux_name.lower() for p in AGENT_PATTERNS):
                name = tmux_name
            else:
                name = f"{proc['tool']}-{proc['pid']}"
        else:
            name = f"{proc['tool']}-{proc['pid']}"

        ai_title = get_claude_ai_title(proc["pid"], proc["cwd"] or "") if proc.get("cwd") else None
        description = reg_entry.get("description", "")
        # Auto-populate description from AI title when user hasn't set one
        if ai_title and not description and matched_key:
            from dashboard import registry as _registry
            _registry.upsert_session(matched_key, {"description": ai_title})
            description = ai_title

        session = {
            **proc,
            "name": name,
            "tmux": tmux,
            "git": git,
            "ai_title": ai_title,
            "description": description,
            "cwd": reg_entry.get("cwd") or proc.get("cwd") or "",
            "registry_key": matched_key,
        }
        auto_sessions.append(session)

    # Clean up registry entries with no live process
    from dashboard import registry as _registry
    for key, entry in reg_sessions.items():
        if key in matched_registry_keys:
            continue
        # Kill tmux session if still alive
        tmux_name = entry.get("tmux_session") or key
        try:
            r = subprocess.run(["tmux", "has-session", "-t", tmux_name], capture_output=True, timeout=3)
            if r.returncode == 0:
                subprocess.run(["tmux", "kill-session", "-t", tmux_name], capture_output=True, timeout=5)
        except (FileNotFoundError, subprocess.TimeoutExpired, subprocess.SubprocessError):
            pass
        _registry.delete_session(key)

    return auto_sessions
