"""The messages table: what the user typed and what the agent wrote back, from the archive.

See docs/0_12_03_memory.md, section 3. What counts as a message is the
tool's call (`AgentCli.conversation_reader`), the same as the File viewer's.

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
from pathlib import Path

import zstandard

from agentop import conversation
from agentop.agentclis import get_agent

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
-- Full-text search over the text. Trigrams match any part of a word, so a
-- partial name, a URL, or Japanese text (no spaces between words) all match.
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
    text, content='messages', content_rowid='id', tokenize='trigram'
);
CREATE TRIGGER IF NOT EXISTS messages_fts_insert AFTER INSERT ON messages BEGIN
    INSERT INTO messages_fts (rowid, text) VALUES (new.id, new.text);
END;
CREATE TABLE IF NOT EXISTS files (
    path TEXT PRIMARY KEY,     -- relative to the archive
    offset INTEGER NOT NULL,   -- bytes read, up to the last whole line
    context TEXT NOT NULL      -- what a later line needs from earlier ones (session, turn, ...)
);
"""


def reader_for(relative: Path) -> tuple[conversation.Reader, dict] | None:
    """How to read an archived file, `<tool>/<the tool's own layout>`, or None if it holds nothing for the table.

    Only what the tool counts as searchable: for Claude, not the subagents and
    tool output beside a session, which aren't what was said to the user.
    """
    parts = relative.parts
    agent = get_agent(parts[0]) if len(parts) > 1 else None
    if agent is None or not agent.searchable(Path(*parts[1:])):
        return None
    return agent.conversation_reader(relative)


# ---------------------------------------------------------------- the table


def _use_wal(db: sqlite3.Connection) -> None:
    # Switching a new file to WAL fails at once, without the busy timeout,
    # while another connection is opening it too (the sweep and a request at start).
    deadline = time.monotonic() + 10
    while True:
        try:
            db.execute("PRAGMA journal_mode=WAL")
            return
        except sqlite3.OperationalError:
            if time.monotonic() > deadline:
                raise
            time.sleep(0.05)


