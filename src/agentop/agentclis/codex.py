from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Optional

import psutil

from agentop.agentcli import AgentCli


class CodexAgentCli(AgentCli):
    name = "codex"

    # Tolerance for matching process start time to thread created_at_ms (30 seconds)
    _START_TOLERANCE_MS = 30_000

    @property
    def launch_cmd(self) -> str:
        return "codex --dangerously-bypass-hook-trust"

    def start_session(self, cwd: str, short_name: str = "", params: dict | None = None) -> dict:
        """Pre-trust cwd before launching, otherwise codex's first-run 'Do you
        trust the contents of this directory?' prompt eats whatever gets typed
        next (e.g. a dialogue orchestrator's opening prompt, sent unattended).
        """
        self._pretrust_dir(cwd)
        return super().start_session(cwd, short_name, params)

    @staticmethod
    def _pretrust_dir(cwd: str) -> None:
        config_path = Path("~/.codex/config.toml").expanduser()
        if not config_path.exists():
            return
        marker = f'[projects."{cwd}"]'
        text = config_path.read_text()
        if marker in text:
            return
        with open(config_path, "a") as f:
            f.write(f'\n{marker}\ntrust_level = "trusted"\n')

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
                    SELECT id, title, first_user_message, tokens_used, rollout_path, created_at_ms
                    FROM threads
                    WHERE archived IS NULL OR archived = 0
                    ORDER BY ABS(created_at_ms - ?) ASC
                    LIMIT 1
                    """,
                    (start_ms,),
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

    def get_resume_cmd(self, session_id: str) -> str:
        return f"codex resume {session_id}"

    def delete_session(self, session_id: str) -> None:
        db_path = Path("~/.codex/state_5.sqlite").expanduser()
        if not db_path.exists():
            raise FileNotFoundError("Codex database not found")
        with sqlite3.connect(str(db_path), timeout=5) as conn:
            cur = conn.execute("UPDATE threads SET archived = 1 WHERE id = ?", (session_id,))
            if cur.rowcount == 0:
                raise FileNotFoundError(f"Session {session_id} not found in Codex database")
            conn.commit()

    def get_saved_sessions(self, limit: int = 20) -> list[dict]:
        db_path = Path("~/.codex/state_5.sqlite").expanduser()
        if not db_path.exists():
            return []
        try:
            with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    """
                    SELECT id, title, first_user_message, cwd, updated_at_ms, created_at_ms
                    FROM threads
                    WHERE archived IS NULL OR archived = 0
                    ORDER BY COALESCE(updated_at_ms, created_at_ms) DESC
                    LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
        except sqlite3.Error:
            return []
        result = []
        for row in rows:
            title = row["title"] or row["first_user_message"] or None
            ts_ms = row["updated_at_ms"] or row["created_at_ms"] or 0
            result.append(
                {
                    "session_id": row["id"],
                    "title": title[:80] + ("…" if title and len(title) > 80 else "") if title else None,
                    "cwd": row["cwd"] or "",
                    "tool": self.name,
                    "last_active": ts_ms / 1000,
                }
            )
        return result

    def get_session_meta(self, pid: int, cwd: str) -> dict:
        """Override to query _find_thread once and derive session_id, title, and token usage."""
        thread = self._find_thread(pid, cwd)
        meta: dict = {}
        if not thread:
            return meta
        if thread.get("id"):
            meta["session_id"] = thread["id"]
        title = thread.get("title")
        if not title:
            first_msg = (thread.get("first_user_message") or "").strip()
            if first_msg:
                title = first_msg[:80] + ("…" if len(first_msg) > 80 else "")
        if title:
            meta["ai_title"] = title
        if thread.get("rollout_path"):
            token_usage = self._read_rollout_token_usage(thread["rollout_path"])
            if token_usage:
                meta["token_usage"] = token_usage
        return meta
