"""SessionStore: the service's live view of sessions.

tmux says what exists, the scanner says what is running, and the registry says
what agentop knows about it. The store merges those on a tick and publishes the
differences. It also runs starts: the request returns at once, and the session
moves from `starting` to `running` or `failed` in the background.
"""

from __future__ import annotations

import asyncio
import copy
import logging
import os
import re
import time

from agentop import ops, registry
from agentop.tmux import Session

LOG = logging.getLogger(__name__)

TICK_SECONDS = 2.0
META_EVERY_TICKS = 5
PRUNE_EVERY_TICKS = 300

META_KEYS = ("session_id", "ai_title", "token_usage", "bridge_url", "bridge_session_id", "remote_name", "claude_status")
# Change on nearly every tick, so they are only published with metadata.
_VOLATILE_KEYS = ("runtime_seconds", "memory_mb")

_RESUME_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_./:-]*")
_SLUG_INVALID = re.compile(r"[^A-Za-z0-9_-]+")
_PID_SUFFIX = re.compile(r"-\d+$")


class StoreError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def _default_name(cwd: str) -> str:
    slug = _SLUG_INVALID.sub("-", os.path.basename(cwd.rstrip("/"))).strip("-_")
    return slug[:32].rstrip("-_") or "session"


def _base_row(session_id: str, record: dict, tmux_sessions: dict[str, int]) -> dict:
    attached = tmux_sessions.get(session_id)
    return {
        "id": session_id,
        "display_name": record.get("display_name") or _PID_SUFFIX.sub("", session_id),
        "description": record.get("description") or "",
        "tool": record.get("tool"),
        "status": "exited",
        "step": None,
        "error": None,
        "managed": bool(record.get("managed") or record.get("status")),
        "attachable": attached is not None,
        "cwd": record.get("cwd") or "",
        "created_at": record.get("created_at"),
        "pid": None,
        "runtime_seconds": None,
        "memory_mb": None,
        "tmux": {"session": session_id, "clients": attached} if attached is not None else None,
    }


def merge_sessions(
    scan: list[dict],
    tmux_sessions: dict[str, int],
    records: dict[str, dict],
    starting: dict[str, dict],
    meta: dict[str, dict],
    now: float,
) -> dict[str, dict]:
    """Combine the three sources into rows keyed by session ID."""
    rows: dict[str, dict] = {}

    for s in scan:
        session_id = s["name"]
        record = records.get(session_id, {})
        row = _base_row(session_id, record, tmux_sessions)
        tmux_name = (s.get("tmux") or {}).get("session")
        attached = tmux_sessions.get(tmux_name) if tmux_name else None
        row.update(
            {
                "tool": s.get("tool") or row["tool"],
                "status": "running",
                "display_name": row["display_name"] if s.get("managed") else session_id,
                "managed": bool(s.get("managed")),
                "attachable": attached is not None,
                "cwd": s.get("cwd") or row["cwd"],
                "created_at": row["created_at"] or now - (s.get("runtime_seconds") or 0),
                "pid": s.get("pid"),
                "runtime_seconds": s.get("runtime_seconds"),
                "memory_mb": s.get("memory_mb"),
                "tmux": {"session": tmux_name, "clients": attached} if attached is not None else None,
            }
        )
        rows[session_id] = row

    for session_id, record in records.items():
        if session_id in rows:
            continue
        alive = session_id in tmux_sessions
        failed = record.get("status") == "failed"
        if failed or (alive and (record.get("managed") or record.get("status"))):
            row = _base_row(session_id, record, tmux_sessions)
            if failed:
                row.update(status="failed", error=record.get("error"))
            rows[session_id] = row

    for session_id, row in rows.items():
        row.update(meta.get(session_id, {}))

    for session_id, record in starting.items():
        row = rows.get(session_id) or _base_row(session_id, record, tmux_sessions)
        row.update(status="starting", step=record.get("step"), error=None)
        rows[session_id] = row

    return rows


def _visible(row: dict) -> dict:
    return {k: v for k, v in row.items() if k not in _VOLATILE_KEYS}


