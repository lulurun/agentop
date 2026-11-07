"""The old dashboard's routes, unchanged in behaviour, until the new UI replaces them.

These have no token. HostOriginGuard is their only protection, so app.py does
not register them on a non-loopback bind.
"""

from __future__ import annotations

import asyncio
import os

from fastapi import APIRouter, HTTPException, WebSocket

from agentop import ops, scanner
from agentop.dialogue import ops as dialogue_ops

from . import terminal
from .store import SessionStore


def create_legacy_router(store: SessionStore) -> APIRouter:
    router = APIRouter()

    def cached() -> list[dict]:
        return store.last_scan

    @router.get("/api/health")
    async def health():
        return {"status": "ok", "last_updated": store.last_updated}

    @router.get("/api/info")
    async def info():
        return {"home": os.path.expanduser("~")}

    @router.get("/api/sessions")
    async def list_sessions():
        return list(cached())

    @router.post("/api/sessions/start")
    async def create_and_start_session(body: dict):
        tool = body.get("tool", "claude")
        cwd = os.path.expanduser(body.get("cwd", ""))
        short_name = body.get("short_name", "").strip()
        if not cwd:
            raise HTTPException(status_code=400, detail="cwd is required")
        if not ops.is_valid_short_name(short_name):
            raise HTTPException(status_code=400, detail="short_name must be 1-32 letters, digits, '-' or '_'")
        result = await asyncio.to_thread(ops.start, tool, cwd, short_name)
        if not result.get("ok"):
            raise HTTPException(status_code=500, detail=result.get("error", "Failed to start session"))
        return {"ok": True, "name": result["name"], "pid": result.get("pid")}

    @router.get("/api/saved-sessions")
    async def list_saved_sessions(tool: str | None = None, limit: int = 50):
        return await asyncio.to_thread(ops.get_saved_sessions, tool, limit, list(cached()))

    @router.delete("/api/saved-sessions/{tool}/{session_id:path}")
    async def delete_saved_session(tool: str, session_id: str):
        result = await asyncio.to_thread(ops.delete_saved_session, tool, session_id)
        if not result["ok"]:
            raise HTTPException(status_code=400, detail=result["error"])
        return result

    @router.post("/api/sessions/resume")
    async def resume_session(body: dict):
        tool = body.get("tool", "")
        session_id = body.get("session_id", "").strip()
        cwd = os.path.expanduser(body.get("cwd", ""))
        short_name = body.get("short_name", "").strip()
        if not tool:
            raise HTTPException(status_code=400, detail="tool is required")
        if not session_id:
            raise HTTPException(status_code=400, detail="session_id is required")
        if not cwd:
            raise HTTPException(status_code=400, detail="cwd is required")
        result = await asyncio.to_thread(ops.resume_session, tool, session_id, cwd, short_name)
        if not result.get("ok"):
            raise HTTPException(status_code=500, detail=result.get("error", "Failed to resume session"))
        return {"ok": True, "name": result["name"], "pid": result.get("pid")}

    @router.patch("/api/sessions/{name:path}/description")
    async def set_session_description(name: str, body: dict):
        description = (body.get("description") or "").strip()
        result = await asyncio.to_thread(ops.set_description, name, description)
        if not result.get("ok"):
            raise HTTPException(status_code=404, detail=result.get("error"))
        for s in cached():
            if s["name"] == name:
                if description:
                    s["description"] = description
                else:
                    s.pop("description", None)
                break
        return {"ok": True}

    @router.post("/api/sessions/{name}/stop")
    async def stop_session(name: str):
        result = await asyncio.to_thread(ops.stop, name, list(cached()))
        if not result.get("ok"):
            error = result["error"]
            if "not found" in error.lower():
                raise HTTPException(status_code=404, detail=error)
            if "not a managed" in error.lower():
                raise HTTPException(status_code=403, detail=error)
            raise HTTPException(status_code=500, detail=error)
        return result

    @router.get("/api/sessions/{name:path}")
    async def get_session(name: str):
        for s in cached():
            if s["name"] == name:
                result = dict(s)
                pid = result.get("pid")
                result["process_tree"] = await asyncio.to_thread(scanner.get_process_tree, pid) if pid else []
                return result
        raise HTTPException(status_code=404, detail="Session not found")

    @router.get("/api/dialogues")
    async def list_dialogues():
        return await asyncio.to_thread(dialogue_ops.list_dialogues)

    @router.get("/api/dialogues/{dialogue_id}")
    async def get_dialogue(dialogue_id: str):
        detail = await asyncio.to_thread(dialogue_ops.get_dialogue, dialogue_id)
        if detail is None:
            raise HTTPException(status_code=404, detail="Dialogue not found")
        return detail

    @router.post("/api/dialogues/{dialogue_id}/stop")
    async def stop_dialogue(dialogue_id: str):
        result = await asyncio.to_thread(dialogue_ops.stop_dialogue, dialogue_id)
        if not result.get("ok"):
            raise HTTPException(status_code=404, detail=result.get("error"))
        return result

    @router.delete("/api/dialogues/{dialogue_id}")
    async def remove_dialogue(dialogue_id: str):
        result = await asyncio.to_thread(dialogue_ops.remove_dialogue, dialogue_id)
        if not result.get("ok"):
            raise HTTPException(status_code=404, detail=result.get("error"))
        return result

    @router.websocket("/ws/sessions/{name}/terminal")
    async def session_terminal(ws: WebSocket, name: str):
        await ws.accept()
        session = next((s for s in cached() if s["name"] == name), None)
        if session is None:
            await ws.send_text("Session not found.\r\n")
            await ws.close()
            return
        tmux_name = (session.get("tmux") or {}).get("session") or name
        await terminal.bridge(ws, tmux_name, 220, 50, send_closed=False)

    return router
