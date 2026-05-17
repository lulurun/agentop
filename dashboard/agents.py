"""Agent definitions: base class + per-tool implementations.

BaseAgent defines the shared interface and provides the generic tmux session
start logic.  Each subclass overrides only what is tool-specific:

  - matches()         — process detection
  - launch_cmd        — shell command to run inside tmux
  - get_ai_title()    — read the AI-generated conversation title from disk
  - get_extra_meta()  — any additional tool-specific fields (e.g. remote-control info)
  - post_start_hook() — first-run setup after the process appears (e.g. trust prompt)

get_session_meta() is implemented once in BaseAgent and calls get_ai_title() +
get_extra_meta() so subclasses never have to remember to populate "ai_title".

start_session() is also implemented once in BaseAgent: it handles the generic
tmux lifecycle (create temp session → send launch_cmd → wait for PID → rename
→ call post_start_hook).

To add a new tool: subclass BaseAgent, override what differs, append an instance
to AGENTS.
"""

from __future__ import annotations

import json
import os
import random
import socket
import sqlite3
import string
import subprocess
import time
from pathlib import Path
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

    def start_session(self, cwd: str) -> dict:
        """Create a tmux session, launch the agent, wait for its PID, then
        rename the session to agentop_{name}_{pid}.

        Returns {"ok": True, "name": …, "pid": …, "tmux_session": …}
             or {"ok": False, "error": …}.
        """
        rand_id = "".join(random.choices(string.ascii_lowercase + string.digits, k=6))
        temp_name = f"agentop_{self.name}_tmp_{rand_id}"

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
            final_name = f"agentop_{self.name}_{tool_pid}"
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
        """Return the AI-generated conversation title for this session, or None.

        Each agent reads from its own storage format.
        """
        return None

    def get_extra_meta(self, pid: int, cwd: str) -> dict:
        """Return additional tool-specific fields to include in the session dict.

        Override for things like remote-control bridge info.
        """
        return {}

    def get_session_meta(self, pid: int, cwd: str) -> dict:
        """Merge get_ai_title() and get_extra_meta() into one dict.

        Subclasses should override get_ai_title / get_extra_meta, not this method.
        """
        meta = self.get_extra_meta(pid, cwd)
        title = self.get_ai_title(pid, cwd)
        if title:
            meta["ai_title"] = title
        return meta


# ---------------------------------------------------------------------------
# Claude
# ---------------------------------------------------------------------------

class ClaudeAgent(BaseAgent):
    name = "claude"

    @property
    def launch_cmd(self) -> str:
        return "claude --dangerously-skip-permissions --remote-control"

    def post_start_hook(self, tmux_session: str) -> None:
        """Accept Claude's trust / safety-check prompt automatically."""
        for _ in range(40):
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

    def _get_jsonl_path(self, pid: int, cwd: str) -> Optional[Path]:
        """Resolve the JSONL conversation file for a Claude session, or None."""
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
            return jsonl_path if jsonl_path.exists() else None
        except (OSError, json.JSONDecodeError):
            return None

    def _read_token_usage(self, jsonl_path: Path) -> Optional[dict]:
        """Sum token usage across unique assistant messages in a JSONL file."""
        try:
            seen_ids: set = set()
            totals = {
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
            }
            with open(jsonl_path) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                        if obj.get("type") != "assistant":
                            continue
                        msg = obj.get("message", {})
                        msg_id = msg.get("id")
                        if not msg_id or msg_id in seen_ids:
                            continue
                        seen_ids.add(msg_id)
                        usage = msg.get("usage", {})
                        for k in totals:
                            totals[k] += usage.get(k, 0)
                    except json.JSONDecodeError:
                        continue
            return totals if seen_ids else None
        except OSError:
            return None

    def get_ai_title(self, pid: int, cwd: str) -> Optional[str]:
        """Read the AI-generated title from ~/.claude/projects/{slug}/{session_id}.jsonl."""
        jsonl_path = self._get_jsonl_path(pid, cwd)
        if not jsonl_path:
            return None
        try:
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
        except OSError:
            return None

    def get_extra_meta(self, pid: int, cwd: str) -> dict:
        """Read remote-control bridge info and token usage from ~/.claude/sessions/{pid}.json."""
        session_file = Path(f"~/.claude/sessions/{pid}.json").expanduser()
        result: dict = {}
        if session_file.exists():
            try:
                with open(session_file) as f:
                    meta = json.load(f)
                bridge_id = meta.get("bridgeSessionId")
                if bridge_id:
                    hostname = socket.gethostname()
                    raw = bridge_id.replace("session_", "")
                    remote_name = (
                        f"{hostname}-{raw[:4]}-{raw[4:8]}" if len(raw) >= 8 else f"{hostname}-{raw}"
                    )
                    result.update({
                        "bridge_session_id": bridge_id,
                        "bridge_url": f"https://claude.ai/code/{bridge_id}",
                        "remote_name": remote_name,
                        "claude_status": meta.get("status"),
                    })
            except (OSError, json.JSONDecodeError):
                pass

        jsonl_path = self._get_jsonl_path(pid, cwd)
        if jsonl_path:
            token_usage = self._read_token_usage(jsonl_path)
            if token_usage:
                result["token_usage"] = token_usage

        return result


