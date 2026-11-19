from __future__ import annotations

import json
import logging
import re
import sqlite3
import time
import tomllib
from collections.abc import Iterator
from pathlib import Path
from typing import Optional

import psutil

from agentop.agentclis.base import AgentCli, files_under
from agentop.conversation import TRANSCRIPT, Message, Reader, epoch, json_lines, texts
from agentop.results import SavedSession, StartResult, saved_session
from agentop.tmux import Session

LOG = logging.getLogger(__name__)

# A folder name with these can't be written safely into codex's config.
_UNSAFE_PATH_CHARS = re.compile(r"[\x00-\x1f\x7f\ud800-\udfff]")

# tmux paste-buffer returning only confirms the paste was queued, not that
# codex's CLI has finished ingesting it from the pty. Sending Enter immediately
# races that ingestion for large prompts (e.g. the turn-1 role+brief message)
# and can submit before the paste lands, silently dropping it. A short settle
# delay scaled to payload size avoids that race.
_BASE_SETTLE_SECONDS = 0.5
_SETTLE_SECONDS_PER_KB = 0.05

# ---------------------------------------------------------------- transcripts as conversations

_NOISE = ("# AGENTS.md instructions",)
_TAGGED = re.compile(r"<([A-Za-z_][\w-]*)>.*</\1>", re.DOTALL)


def _injected(text: str) -> bool:
    """Text Codex adds to a user message: instructions, context, app events, each one whole tagged block."""
    return text.startswith(_NOISE) or _TAGGED.fullmatch(text) is not None


def messages(record: dict, context: dict) -> Iterator[Message]:
    """The prompt or reply in one record of a Codex rollout."""
    kind = record.get("type")
    payload = record.get("payload") or {}
    if kind == "session_meta":
        context["session"] = payload.get("id") or payload.get("session_id")
        context["project"] = payload.get("cwd")
        return
    if kind == "turn_context":
        context["turn"] = payload.get("turn_id")
        context["project"] = payload.get("cwd") or context.get("project")
        return
    if kind != "response_item" or payload.get("type") != "message":
        return
    role = payload.get("role")
    if role not in ("user", "assistant") or not payload.get("id"):
        return
    kinds = ("input_text",) if role == "user" else ("output_text",)
    parts = [t.strip() for t in texts(payload.get("content"), kinds)]
    text = "\n\n".join(t for t in parts if t and not (role == "user" and _injected(t)))
    if text:
        yield Message(
            record=payload["id"],
            role=role,
            text=text,
            at=epoch(record.get("timestamp")),
            session=context.get("session"),
            project=context.get("project"),
            turn=context.get("turn"),
        )


