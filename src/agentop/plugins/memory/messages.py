"""The messages table: what the user typed and what the agent wrote back, from the archive.

See docs/v2-design.md, section 12.11. Only text is kept: the user's prompts and
the assistant's text. Tool calls, tool output, thinking, and the text the tools
inject (instructions, reminders, context) are left out.

The table is derived. Deleting messages.sqlite and indexing again rebuilds it
from the archive. Each archived file is read from where the last run stopped,
and each record is stored once, by its own ID, so a file read twice or a
session resumed into a new file adds nothing.
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import zstandard

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY,
    tool TEXT NOT NULL,
    record TEXT NOT NULL,      -- the record's own ID in the transcript
    session TEXT,
    project TEXT,              -- the working directory, where the tool records it
    turn TEXT,                 -- groups a prompt with the replies to it
    at REAL,                   -- seconds since the epoch
    role TEXT NOT NULL,        -- 'user' or 'assistant'
    text TEXT NOT NULL,
    file TEXT NOT NULL,        -- the archived file, relative to the archive
    UNIQUE (tool, record)
);
CREATE INDEX IF NOT EXISTS messages_session ON messages (session, at);
CREATE INDEX IF NOT EXISTS messages_at ON messages (at);
CREATE TABLE IF NOT EXISTS files (
    path TEXT PRIMARY KEY,     -- relative to the archive
    offset INTEGER NOT NULL,   -- bytes read, up to the last whole line
    context TEXT NOT NULL      -- what a later line needs from earlier ones (session, turn, ...)
);
"""


@dataclass
class Message:
    record: str
    role: str
    text: str
    at: float | None = None
    session: str | None = None
    project: str | None = None
    turn: str | None = None


def _epoch(stamp) -> float | None:
    if not isinstance(stamp, str):
        return None
    try:
        return datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _texts(content, kinds: tuple[str, ...]) -> list[str]:
    if isinstance(content, str):
        return [content]
    if not isinstance(content, list):
        return []
    return [b["text"] for b in content if isinstance(b, dict) and b.get("type") in kinds and isinstance(b.get("text"), str)]


# ---------------------------------------------------------------- claude

_REMINDER = re.compile(r"<system-reminder>.*?</system-reminder>", re.S)
_COMMAND = re.compile(r"<command-name>(.*?)</command-name>", re.S)
_ARGS = re.compile(r"<command-args>(.*?)</command-args>", re.S)
_CLAUDE_NOISE = ("<local-command-", "<command-message>", "[Request interrupted", "<task-notification>", "Caveat:")


def _claude_prompt(content) -> str:
    if isinstance(content, list) and any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
        return ""
    text = _REMINDER.sub("", "\n".join(_texts(content, ("text",)))).strip()
    command = _COMMAND.search(text)
    if command and text.startswith("<command-"):
        # A slash command: what was typed, not the expansion.
        args = _ARGS.search(text)
        return f"{command.group(1).strip()} {args.group(1).strip() if args else ''}".strip()
    if text.startswith(_CLAUDE_NOISE):
        return ""
    return text


def claude_messages(record: dict, context: dict) -> Iterator[Message]:
    kind = record.get("type")
    if kind not in ("user", "assistant") or record.get("isSidechain") or record.get("isMeta"):
        return
    if record.get("isCompactSummary") or record.get("isApiErrorMessage"):
        return
    uuid = record.get("uuid")
    message = record.get("message") or {}
    if not uuid or not isinstance(message, dict):
        return
    if kind == "user":
        text = _claude_prompt(message.get("content"))
        if text:
            context["turn"] = uuid
    else:
        text = "\n\n".join(_texts(message.get("content"), ("text",))).strip()
    if text:
        yield Message(
            record=uuid,
            role=kind,
            text=text,
            at=_epoch(record.get("timestamp")),
            session=record.get("sessionId"),
            project=record.get("cwd"),
            turn=context.get("turn"),
        )


# ---------------------------------------------------------------- codex

_CODEX_NOISE = ("# AGENTS.md instructions",)
_TAGGED = re.compile(r"<([A-Za-z_][\w-]*)>.*</\1>", re.S)


def _injected(text: str) -> bool:
    """Text Codex adds to a user message: instructions, context, app events, each one whole tagged block."""
    return text.startswith(_CODEX_NOISE) or _TAGGED.fullmatch(text) is not None


def codex_messages(record: dict, context: dict) -> Iterator[Message]:
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
    parts = [t.strip() for t in _texts(payload.get("content"), kinds)]
    text = "\n\n".join(t for t in parts if t and not (role == "user" and _injected(t)))
    if text:
        yield Message(
            record=payload["id"],
            role=role,
            text=text,
            at=_epoch(record.get("timestamp")),
            session=context.get("session"),
            project=context.get("project"),
            turn=context.get("turn"),
        )


# ---------------------------------------------------------------- antigravity

_REQUEST = re.compile(r"<USER_REQUEST>\s*(.*?)\s*</USER_REQUEST>", re.S)