class Messages:
    def __init__(self, path: Path, archive_dir: Path):
        self.path = path
        self.archive_dir = archive_dir

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=30)
        _use_wal(db)
        had_index = db.execute(
            "SELECT 1 FROM sqlite_master WHERE name = 'messages_fts'"
        ).fetchone()
        db.executescript(SCHEMA)
        if not had_index:
            # A table built before the index: index what is already there.
            with db:
                db.execute("INSERT INTO messages_fts (messages_fts) VALUES ('rebuild')")
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
            # The statement's own count: the index trigger's writes aren't in it.
            added = db.executemany(
                "INSERT OR IGNORE INTO messages (tool, record, session, project, turn, at, role, text, file)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            ).rowcount
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

    # ------------------------------------------------------------ recall

    def search(
        self,
        query: str,
        limit: int = 20,
        tool: str | None = None,
        project: str | None = None,
        since: float | None = None,
    ) -> list[dict]:
        """Messages holding every term in the query, best match first.

        A term is a word, or "a phrase in quotes". Terms of three characters or
        more use the index; shorter ones are matched as plain text.
        """
        terms = split_terms(query)
        if not terms or not self.path.exists():
            return []
        source, params, ranked = _matching(terms, tool, project, since)
        columns = "m.id, m.tool, m.session, m.project, m.turn, m.at, m.role, m.text"
        order = "f.rank, m.at DESC" if ranked else "m.at DESC"
        sql = f"SELECT {columns} {source} ORDER BY {order} LIMIT ?"
        params = [*params, limit]
        db = self._connect()
        try:
            rows = db.execute(sql, params).fetchall()
            hits = []
            for id_, tool_, session, project_, turn, at, role, text in rows:
                prompt = None
                if role == "assistant" and turn:
                    found = (
                        db.execute(
                            "SELECT text FROM messages WHERE tool = ? AND record = ? AND role = 'user'",
                            (tool_, turn),
                        ).fetchone()
                        or db.execute(
                            "SELECT text FROM messages WHERE tool = ? AND session = ? AND turn = ? AND role = 'user'"
                            " ORDER BY at, id LIMIT 1",
                            (tool_, session, turn),
                        ).fetchone()
                    )
                    prompt = found[0] if found else None
                hits.append(
                    {
                        "id": id_,
                        "tool": tool_,
                        "session": session,
                        "project": project_,
                        "at": at,
                        "role": role,
                        "snippet": snippet(text, terms),
                        "prompt": snippet(prompt, terms, 200) if prompt else None,
                    }
                )
            return hits
        finally:
            db.close()

    def search_sessions(
        self,
        query: str,
        limit: int = 50,
        tool: str | None = None,
        project: str | None = None,
        since: float | None = None,
    ) -> list[dict]:
        """Sessions with messages holding every term, most such messages first."""
        terms = split_terms(query)
        if not terms or not self.path.exists():
            return []
        source, params, _ = _matching(terms, tool, project, since)
        sql = (
            f"SELECT m.tool, m.session, COUNT(*) AS n, MAX(m.at) {source} AND m.session IS NOT NULL"
            " GROUP BY m.tool, m.session ORDER BY n DESC, MAX(m.at) DESC LIMIT ?"
        )
        db = self._connect()
        try:
            rows = db.execute(sql, [*params, limit]).fetchall()
        finally:
            db.close()
        return [{"tool": t, "session": s, "mentions": n} for t, s, n, _ in rows]

    def session(self, tool: str | None, session: str) -> dict | None:
        """What the table knows of a session: its tool, project, first and last message, and how many."""
        if not self.path.exists():
            return None
        db = self._connect()
        try:
            row = db.execute(
                "SELECT tool, MAX(project), MIN(at), MAX(at), COUNT(*) FROM messages"
                " WHERE session = ? AND (? IS NULL OR tool = ?) GROUP BY tool ORDER BY MAX(at) DESC LIMIT 1",
                (session, tool, tool),
            ).fetchone()
        finally:
            db.close()
        if not row:
            return None
        tool_, project, first_at, last_at, count = row
        return {"tool": tool_, "session": session, "project": project, "first_at": first_at, "last_at": last_at, "messages": count}

    def conversation(self, tool: str | None, session: str, around: int | None = None, radius: int = 6) -> list[dict]:
        """A session's messages, oldest first. With `around` (a message id), only those near it."""
        if not self.path.exists():
            return []
        db = self._connect()
        try:
            rows = db.execute(
                "SELECT id, role, at, text FROM messages WHERE session = ? AND (? IS NULL OR tool = ?) ORDER BY at, id",
                (session, tool, tool),
            ).fetchall()
        finally:
            db.close()
        if around is not None:
            index = next((i for i, row in enumerate(rows) if row[0] == around), None)
            if index is not None:
                rows = rows[max(0, index - radius) : index + radius + 1]
        return [
            {"id": id_, "role": role, "at": at, "text": text}
            for id_, role, at, text in rows
        ]


def _matching(
    terms: list[str], tool: str | None, project: str | None, since: float | None
) -> tuple[str, list, bool]:
    """The FROM and WHERE of the messages holding every term, their parameters, and whether ranked by the index."""
    where, params = ["1"], []
    for term in terms:
        if len(term) < 3:
            # Trigrams need three characters.
            where.append("m.text LIKE ? ESCAPE '\\'")
            params.append("%" + term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")
    for column, value in (("m.tool", tool), ("m.project", project)):
        if value:
            where.append(f"{column} = ?")
            params.append(value)
    if since is not None:
        where.append("m.at >= ?")
        params.append(since)
    long_terms = [t for t in terms if len(t) >= 3]
    if not long_terms:
        return "FROM messages m WHERE " + " AND ".join(where), params, False
    match = " AND ".join('"' + t.replace('"', '""') + '"' for t in long_terms)
    source = "FROM messages_fts f JOIN messages m ON m.id = f.rowid WHERE messages_fts MATCH ? AND " + " AND ".join(where)
    return source, [match, *params], True


def split_terms(query: str) -> list[str]:
    """Words, and "phrases in quotes", from a search query."""
    terms = []
    for quoted, word in re.findall(r'"([^"]*)"|(\S+)', query):
        term = (quoted or word).strip()
        if term:
            terms.append(term)
    return terms


def snippet(text: str, terms: list[str], width: int = 300) -> str:
    """The part of the text around the first term found, on one line."""
    flat = " ".join(text.split())
    lower = flat.lower()
    found = [lower.find(t.lower()) for t in terms]
    starts = [i for i in found if i >= 0]
    start = max(0, min(starts) - width // 3) if starts else 0
    piece = flat[start : start + width]
    return (
        ("…" if start > 0 else "") + piece + ("…" if start + width < len(flat) else "")
    )
