from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

from agentop.agents.base import BaseAgent


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

    def get_resume_cmd(self, session_id: str) -> str:
        return "gemini --resume latest"

    def delete_session(self, session_id: str) -> None:
        tmp_base = Path("~/.gemini/tmp").expanduser()
        if not tmp_base.exists():
            raise FileNotFoundError("Gemini session directory not found")
        for project_dir in tmp_base.iterdir():
            if not project_dir.is_dir():
                continue
            chats_dir = project_dir / "chats"
            if not chats_dir.exists():
                continue
            for f in chats_dir.glob("session-*"):
                if f.stem == session_id:
                    f.unlink()
                    return
                try:
                    if f.suffix == ".jsonl":
                        with open(f) as fh:
                            sid = json.loads(fh.readline()).get("sessionId")
                    else:
                        with open(f) as fh:
                            sid = json.load(fh).get("sessionId")
                    if sid == session_id:
                        f.unlink()
                        return
                except (OSError, json.JSONDecodeError):
                    continue
        raise FileNotFoundError(f"Session {session_id} not found in Gemini sessions")

    def get_saved_sessions(self, limit: int = 20) -> list[dict]:
        """Return the most-recent Gemini session per project directory."""
        tmp_base = Path("~/.gemini/tmp").expanduser()
        if not tmp_base.exists():
            return []
        sessions = []
        try:
            for project_dir in tmp_base.iterdir():
                if not project_dir.is_dir():
                    continue
                root_file = project_dir / ".project_root"
                cwd = root_file.read_text().strip() if root_file.exists() else ""
                chats_dir = project_dir / "chats"
                if not chats_dir.exists():
                    continue
                chat_files = sorted(
                    list(chats_dir.glob("session-*.json")) + list(chats_dir.glob("session-*.jsonl")),
                    key=lambda f: f.stat().st_mtime,
                    reverse=True,
                )
                if not chat_files:
                    continue
                # Only the most-recent session per project (resume always targets latest)
                f = chat_files[0]
                session_id = None
                title = None
                try:
                    if f.suffix == ".jsonl":
                        with open(f) as fh:
                            lines = fh.readlines()
                        if lines:
                            header = json.loads(lines[0])
                            session_id = header.get("sessionId")
                        for line in lines[1:]:
                            obj = json.loads(line)
                            if obj.get("type") == "user":
                                content = obj.get("content", "")
                                if isinstance(content, list):
                                    content = " ".join(c.get("text", "") for c in content if isinstance(c, dict))
                                title = str(content).strip()[:80]
                                break
                    else:
                        with open(f) as fh:
                            data = json.load(fh)
                        session_id = data.get("sessionId") or f.stem
                        for msg in data.get("messages", data.get("history", [])):
                            if msg.get("type") == "user":
                                content = msg.get("content", "")
                                if isinstance(content, list):
                                    content = " ".join(
                                        c.get("text", "") for c in content if isinstance(c, dict)
                                    )
                                title = str(content).strip()[:80]
                                if title:
                                    break
                except (OSError, json.JSONDecodeError, KeyError):
                    pass
                sessions.append({
                    "session_id": session_id or f.stem,
                    "title": title or None,
                    "cwd": cwd,
                    "tool": self.name,
                    "last_active": f.stat().st_mtime,
                })
        except OSError:
            pass
        sessions.sort(key=lambda x: x["last_active"], reverse=True)
        return sessions[:limit]

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
