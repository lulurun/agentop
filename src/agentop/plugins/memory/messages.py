"""The messages table: what the user typed and what the agent wrote back, from the archive.

See docs/v2-design.md, section 12.11. What counts as a message is
agentop.conversation's call, the same as the File viewer's.

The table is derived. Deleting messages.sqlite and indexing again rebuilds it
from the archive. Each archived file is read from where the last run stopped,
and each record is stored once, by its own ID, so a file read twice or a
session resumed into a new file adds nothing.
"""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import zstandard

from agentop import conversation

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


def reader_for(relative: Path) -> tuple[conversation.Reader, dict] | None:
    """How to read an archived file, or None if it holds nothing for the table.

    Only the main conversations: a Claude session's folder holds its subagents
    and tool output, which aren't what was said to the user.
    """
    parts = relative.parts
    if len(parts) < 3 or not conversation.TRANSCRIPT.search(relative.name):
        return None
    tool = parts[0]
    if tool == "claude" and len(parts) != 3:
        return None
    if tool == "codex" and parts[1] not in ("sessions", "archived_sessions"):
        return None
    return conversation.reader_for(tool, relative)


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
            known = {
                row[0]: (row[1], row[2])
                for row in db.execute("SELECT path, offset, context FROM files")
            }
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
                    added = self._read(
                        db, key, path, reader, offset, json.loads(context)
                    )
                except (OSError, ValueError, zstandard.ZstdError) as exc:
                    if len(errors) < 20:
                        errors.append(f"{key}: {exc}")
                    continue
                counts["files"] += 1
                counts["added"] += added
        finally:
            db.close()
        return {
            "at": started,
            "seconds": round(time.time() - started, 3),
            **counts,
            "errors": errors,
        }

    def _read(
        self,
        db: sqlite3.Connection,
        key: str,
        path: Path,
        reader: conversation.Reader,
        offset: int,
        context: dict,
    ) -> int:
        tool = key.split("/", 1)[0]
        rows = []
        with open(path, "rb") as f:
            if conversation.compressed(path):
                # Written once, whole, so read whole. The offset just marks it read.
                lines: Iterator[bytes] = iter(
                    zstandard.ZstdDecompressor()
                    .stream_reader(f)
                    .read()
                    .splitlines(keepends=True)
                )
                offset = path.stat().st_size
                growing = False
            else:
                f.seek(offset)
                lines, growing = iter(f), True

            def whole_lines() -> Iterator[bytes]:
                nonlocal offset
                for line in lines:
                    if growing:
                        if not line.endswith(b"\n"):
                            return  # Still being written: read it next time.
                        offset += len(line)
                    yield line

            for m in conversation.records(whole_lines(), context, reader):
                rows.append(
                    (
                        tool,
                        m.record,
                        m.session,
                        m.project,
                        m.turn,
                        m.at,
                        m.role,
                        m.text,
                        key,
                    )
                )
        with db:
            before = db.total_changes
            db.executemany(
                "INSERT OR IGNORE INTO messages (tool, record, session, project, turn, at, role, text, file)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
            added = db.total_changes - before
            db.execute(
                "INSERT OR REPLACE INTO files (path, offset, context) VALUES (?, ?, ?)",
                (key, offset, json.dumps(context)),
            )
        return added

    def status(self) -> dict:
        if not self.path.exists():
            return {"messages": 0, "sessions": 0, "tools": {}}
        db = self._connect()
        try:
            tools = {
                tool: {"messages": n, "sessions": s}
                for tool, n, s in db.execute(
                    "SELECT tool, COUNT(*), COUNT(DISTINCT session) FROM messages GROUP BY tool"
                )
            }
        finally:
            db.close()
        return {
            "messages": sum(t["messages"] for t in tools.values()),
            "sessions": sum(t["sessions"] for t in tools.values()),
            "tools": tools,
        }
