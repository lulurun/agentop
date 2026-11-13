"""Dialogues: two agents work together on one task. See docs/dialogue.md and docs/v2-design.md, section 12.9.

`agentop dialogue start` (cli.py) starts both agents as ordinary managed
sessions and an orchestrator process (runner.py) that relays messages between
them. The orchestrator is not part of the service: it keeps running when the
service restarts. The service side, here, lists saved dialogues for the app,
and stops and removes them.

Nothing here runs at import, so the orchestrator process and the CLI can load
the package without FastAPI.
"""

from __future__ import annotations

import re


def register(api) -> None:
    import asyncio

    from fastapi import HTTPException

    from . import ops
    from .model import dialogues_dir

    def exact_name(dialogue_id: str) -> str:
        """A dialogue ID names a folder that remove deletes, so it must be exactly that name.

        DialogueMeta.load also accepts the end of a name, which is handy on the
        command line, but over the API it could remove the wrong dialogue.
        """
        known = re.fullmatch(r"[A-Za-z0-9_-][A-Za-z0-9._-]*", dialogue_id) and (
            dialogues_dir() / dialogue_id / "meta.json"
        ).is_file()
        if not known:
            raise HTTPException(404, "No such dialogue")
        return dialogue_id

    @api.router.get("/dialogues")
    async def list_dialogues():
        return await asyncio.to_thread(ops.list_dialogues)

    @api.router.get("/dialogues/{dialogue_id}")
    async def get_dialogue(dialogue_id: str):
        detail = await asyncio.to_thread(ops.get_dialogue, exact_name(dialogue_id))
        if detail is None:
            raise HTTPException(404, "No such dialogue")
        return detail

    @api.router.post("/dialogues/{dialogue_id}/stop")
    async def stop_dialogue(dialogue_id: str):
        result = await asyncio.to_thread(ops.stop_dialogue, exact_name(dialogue_id))
        if not result.get("ok"):
            raise HTTPException(404, result.get("error"))
        return result

    @api.router.delete("/dialogues/{dialogue_id}")
    async def remove_dialogue(dialogue_id: str):
        result = await asyncio.to_thread(ops.remove_dialogue, exact_name(dialogue_id))
        if not result.get("ok"):
            raise HTTPException(404, result.get("error"))
        return result

    def stop_all() -> None:
        stopped = ops.stop_running()
        if stopped:
            api.log.info("Turned off: stopped dialogues %s", ", ".join(stopped))

    api.on_disable(stop_all)