def antigravity_messages(record: dict, context: dict) -> Iterator[Message]:
    kind = record.get("type")
    step = record.get("step_index")
    content = record.get("content")
    if step is None or not isinstance(content, str):
        return
    if kind == "USER_INPUT":
        found = _REQUEST.search(content)
        # Some prompts have no closing tag.
        text = (found.group(1) if found else content.removeprefix("<USER_REQUEST>")).strip()
        role = "user"
        context["turn"] = str(step)
    elif kind == "PLANNER_RESPONSE":
        text, role = content.strip(), "assistant"
    else:
        return
    if text:
        session = context.get("session")
        yield Message(
            record=f"{session}:{step}",
            role=role,
            text=text,
            at=_epoch(record.get("created_at")),
            session=session,
            turn=context.get("turn"),
        )


# ---------------------------------------------------------------- which files

Reader = Callable[[dict, dict], Iterator[Message]]

# A transcript, compressed (Codex compresses older ones), or the old copy of one
# the tool rewrote (`name.jsonl.1800000000`).
_TRANSCRIPT = re.compile(r"\.jsonl(\.zst)?(\.\d+)?$")


def reader_for(relative: Path) -> tuple[Reader, dict] | None:
    """How to read an archived file, and its starting context, or None if it holds no conversation."""
    parts = relative.parts
    if not _TRANSCRIPT.search(relative.name):
        return None
    tool = parts[0]
    if tool == "claude" and len(parts) == 3:
        # <slug>/<session>.jsonl. The session folders beside it hold subagents and tool output.
        return claude_messages, {}
    if tool == "codex" and parts[1] in ("sessions", "archived_sessions"):
        return codex_messages, {}
    if tool == "antigravity" and parts[-3:-1] == (".system_generated", "logs") and relative.name.startswith("transcript.jsonl"):
        return antigravity_messages, {"session": parts[1]}
    return None


# ---------------------------------------------------------------- the table


class Messages:
    def __init__(self, path: Path, archive_dir: Path):
        self.path = path
        self.archive_dir = archive_dir

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=30)
        db.execute("PRAGMA journal_mode=WAL")
        db.executescript(SCHEMA)
        return db

    def index(self, stop: Callable[[], bool] = lambda: False) -> dict:
        """Read what's new in the archive. Returns what this run did."""
        started = time.time()
        counts = {"files": 0, "added": 0}
        errors: list[str] = []
        if not self.archive_dir.is_dir():
            return {"at": started, "seconds": 0.0, **counts, "errors": errors}
        db = self._connect()
        try:
            known = {row[0]: (row[1], row[2]) for row in db.execute("SELECT path, offset, context FROM files")}
            for path in sorted(self.archive_dir.rglob("*")):
                if stop():
                    break
                relative = path.relative_to(self.archive_dir)
                how = reader_for(relative)
                if how is None or not path.is_file():
                    continue
                key = str(relative)
                offset, context = known.get(key, (0, None))
                try:
                    size = path.stat().st_size
                    if size == offset:
                        continue
                    reader, start = how
                    if size < offset or context is None:
                        offset, context = 0, json.dumps(start)
                    added = self._read(db, key, path, reader, offset, json.loads(context))
                except (OSError, ValueError, zstandard.ZstdError) as exc:
                    if len(errors) < 20:
                        errors.append(f"{key}: {exc}")
                    continue
                counts["files"] += 1
                counts["added"] += added
        finally:
            db.close()
        return {"at": started, "seconds": round(time.time() - started, 3), **counts, "errors": errors}

    def _read(self, db: sqlite3.Connection, key: str, path: Path, reader: Reader, offset: int, context: dict) -> int:
        tool = key.split("/", 1)[0]
        rows = []
        with open(path, "rb") as f:
            if ".jsonl.zst" in path.name:
                # Written once, whole, so read whole. The offset just marks it read.
                lines: Iterator[bytes] = iter(zstandard.ZstdDecompressor().stream_reader(f).read().splitlines(keepends=True))
                offset = path.stat().st_size
                growing = False
            else:
                f.seek(offset)
                lines, growing = iter(f), True
            for line in lines:
                if growing:
                    if not line.endswith(b"\n"):
                        break  # Still being written: read it next time.
                    offset += len(line)
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if isinstance(record, dict):
                    for m in reader(record, context):
                        rows.append((tool, m.record, m.session, m.project, m.turn, m.at, m.role, m.text, key))
        with db:
            before = db.total_changes
            db.executemany(
                "INSERT OR IGNORE INTO messages (tool, record, session, project, turn, at, role, text, file)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
            added = db.total_changes - before
            db.execute(
                "INSERT OR REPLACE INTO files (path, offset, context) VALUES (?, ?, ?)", (key, offset, json.dumps(context))
            )
        return added

    def status(self) -> dict:
        if not self.path.exists():
            return {"messages": 0, "sessions": 0, "tools": {}}
        db = self._connect()
        try:
            tools = {
                tool: {"messages": n, "sessions": s}
                for tool, n, s in db.execute("SELECT tool, COUNT(*), COUNT(DISTINCT session) FROM messages GROUP BY tool")
            }
        finally:
            db.close()
        return {
            "messages": sum(t["messages"] for t in tools.values()),
            "sessions": sum(t["sessions"] for t in tools.values()),
            "tools": tools,
        }
