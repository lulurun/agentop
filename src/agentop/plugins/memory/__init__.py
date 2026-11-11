"""Memory: agentop's own record of conversations. See docs/v2-design.md, section 12.11.

The archive is a copy of every agent transcript on this host, kept up to date
by a sweep at start, every few minutes, and when a session exits. After each
sweep the messages table reads what's new in it.
"""

from __future__ import annotations

import asyncio
import threading

from .archive import Archive
from .messages import Messages

SWEEP_SECONDS = 300


def register(api):
    archive = Archive(api.data_dir)
    messages = Messages(api.data_dir / "messages.sqlite", archive.files_dir)
    last_index: dict = {}
    # One sweep at a time: the timer, a session exiting, and "Sweep now" can all ask at once.
    lock = threading.Lock()

    def sweep() -> dict:
        with lock:
            result = archive.sweep(stop=stopping.is_set)
            indexed = messages.index(stop=stopping.is_set)
            last_index.clear()
            last_index.update(indexed)
        if result["copied"] or result["appended"] or result["replaced"] or result["errors"]:
            api.log.info(
                "Swept %d new, %d grown, %d replaced, %d errors in %.1fs",
                result["copied"], result["appended"], result["replaced"], len(result["errors"]), result["seconds"],
            )
        if indexed["added"] or indexed["errors"]:
            api.log.info(
                "Indexed %d messages from %d files, %d errors in %.1fs",
                indexed["added"], indexed["files"], len(indexed["errors"]), indexed["seconds"],
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
                await asyncio.to_thread(wanted.wait, SWEEP_SECONDS)
        finally:
            # Release the threads, or a service restart would wait for them.
            stopping.set()
            wanted.set()

    api.task(sweeper)
    # The tool has written its last lines by the time the session exits.
    api.on("session.exited", lambda _row: wanted.set())

    def status() -> dict:
        return {**archive.status(), "messages": {**messages.status(), "last_index": last_index or None}}

    @api.router.get("/status")
    async def get_status():
        return await asyncio.to_thread(status)

    @api.router.post("/sweep")
    async def sweep_now():
        await asyncio.to_thread(sweep)
        return await asyncio.to_thread(status)