# ---------------------------------------------------------------------------
# Codex
# ---------------------------------------------------------------------------

class CodexAgent(BaseAgent):
    name = "codex"

    # Tolerance for matching process start time to thread created_at_ms (30 seconds)
    _START_TOLERANCE_MS = 30_000

    @property
    def launch_cmd(self) -> str:
        return "codex"

    def _find_thread(self, pid: int, cwd: str) -> Optional[dict]:
        """Query state_5.sqlite for the thread matching this process by cwd + start time.

        Returns a dict with title, first_user_message, tokens_used, rollout_path,
        or None if no close match is found.
        """
        db_path = Path("~/.codex/state_5.sqlite").expanduser()
        if not db_path.exists():
            return None
        try:
            start_ms = int(psutil.Process(pid).create_time() * 1000)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return None
        try:
            with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2) as conn:
                conn.row_factory = sqlite3.Row
                cur = conn.execute(
                    """
                    SELECT title, first_user_message, tokens_used, rollout_path, created_at_ms
                    FROM threads
                    WHERE cwd = ? AND (archived IS NULL OR archived = 0)
                    ORDER BY ABS(created_at_ms - ?) ASC
                    LIMIT 1
                    """,
                    (os.path.normpath(cwd), start_ms),
                )
                row = cur.fetchone()
        except sqlite3.Error:
            return None
        if row is None:
            return None
        if abs(row["created_at_ms"] - start_ms) > self._START_TOLERANCE_MS:
            return None
        return dict(row)

    def _read_rollout_token_usage(self, rollout_path: str) -> Optional[dict]:
        """Scan a rollout JSONL for the last token_count event and return token usage."""
        try:
            path = Path(rollout_path)
            if not path.exists():
                return None
            last_usage: Optional[dict] = None
            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                        payload = obj.get("payload") or {}
                        if obj.get("type") == "event_msg" and payload.get("type") == "token_count":
                            total = (payload.get("info") or {}).get("total_token_usage")
                            if total:
                                last_usage = total
                    except json.JSONDecodeError:
                        continue
            if not last_usage:
                return None
            return {
                "input_tokens": last_usage.get("input_tokens", 0),
                "output_tokens": last_usage.get("output_tokens", 0),
                "cache_read_input_tokens": last_usage.get("cached_input_tokens", 0),
                "cache_creation_input_tokens": 0,
            }
        except OSError:
            return None

    def get_ai_title(self, pid: int, cwd: str) -> Optional[str]:
        """Read the thread title from state_5.sqlite, matched by PID start time."""
        thread = self._find_thread(pid, cwd)
        if not thread:
            return None
        title = thread.get("title")
        if title:
            return title
        first_msg = (thread.get("first_user_message") or "").strip()
        if first_msg:
            return first_msg[:80] + ("…" if len(first_msg) > 80 else "")
        return None

    def get_extra_meta(self, pid: int, cwd: str) -> dict:
        """Read token usage from the rollout file identified via state_5.sqlite."""
        thread = self._find_thread(pid, cwd)
        if not thread or not thread.get("rollout_path"):
            return {}
        token_usage = self._read_rollout_token_usage(thread["rollout_path"])
        if not token_usage:
            return {}
        return {"token_usage": token_usage}


