"""Dialogues: two agents work together on one task. See docs/dialogue.md and docs/design.md, section 12.9.

`agentop dialogue start` (cli.py) starts both agents as ordinary managed
sessions and an orchestrator process (runner.py) that relays messages between
them. The orchestrator is not part of the service: it keeps running when the
service restarts. The service side, here, lists saved dialogues for the app,
and stops and removes them.

Nothing here runs at import, so the orchestrator process and the CLI can load
the package without FastAPI.
"""

# No `from __future__ import annotations`: FastAPI reads the request body's model,
# defined inside register(), from the route's annotations.
import re


def register(api) -> None:
    import asyncio

    import threading

    from fastapi import HTTPException
    from pydantic import BaseModel, Field

    from . import ops
    from .model import dialogues_dir

    class NewDialogue(BaseModel):
        scenario: str
        values: dict[str, str] = Field(default_factory=dict)
        agent_a: str = "claude"
        agent_b: str = "claude"
        max_turns: int = 0

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

    @api.router.get("/scenarios")
    async def list_scenarios():
        return await asyncio.to_thread(ops.list_scenarios)

    @api.router.post("/dialogues", status_code=202)
    async def create_dialogue(body: NewDialogue):
        try:
            meta = await asyncio.to_thread(
                ops.new_dialogue, body.scenario, body.values, body.agent_a, body.agent_b, body.max_turns
            )
        except ops.DialogueInputError as exc:
            raise HTTPException(400, str(exc)) from None

        def launch():
            try:
                result = ops.launch_dialogue(meta)
                if not result.get("ok"):
                    api.log.warning("Dialogue %s did not start: %s", meta.id, result.get("error"))
            except Exception:
                api.log.exception("Dialogue %s failed to launch", meta.id)

        # Starting both agents takes as long as they take to boot: the request returns now, and the
        # dialogue shows as `starting` until its orchestrator runs.
        threading.Thread(target=launch, name=f"dialogue-launch-{meta.id}", daemon=True).start()
        return {"id": meta._dir().name}

    @api.router.get("/dialogues/{dialogue_id}/turns")
    async def dialogue_turns(dialogue_id: str):
        turns = await asyncio.to_thread(ops.get_turns, exact_name(dialogue_id))
        if turns is None:
            raise HTTPException(404, "No such dialogue")
        return turns

    @api.router.get("/dialogues/{dialogue_id}/files")
    async def dialogue_files(dialogue_id: str):
        rows = await asyncio.to_thread(ops.list_files, exact_name(dialogue_id))
        if rows is None:
            raise HTTPException(404, "No such dialogue")
        return rows

    @api.router.get("/dialogues/{dialogue_id}/file")
    async def dialogue_file(dialogue_id: str, path: str):
        found = await asyncio.to_thread(ops.read_file, exact_name(dialogue_id), path)
        if found is None:
            raise HTTPException(404, "No such file")
        return found

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
