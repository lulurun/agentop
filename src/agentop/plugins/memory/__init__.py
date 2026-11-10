"""Memory: agentop's own record of conversations. See docs/v2-design.md, section 12.11.

Phase 1 is the archive: a copy of every agent transcript on this host, kept
up to date by a sweep at start, every few minutes, and when a session exits.
"""

from __future__ import annotations

import asyncio
import threading

from agentop import paths

from .archive import Archive

SWEEP_SECONDS = 300


def register(api):
    archive = Archive(paths.data_dir() / "memory")
    # One sweep at a time: the timer, a session exiting, and "Sweep now" can all ask at once.
    lock = threading.Lock()

    def sweep() -> dict:
        with lock:
            result = archive.sweep(stop=stopping.is_set)
        if result["copied"] or result["appended"] or result["replaced"] or result["errors"]:
            api.log.info(
                "Swept %d new, %d grown, %d replaced, %d errors in %.1fs",
                result["copied"], result["appended"], result["replaced"], len(result["errors"]), result["seconds"],
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

    @api.router.get("/status")
    async def status():
        return archive.status()

    @api.router.post("/sweep")
    async def sweep_now():
        await asyncio.to_thread(sweep)
        return archive.status()