class SessionStore:
    def __init__(self, tick_seconds: float = TICK_SECONDS):
        self.tick_seconds = tick_seconds
        self.last_scan: list[dict] = []
        self.last_updated = 0.0
        self._sessions: dict[str, dict] = {}
        self._starting: dict[str, dict] = {}
        self._meta: dict[str, dict] = {}
        self._subscribers: set[asyncio.Queue] = set()
        # Held for a whole refresh, so a finished start can never be overwritten
        # by a merge of data collected before it finished.
        self._lock = asyncio.Lock()
        self._tick_task: asyncio.Task | None = None
        self._start_tasks: set[asyncio.Task] = set()
        self._ticks = 0

    # ------------------------------------------------------------------ lifecycle

    async def startup(self) -> None:
        await self.refresh(with_meta=True)
        await self._reconcile_interrupted_starts()
        self._tick_task = asyncio.create_task(self._tick_loop())

    async def shutdown(self) -> None:
        if self._tick_task:
            self._tick_task.cancel()
            await asyncio.gather(self._tick_task, return_exceptions=True)
        if self._start_tasks:
            await asyncio.gather(*self._start_tasks, return_exceptions=True)

    async def _reconcile_interrupted_starts(self) -> None:
        """A start cannot survive a restart: its record says `starting`, with nothing running it."""
        records = (await asyncio.to_thread(registry.load))["sessions"]
        interrupted = [k for k, v in records.items() if v.get("status") == "starting" and k not in self._starting]
        for session_id in interrupted:
            if self._sessions.get(session_id, {}).get("pid"):
                fields = {"status": "running", "step": None}
            else:
                fields = {"status": "failed", "step": None, "error": "Interrupted by a service restart"}
            await asyncio.to_thread(registry.upsert_session, session_id, fields)
        if interrupted:
            await self.refresh()

    async def _tick_loop(self) -> None:
        while True:
            await asyncio.sleep(self.tick_seconds)
            self._ticks += 1
            try:
                await self.refresh(with_meta=self._ticks % META_EVERY_TICKS == 0)
            except Exception:
                LOG.exception("Session refresh failed")

    # ------------------------------------------------------------------ reading

    def list(self) -> list[dict]:
        return list(copy.deepcopy(self._sessions).values())

    def get(self, session_id: str) -> dict | None:
        return copy.deepcopy(self._sessions.get(session_id))

    def tmux_session(self, session_id: str) -> str:
        """Return the tmux session to attach to. A session not seen yet is assumed to be its own ID."""
        row = self._sessions.get(session_id) or {}
        return (row.get("tmux") or {}).get("session") or session_id

    @staticmethod
    def _collect(with_meta: bool):
        tmux_sessions = Session.list_all()
        records = registry.load()["sessions"]
        scan = ops.get_sessions(with_meta=with_meta)
        return tmux_sessions, records, scan

    async def refresh(self, with_meta: bool = False) -> None:
        async with self._lock:
            await self._refresh_locked(with_meta)

    async def _refresh_locked(self, with_meta: bool) -> None:
        tmux_sessions, records, scan = await asyncio.to_thread(self._collect, with_meta)
        if tmux_sessions is None:
            LOG.warning("Could not list tmux sessions; keeping the last known state")
            return

        if with_meta:
            # Merged, not replaced: an exited session keeps the ID it can be resumed with.
            self._meta.update({s["name"]: {k: s[k] for k in META_KEYS if k in s} for s in scan})
        self.last_scan = scan
        self.last_updated = time.time()

        rows = merge_sessions(scan, tmux_sessions, records, self._starting, self._meta, time.time())
        self._meta = {k: v for k, v in self._meta.items() if k in rows}
        self._publish_changes(rows, include_volatile=with_meta)
        self._sessions = rows

        if self._ticks % PRUNE_EVERY_TICKS == 0:
            await asyncio.to_thread(registry.mark_ended_and_prune, set(tmux_sessions))

    # ------------------------------------------------------------------ events

    def snapshot_event(self) -> dict:
        return {"type": "snapshot", "sessions": self.list()}

    def subscribe(self) -> asyncio.Queue:
        """Return a queue of events, beginning with a full snapshot."""
        queue: asyncio.Queue = asyncio.Queue(maxsize=256)
        queue.put_nowait(self.snapshot_event())
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._subscribers.discard(queue)

    def _publish(self, event: dict) -> None:
        for queue in self._subscribers:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                # A client this far behind is better served by starting over.
                while not queue.empty():
                    queue.get_nowait()
                queue.put_nowait(self.snapshot_event())

    def _publish_changes(self, rows: dict[str, dict], include_volatile: bool) -> None:
        for session_id in self._sessions.keys() - rows.keys():
            self._publish({"type": "session.removed", "id": session_id})
        for session_id, row in rows.items():
            old = self._sessions.get(session_id)
            compare = (lambda r: r) if include_volatile else _visible
            if old is None or compare(old) != compare(row):
                self._publish({"type": "session.upserted", "session": copy.deepcopy(row)})

    # ------------------------------------------------------------------ actions

    async def create(self, tool: str, cwd: str, name: str | None = None, resume_id: str | None = None) -> dict:
        """Validate, record the session as `starting`, and begin starting it in the background."""
        agent = ops.get_agent(tool)
        if not agent:
            raise StoreError(400, f"Unknown tool: {tool}")
        cwd = os.path.abspath(os.path.expanduser(cwd))
        if not os.path.isdir(cwd):
            raise StoreError(400, f"Not a directory: {cwd}")
        short_name = name or _default_name(cwd)
        if not ops.is_valid_short_name(short_name):
            raise StoreError(400, "Name must be 1-32 letters, digits, '-' or '_', starting with a letter or digit")
        if resume_id is not None:
            # The ID is typed into a shell.
            if not _RESUME_ID.fullmatch(resume_id):
                raise StoreError(400, "Invalid session_id")
            if not agent.get_resume_cmd(resume_id):
                raise StoreError(400, f"{tool} does not support resume")

        session_id = agent.new_session_name(short_name)
        record = {
            "managed": True,
            "tool": tool,
            "cwd": cwd,
            "display_name": short_name,
            "created_at": time.time(),
            "status": "starting",
            "step": "creating",
        }
        await asyncio.to_thread(registry.upsert_session, session_id, record)
        async with self._lock:
            self._starting[session_id] = record
            await self._refresh_locked(with_meta=False)

        task = asyncio.create_task(self._run_start(session_id, tool, cwd, short_name, resume_id))
        self._start_tasks.add(task)
        task.add_done_callback(self._start_tasks.discard)
        return self.get(session_id)

    def _set_step(self, session_id: str, step: str) -> None:
        record = self._starting.get(session_id)
        row = self._sessions.get(session_id)
        if record is None or row is None:
            return
        record["step"] = step
        row["step"] = step
        self._publish({"type": "session.upserted", "session": copy.deepcopy(row)})

    async def _run_start(self, session_id: str, tool: str, cwd: str, short_name: str, resume_id: str | None) -> None:
        loop = asyncio.get_running_loop()

        def on_progress(step: str) -> None:
            loop.call_soon_threadsafe(self._set_step, session_id, step)

        try:
            if resume_id is None:
                result = await asyncio.to_thread(
                    ops.start, tool, cwd, short_name, None, session_id, on_progress
                )
            else:
                result = await asyncio.to_thread(
                    ops.resume_session, tool, resume_id, cwd, short_name, session_id, on_progress
                )
        except Exception as exc:
            LOG.exception("Start of %s failed", session_id)
            result = {"ok": False, "error": str(exc)}

        if not result.get("ok"):
            error = result.get("error") or "Failed to start"
            await asyncio.to_thread(
                registry.upsert_session, session_id, {"status": "failed", "step": None, "error": error}
            )
        async with self._lock:
            self._starting.pop(session_id, None)
            await self._refresh_locked(with_meta=True)

    def _require(self, session_id: str) -> dict:
        row = self._sessions.get(session_id)
        if row is None:
            raise StoreError(404, "Session not found")
        if not row["managed"]:
            raise StoreError(403, "Not a managed session")
        return row

    async def stop(self, session_id: str) -> dict:
        """Stop the agent gracefully and close its tmux session."""
        row = self._require(session_id)
        if row["status"] == "starting":
            raise StoreError(409, "Session is still starting")
        if row["status"] != "running":
            return await self.close(session_id)
        result = await asyncio.to_thread(ops.stop, session_id, self.last_scan)
        if not result.get("ok"):
            raise StoreError(500, result.get("error") or "Failed to stop session")
        await self.refresh()
        return result

    async def close(self, session_id: str) -> dict:
        """Dismiss a session whose agent is not running, closing its tmux session if any."""
        row = self._require(session_id)
        if row["status"] not in ("exited", "failed"):
            raise StoreError(409, f"Session is {row['status']}")
        if row["attachable"]:
            await asyncio.to_thread(Session.kill, session_id)
        fields = {"status": None, "step": None, "error": None, "ended_at": time.time()}
        await asyncio.to_thread(registry.upsert_session, session_id, fields)
        await self.refresh()
        return {"ok": True}

    async def update(self, session_id: str, display_name: str | None, description: str | None) -> dict:
        self._require(session_id)
        fields = {}
        if display_name is not None:
            fields["display_name"] = display_name.strip() or None
        if description is not None:
            fields["description"] = description.strip() or None
        await asyncio.to_thread(registry.upsert_session, session_id, fields)
        await self.refresh()
        return self.get(session_id)


