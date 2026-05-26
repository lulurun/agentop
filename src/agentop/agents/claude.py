from __future__ import annotations

import json
import socket
import subprocess
import time
from pathlib import Path
from typing import Optional

from agentop.agents.base import BaseAgent

_PROJECTS_DIR = Path("~/.claude/projects")


class ClaudeAgent(BaseAgent):
    name = "claude"

    @property
    def launch_cmd(self) -> str:
        return "claude --dangerously-skip-permissions --remote-control"

    def get_resume_cmd(self, session_id: str) -> str:
        return f"claude --resume {session_id} --dangerously-skip-permissions --remote-control"

    def get_saved_sessions(self, limit: int = 20) -> list[dict]:
        projects_dir = _PROJECTS_DIR.expanduser()
        if not projects_dir.exists():
            return []
        sessions = []
        for proj_dir in projects_dir.iterdir():
            if not proj_dir.is_dir():
                continue
            for f in proj_dir.glob("*.jsonl"):
                title = None
                cwd = None
                try:
                    with open(f) as fp:
                        for line in fp:
                            try:
                                obj = json.loads(line)
                                t = obj.get("type")
                                if t == "ai-title" and not title:
                                    title = obj.get("aiTitle")
                                if t in ("system", "user") and not cwd:
                                    cwd = obj.get("cwd")
                            except json.JSONDecodeError:
                                continue
                            if title and cwd:
                                break
                except OSError:
                    continue
                sessions.append({
                    "session_id": f.stem,
                    "title": title,
                    "cwd": cwd or "",
                    "tool": self.name,
                    "last_active": f.stat().st_mtime,
                })
        sessions.sort(key=lambda x: x["last_active"], reverse=True)
        return sessions[:limit]

    def post_start_hook(self, tmux_session: str) -> None:
        """Accept Claude's trust / safety-check prompt automatically."""
        for _ in range(40):
            time.sleep(0.25)
            try:
                r = subprocess.run(
                    ["tmux", "capture-pane", "-t", tmux_session, "-p"],
                    capture_output=True,
                    text=True,
                    timeout=3,
                )
                content = r.stdout.lower()
                if "trust" in content or "safety check" in content:
                    time.sleep(0.1)
                    subprocess.run(
                        ["tmux", "send-keys", "-t", tmux_session, "Enter"],
                        capture_output=True,
                        timeout=3,
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
                    remote_name = f"{hostname}-{raw[:4]}-{raw[4:8]}" if len(raw) >= 8 else f"{hostname}-{raw}"
                    result.update(
                        {
                            "bridge_session_id": bridge_id,
                            "bridge_url": f"https://claude.ai/code/{bridge_id}",
                            "remote_name": remote_name,
                            "claude_status": meta.get("status"),
                        }
                    )
            except (OSError, json.JSONDecodeError):
                pass

        jsonl_path = self._get_jsonl_path(pid, cwd)
        if jsonl_path:
            token_usage = self._read_token_usage(jsonl_path)
            if token_usage:
                result["token_usage"] = token_usage

        return result