class CodexAgentCli(AgentCli):
    name = "codex"
    add_dir_flag = "--add-dir"
    # Codex boots its MCP servers first; a prompt sent sooner is dropped.
    boot_seconds = 20.0

    # Tolerance for matching process start time to thread created_at_ms (30 seconds)
    _START_TOLERANCE_MS = 30_000
    # Codex's own background processes, which share the name but are not agents.
    _HELPERS = ("app-server", "codex-code-mode-host")
    _RESUME_RE = re.compile(r"\bresume\s+([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\b")

    def __init__(self, home: Optional[Path] = None) -> None:
        super().__init__(home)
        # The last thread seen for each process, keyed by (pid, start time), since codex trims its log.
        self._threads: dict[tuple[int, float], str] = {}

    def matches(self, proc_name: str, cmdline: str) -> bool:
        if not super().matches(proc_name, cmdline):
            return False
        words = [word.split("/")[-1] for word in cmdline.split()]
        if any(helper in {*words, proc_name} for helper in self._HELPERS):
            return False
        # `codex exec` answers one prompt and exits: a script's, or agentop's
        # own (ask), never a session to list.
        after = words.index("codex") + 1 if "codex" in words else 1
        return words[after : after + 1] != ["exec"]

    # ------------------------------------------------------------------ one-shot prompts

    # The fast, cheap model is plenty for summaries.
    ask_model = "gpt-6-luna"

    def ask_cmd(self, system: str) -> list[str]:
        # --ephemeral writes no rollout, so an answer never becomes a session to
        # archive. --ignore-user-config skips the user's MCP servers and
        # settings (sign-in still comes from ~/.codex), and with the shell tool
        # off the model can only answer. The system prompt goes in as the
        # developer instructions, a TOML string.
        return [
            "codex",
            "exec",
            "--ephemeral",
            "--skip-git-repo-check",
            "--ignore-user-config",
            "--ignore-rules",
            "--sandbox",
            "read-only",
            "--color",
            "never",
            "--model",
            self.ask_model,
            "-c",
            "model_reasoning_effort=low",
            "-c",
            "features.shell_tool=false",
            "-c",
            f"developer_instructions={json.dumps(system, ensure_ascii=False)}",
            "-",
        ]

    @property
    def launch_cmd(self) -> str:
        return "codex --dangerously-bypass-hook-trust"

    def skills_dir(self) -> Path:
        return self.dir / "skills"

    def send_text(self, session: str, text: str) -> None:
        """Bracketed paste, since codex submits on a bare newline, then a pause for it to take the paste in."""
        Session.paste_text_bracketed(session, text)
        time.sleep(_BASE_SETTLE_SECONDS + _SETTLE_SECONDS_PER_KB * (len(text) / 1024))
        Session.send_keys(session, "Enter")

    def start_session(self, cwd: str, short_name: str = "", params: dict | None = None, **kwargs) -> StartResult:
        """Pre-trust cwd before launching, otherwise codex's first-run 'Do you
        trust the contents of this directory?' prompt eats whatever gets typed
        next (e.g. a dialogue orchestrator's opening prompt, sent unattended).
        """
        self._pretrust_dir(cwd)
        return super().start_session(cwd, short_name, params, **kwargs)

    @property
    def dir(self) -> Path:
        return self.home / ".codex"

    def _pretrust_dir(self, cwd: str) -> None:
        """Mark cwd trusted in ~/.codex/config.toml, so codex starts without asking.

        cwd is a folder name, which can hold any character: it goes in as an escaped
        TOML string, or a name with a quote and a newline could add its own tables
        (an MCP server command, say). The file is appended to, not rewritten, so a
        symlinked config stays a symlink.
        """
        config_path = (self.dir / "config.toml")
        if not config_path.exists():
            return
        if _UNSAFE_PATH_CHARS.search(cwd):
            LOG.warning("Not pre-trusting %r for codex: the path has control or surrogate characters", cwd)
            return
        try:
            text = config_path.read_text()
            if cwd in (tomllib.loads(text).get("projects") or {}):
                return
            entry = f'\n[projects.{json.dumps(cwd, ensure_ascii=False)}]\ntrust_level = "trusted"\n'
            if tomllib.loads(text + entry).get("projects", {}).get(cwd) != {"trust_level": "trusted"}:
                raise ValueError("the entry wouldn't parse as this folder's trust setting")
        except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError, ValueError, AttributeError) as exc:
            LOG.warning("Not pre-trusting %s for codex: %s", cwd, exc)
            return
        with open(config_path, "a") as f:
            f.write(entry)

    def _logged_thread_id(self, pid: int, start: float) -> Optional[str]:
        """The thread this process last logged about, from codex's own log.

        Codex tags each log line with `pid:{pid}:{uuid}` and the thread it concerns,
        so this follows /new and resumes. Lines from before the process started
        belong to an earlier process that had the same pid.
        """
        db_path = (self.dir / "logs_2.sqlite")
        if not db_path.exists():
            return None
        prefix = f"pid:{pid}:"
        try:
            with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2) as conn:
                row = conn.execute(
                    """
                    SELECT thread_id FROM logs
                    WHERE process_uuid >= ? AND process_uuid < ? AND thread_id IS NOT NULL AND ts >= ?
                    ORDER BY id DESC
                    LIMIT 1
                    """,
                    (prefix, prefix[:-1] + ";", int(start) - 1),
                ).fetchone()
        except sqlite3.Error:
            return None
        return row[0] if row else None

    def session_id_after(self, pid: int, started: float) -> Optional[str]:
        """Codex's log keeps which thread each process worked on, after it exits."""
        return self._logged_thread_id(pid, started)

    def _thread_id(self, pid: int) -> Optional[str]:
        """The thread a running codex works on: from its log, else from `codex resume <id>`."""
        try:
            process = psutil.Process(pid)
            start = process.create_time()
            cmdline = " ".join(process.cmdline())
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return None
        key = (pid, start)
        found = self._logged_thread_id(pid, start)
        if not found:
            resumed = self._RESUME_RE.search(cmdline)
            found = self._threads.get(key) or (resumed.group(1) if resumed else None)
        if found:
            self._threads[key] = found
        return found

    def _thread_by_id(self, thread_id: str) -> Optional[dict]:
        db_path = (self.dir / "state_5.sqlite")
        if not db_path.exists():
            return None
        try:
            with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2) as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute(
                    "SELECT id, title, first_user_message, tokens_used, rollout_path, created_at_ms FROM threads WHERE id = ?",
                    (thread_id,),
                ).fetchone()
        except sqlite3.Error:
            return None
        return dict(row) if row else None

    def _find_thread(self, pid: int, cwd: str) -> Optional[dict]:
        """The thread this process works on, by its own record, else by cwd + start time."""
        thread_id = self._thread_id(pid)
        if thread_id:
            thread = self._thread_by_id(thread_id)
            if thread:
                return thread
        return self._thread_by_start(pid, cwd)

    def _thread_by_start(self, pid: int, cwd: str) -> Optional[dict]:
        """Query state_5.sqlite for the thread matching this process by cwd + start time.

        Returns a dict with title, first_user_message, tokens_used, rollout_path,
        or None if no close match is found.
        """
        db_path = (self.dir / "state_5.sqlite")
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

    def token_usage(self, path: Path) -> Optional[dict]:
        """The last running total in a rollout (its last `token_count` event).

        Codex counts cache reads and writes inside `input_tokens`; they are taken out here, as the other tools report them.
        """
        if not TRANSCRIPT.search(path.name) or not path.name.startswith("rollout-"):
            return None
        last: Optional[dict] = None
        try:
            for obj in json_lines(path):
                payload = obj.get("payload") or {}
                if obj.get("type") == "event_msg" and isinstance(payload, dict) and payload.get("type") == "token_count":
                    total = (payload.get("info") or {}).get("total_token_usage")
                    if total:
                        last = total
        except OSError:
            return None
        if not last:
            return None
        cached = last.get("cached_input_tokens", 0)
        written = last.get("cache_write_input_tokens", 0)
        return {
            "input_tokens": max(last.get("input_tokens", 0) - cached - written, 0),
            "output_tokens": last.get("output_tokens", 0),
            "cache_read_input_tokens": cached,
            "cache_creation_input_tokens": last.get("cache_write_input_tokens", 0),
        }

    def get_resume_cmd(self, session_id: str) -> str:
        return f"codex resume {session_id}"

    def delete_session(self, session_id: str) -> None:
        db_path = (self.dir / "state_5.sqlite")
        if not db_path.exists():
            raise FileNotFoundError("Codex database not found")
        with sqlite3.connect(str(db_path), timeout=5) as conn:
            cur = conn.execute("UPDATE threads SET archived = 1 WHERE id = ?", (session_id,))
            if cur.rowcount == 0:
                raise FileNotFoundError(f"Session {session_id} not found in Codex database")
            conn.commit()

    # ------------------------------------------------------------------ transcripts

    def transcripts_root(self) -> Path:
        return self.dir

    def archive_files(self, root: Path) -> Iterator[Path]:
        """The rollouts, live and archived. Not codex's databases, logs, or config."""
        yield from files_under(root / "sessions")
        yield from files_under(root / "archived_sessions")

    def conversation_reader(self, path: Path) -> tuple[Reader, dict] | None:
        if not TRANSCRIPT.search(path.name) or not path.name.startswith("rollout-"):
            return None
        return messages, {}

    def transcript_files(self, session_id: str, root: Optional[Path] = None) -> tuple[Optional[Path], list[Path]]:
        """The thread's rollout log.

        Codex records where it is, but later compresses it (`.jsonl.zst`) or
        moves it to archived_sessions, so the rollout is also looked for by name.
        """
        home = self.transcripts_root()
        base = root or home
        thread = self._thread_by_id(session_id)
        if thread and thread.get("rollout_path"):
            recorded = Path(thread["rollout_path"])
            if recorded.is_relative_to(home):
                recorded = base / recorded.relative_to(home)
            if recorded.is_file():
                return recorded.parent, [recorded]
        found = sorted(
            path
            for folder in ("sessions", "archived_sessions")
            if (base / folder).is_dir()
            for path in (base / folder).rglob(f"rollout-*{session_id}.jsonl*")
            if path.is_file()
        )
        if not found:
            return None, []
        return (found[0].parent if len(found) == 1 else base), found

    def get_saved_sessions(self, limit: int = 20) -> list[SavedSession]:
        db_path = (self.dir / "state_5.sqlite")
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
            title = title[:80] + ("…" if len(title) > 80 else "") if title else None
            result.append(saved_session(self.name, row["id"], title, row["cwd"], ts_ms / 1000))
        return result

    def get_session_meta(self, pid: int, cwd: str) -> dict:
        """From the one thread _find_thread finds: session_id, title, and token usage."""
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
            path = Path(thread["rollout_path"])
            token_usage = self.token_usage(path) if path.is_file() else None
            if token_usage:
                meta["token_usage"] = token_usage
        return meta