# ---------------------------------------------------------------------------
# Gemini CLI
# ---------------------------------------------------------------------------

class GeminiAgent(BaseAgent):
    name = "gemini"

    def matches(self, proc_name: str, cmdline: str) -> bool:
        name_lower = proc_name.lower()
        cmd_lower = cmdline.lower()
        if name_lower in ("gemini", "gemini-cli") or name_lower.startswith("gemini"):
            return True
        # Gemini CLI runs as `node /path/to/bin/gemini`, so check all tokens
        for token in cmd_lower.split():
            base = token.split("/")[-1]
            if base in ("gemini", "gemini-cli") or base.startswith("gemini-"):
                return True
        return False

    @property
    def launch_cmd(self) -> str:
        return "gemini"

    def _find_project_dir(self, cwd: str) -> Optional[Path]:
        """Return ~/.gemini/tmp/<name>/ whose .project_root matches cwd."""
        tmp_base = Path("~/.gemini/tmp").expanduser()
        if not tmp_base.exists():
            return None
        try:
            for project_dir in tmp_base.iterdir():
                root_file = project_dir / ".project_root"
                if not root_file.exists():
                    continue
                try:
                    root = root_file.read_text().strip()
                    if os.path.normpath(root) == os.path.normpath(cwd):
                        return project_dir
                except OSError:
                    continue
        except OSError:
            pass
        return None

    def get_ai_title(self, pid: int, cwd: str) -> Optional[str]:
        """Gemini CLI does not currently persist conversation titles to disk."""
        return None

    def get_extra_meta(self, pid: int, cwd: str) -> dict:
        """Read token usage from the most-recent Gemini chat session for this cwd."""
        project_dir = self._find_project_dir(cwd)
        if not project_dir:
            return {}
        chats_dir = project_dir / "chats"
        if not chats_dir.exists():
            return {}
        try:
            session_files = sorted(
                list(chats_dir.glob("session-*.json")) + list(chats_dir.glob("session-*.jsonl")),
                key=lambda f: f.stat().st_mtime,
                reverse=True,
            )
        except OSError:
            return {}
        if not session_files:
            return {}

        for session_file in session_files:
            try:
                if session_file.suffix == ".jsonl":
                    messages = []
                    with open(session_file) as f:
                        for i, line in enumerate(f):
                            if i == 0:
                                continue  # skip header line
                            line = line.strip()
                            if line:
                                messages.append(json.loads(line))
                else:
                    with open(session_file) as f:
                        messages = json.load(f).get("messages", [])
            except (OSError, json.JSONDecodeError):
                continue

            totals = {
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 0,
            }
            for msg in messages:
                t = msg.get("tokens") or {}
                totals["input_tokens"] += t.get("input", 0)
                totals["output_tokens"] += t.get("output", 0) + t.get("thoughts", 0) + t.get("tool", 0)
                totals["cache_read_input_tokens"] += t.get("cached", 0)

            if any(totals.values()):
                return {"token_usage": totals}

        return {}


# ---------------------------------------------------------------------------
# Registry — order matters: more specific entries first
# ---------------------------------------------------------------------------

AGENTS: list[BaseAgent] = [
    ClaudeAgent(),
    CodexAgent(),
    GeminiAgent(),
]


def get_agent(name: str) -> Optional[BaseAgent]:
    """Return the registered agent with the given name, or None."""
    return next((a for a in AGENTS if a.name == name), None)
