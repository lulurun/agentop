from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

import psutil

from agentop.agents.base import BaseAgent

_LOG_DIR = Path("~/.gemini/antigravity-cli/log")
_LOG_NAME_RE = re.compile(r"cli-(\d{8})_(\d{6})\.log")
_USER_INPUT_RE = re.compile(r'HandleUserInput called with text: "(.+)"')
_START_TOLERANCE_SEC = 15


class AntigravityAgent(BaseAgent):
    name = "antigravity"

    def matches(self, proc_name: str, cmdline: str) -> bool:
        name_lower = proc_name.lower()
        cmd_lower = cmdline.lower()
        if name_lower in ("agy", "antigravity") or name_lower.startswith("agy"):
            return True
        # May run as `node /path/to/bin/agy …`
        for token in cmd_lower.split():
            base = token.split("/")[-1]
            if base in ("agy", "antigravity") or base.startswith("agy-"):
                return True
        return False

    @property
    def launch_cmd(self) -> str:
        return "agy --dangerously-skip-permissions"

    def _find_log_file(self, pid: int) -> Optional[Path]:
        """Find the log file for this process by matching the filename timestamp to the process start time."""
        log_dir = _LOG_DIR.expanduser()
        if not log_dir.exists():
            return None
        try:
            proc_start = psutil.Process(pid).create_time()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return None

        best_file: Optional[Path] = None
        best_diff = float("inf")
        for f in log_dir.glob("cli-*.log"):
            m = _LOG_NAME_RE.match(f.name)
            if not m:
                continue
            try:
                log_ts = datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S").timestamp()
                diff = abs(log_ts - proc_start)
                if diff < best_diff:
                    best_diff = diff
                    best_file = f
            except ValueError:
                continue

        return best_file if best_diff <= _START_TOLERANCE_SEC else None

    def get_resume_cmd(self, session_id: str) -> str:
        return f"agy --conversation {session_id} --dangerously-skip-permissions"

    def _conv_to_cwd_map(self) -> dict[str, str]:
        """Scan log files to build a conversation_id → cwd mapping."""
        log_dir = _LOG_DIR.expanduser()
        projects_dir = Path("~/.gemini/config/projects").expanduser()
        conv_to_proj: dict[str, str] = {}
        proj_to_cwd: dict[str, str] = {}
        _proj_re = re.compile(r"Conversation using project ID: (\S+)")
        _conv_re = re.compile(r"Created conversation (\S+)")
        if log_dir.exists():
            for log_file in log_dir.glob("cli-*.log"):
                current_proj = None
                try:
                    with open(log_file) as f:
                        for line in f:
                            m = _proj_re.search(line)
                            if m:
                                current_proj = m.group(1)
                            m = _conv_re.search(line)
                            if m and current_proj:
                                conv_to_proj[m.group(1)] = current_proj
                except OSError:
                    continue
        if projects_dir.exists():
            for proj_file in projects_dir.glob("*.json"):
                try:
                    proj = json.loads(proj_file.read_text())
                    cwd = proj.get("name", "")
                    if cwd:
                        proj_to_cwd[proj_file.stem] = cwd
                except (OSError, json.JSONDecodeError):
                    continue
        return {c: proj_to_cwd[p] for c, p in conv_to_proj.items() if p in proj_to_cwd}

    def get_saved_sessions(self, limit: int = 20) -> list[dict]:
        """Return saved antigravity sessions from the brain transcript directory."""
        brain_dir = Path("~/.gemini/antigravity-cli/brain").expanduser()
        if not brain_dir.exists():
            return []
        cwd_map = self._conv_to_cwd_map()
        sessions = []
        for conv_dir in brain_dir.iterdir():
            if not conv_dir.is_dir():
                continue
            session_id = conv_dir.name
            transcript = conv_dir / ".system_generated/logs/transcript.jsonl"
            if not transcript.exists():
                continue
            title = None
            try:
                with open(transcript) as f:
                    for line in f:
                        try:
                            obj = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if obj.get("type") == "USER_INPUT" and not title:
                            raw = obj.get("content", "")
                            text = re.sub(r"<[^>]+>", "", raw).strip()
                            title = text[:80] + ("…" if len(text) > 80 else "")
                            break
            except OSError:
                continue
            sessions.append({
                "session_id": session_id,
                "title": title,
                "cwd": cwd_map.get(session_id, ""),
                "tool": self.name,
                "last_active": transcript.stat().st_mtime,
            })
        sessions.sort(key=lambda x: x["last_active"], reverse=True)
        return sessions[:limit]

    def delete_session(self, session_id: str) -> None:
        import shutil
        conv_dir = Path("~/.gemini/antigravity-cli/brain").expanduser() / session_id
        if not conv_dir.exists():
            raise FileNotFoundError(f"Session {session_id} not found in Antigravity brain")
        shutil.rmtree(conv_dir)

    def get_ai_title(self, pid: int, cwd: str) -> Optional[str]:
        """Return the first user message from the session log as the title."""
        log_file = self._find_log_file(pid)
        if not log_file:
            return None
        try:
            with open(log_file) as f:
                for line in f:
                    m = _USER_INPUT_RE.search(line)
                    if m:
                        text = m.group(1)
                        return text[:80] + ("…" if len(text) > 80 else "")
        except OSError:
            return None
        return None
