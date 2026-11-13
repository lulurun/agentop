"""Memory: agentop's own record of conversations. See docs/v2-design.md, section 12.11.

The archive is a copy of every agent transcript on this host, kept up to date
by a sweep at start, every few minutes, and when a session exits. After each
sweep the messages table reads what's new in it, and then a model summarizes
the sessions that have gone quiet (summaries.py).
"""

from __future__ import annotations

import asyncio
import shutil
import threading

from fastapi import Body

from agentop import agentclis
from agentop.agentclis import AgentCli

from .archive import Archive
from .messages import Messages
from .summaries import Summaries

SWEEP_SECONDS = 300


def summarizer() -> AgentCli | None:
    """The first installed tool that can answer a one-shot prompt."""
    for agent in agentclis.AGENTS:
        if agent.ask_cmd("") is not None and (agent.executable or shutil.which(agent.binary)):
            return agent
    return None


def register(api):
    archive = Archive(api.data_dir)
    messages = Messages(api.data_dir / "messages.sqlite", archive.files_dir)
    summaries = Summaries(messages)
    last_index: dict = {}
    last_summaries: dict = {}
    # One sweep at a time: the timer, a session exiting, and "Sweep now" can all ask at once.
    lock = threading.Lock()
    # Summaries take minutes, so they have their own lock and don't hold up a sweep.
    summarizing = threading.Lock()

    def summaries_enabled() -> bool:
        return bool((api.store.load() or {}).get("summaries", True))

    def summarize() -> None:
        agent = summarizer()
        if not summaries_enabled() or agent is None or not summarizing.acquire(blocking=False):
            return
        try:
            made_by = " ".join(filter(None, [agent.name, agent.ask_model]))

            def ask(prompt: str, system: str) -> str:
                return agent.ask(prompt, system, api.data_dir / "ask", stop=stopping.is_set)

            result = summaries.run(ask, made_by, stop=stopping.is_set)
            last_summaries.clear()
            last_summaries.update(result)
            if result["made"] or result["errors"]:
                api.log.info(
                    "Summarized %d sessions with %s, %d errors in %.1fs%s",
                    result["made"],
                    made_by,
                    len(result["errors"]),
                    result["seconds"],
                    f": {result['errors'][0]}" if result["errors"] else "",
                )
                api.emit({"type": "status"})
        finally:
            summarizing.release()

    def sweep() -> dict:
        with lock:
            result = archive.sweep(stop=stopping.is_set)
            indexed = messages.index(stop=stopping.is_set)
            last_index.clear()
            last_index.update(indexed)
        if (
            result["copied"]
            or result["appended"]
            or result["replaced"]
            or result["errors"]
        ):
            api.log.info(
                "Swept %d new, %d grown, %d replaced, %d errors in %.1fs",
                result["copied"],
                result["appended"],
                result["replaced"],
                len(result["errors"]),
                result["seconds"],
            )
        if indexed["added"] or indexed["errors"]:
            api.log.info(
                "Indexed %d messages from %d files, %d errors in %.1fs",
                indexed["added"],
                indexed["files"],
                len(indexed["errors"]),
                indexed["seconds"],
            )
        api.emit({"type": "status"})
        return result

    # A session exiting asks for a sweep soon. The handler only signals: a sweep
    # can take longer than an event handler is allowed to run.
    wanted = threading.Event()

    stopping = threading.Event()

    async def sweeper():
        stopping.clear()
        try:
            while True:
                wanted.clear()
                await asyncio.to_thread(sweep)
                await asyncio.to_thread(summarize)
                await asyncio.to_thread(wanted.wait, SWEEP_SECONDS)
        finally:
            # Release the threads, or a service restart would wait for them.
            stopping.set()
            wanted.set()

    api.task(sweeper)
    # The tool has written its last lines by the time the session exits.
    api.on("session.exited", lambda _row: wanted.set())

    def status() -> dict:
        agent = summarizer()
        return {
            **archive.status(),
            "messages": {**messages.status(), "last_index": last_index or None},
            "summaries": {
                **summaries.status(),
                "enabled": summaries_enabled(),
                "by": " ".join(filter(None, [agent.name, agent.ask_model])) if agent else None,
                "last_run": last_summaries or None,
            },
        }

    @api.router.get("/status")
    async def get_status():
        return await asyncio.to_thread(status)

    @api.router.post("/sweep")
    async def sweep_now():
        await asyncio.to_thread(sweep)
        return await asyncio.to_thread(status)

    @api.router.patch("/summaries")
    async def set_summaries(enabled: bool = Body(..., embed=True)):
        """Turn session summaries on or off. Off keeps the ones already made."""
        data = api.store.load() or {}
        data["summaries"] = enabled
        api.store.save(data)
        if enabled:
            wanted.set()  # start on the backlog now, not at the next sweep
        return await asyncio.to_thread(status)

    @api.router.get("/search/sessions")
    async def search_sessions(
        q: str, limit: int = 5, tool: str | None = None, project: str | None = None, since: float | None = None
    ):
        """Sessions whose summary holds every word of `q`, best match first."""
        return await asyncio.to_thread(summaries.search, q, max(1, min(limit, 100)), tool, project, since)

    @api.router.get("/summary")
    async def summary(session: str, tool: str | None = None):
        """A session's summary, or null when it has none yet."""
        return await asyncio.to_thread(summaries.get, tool, session)

    @api.router.get("/search")
    async def search(
        q: str, limit: int = 20, tool: str | None = None, project: str | None = None, since: float | None = None
    ):
        """Messages holding every word of `q`, best match first. `since` is seconds since the epoch."""
        return await asyncio.to_thread(messages.search, q, max(1, min(limit, 200)), tool, project, since)

    @api.router.get("/conversation")
    async def conversation(session: str, tool: str | None = None, around: int | None = None, radius: int = 6):
        """A session's messages, oldest first; with `around`, the ones near that message."""
        return await asyncio.to_thread(messages.conversation, tool, session, around, max(0, min(radius, 200)))
