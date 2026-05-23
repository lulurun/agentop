"""Local Agent Session Dashboard — FastAPI backend."""

import asyncio
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from agentop import ops, scanner

# ---------------------------------------------------------------------------
# Cache refreshed by background task
# ---------------------------------------------------------------------------

_cache: dict[str, Any] = {
    "sessions": [],
    "files": [],
    "last_updated": 0,
}
_cache_lock = asyncio.Lock()

REFRESH_INTERVAL = 5  # seconds

STATIC_DIR = Path(__file__).parent.parent / "html"


async def _refresh_loop():
    while True:
        try:
            sessions = await asyncio.get_event_loop().run_in_executor(None, ops.get_sessions)
            files = await asyncio.get_event_loop().run_in_executor(None, scanner.scan_agent_dirs)
            async with _cache_lock:
                _cache["sessions"] = sessions
                _cache["files"] = files
                _cache["last_updated"] = time.time()
        except Exception as exc:  # noqa: BLE001
            print(f"[agentop] refresh error: {exc}")
        await asyncio.sleep(REFRESH_INTERVAL)


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(_refresh_loop())
    yield
    task.cancel()


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(title="Agent Session Dashboard", lifespan=lifespan)


# ---------------------------------------------------------------------------
# API routes
# ---------------------------------------------------------------------------


@app.get("/api/health")
async def health():
    return {"status": "ok", "last_updated": _cache["last_updated"]}


@app.get("/api/info")
async def info():
    return {"home": os.path.expanduser("~")}


@app.get("/api/sessions")
async def list_sessions():
    async with _cache_lock:
        return _cache["sessions"]


@app.post("/api/sessions/start")
async def create_and_start_session(body: dict):
    tool = body.get("tool", "claude")
    cwd = os.path.expanduser(body.get("cwd", ""))
    short_name = body.get("short_name", "").strip()
    if not cwd:
        raise HTTPException(status_code=400, detail="cwd is required")
    if not short_name:
        raise HTTPException(status_code=400, detail="short_name is required")
    if len(short_name) >= 32:
        raise HTTPException(status_code=400, detail="short_name must be under 32 characters")
    result = await asyncio.get_event_loop().run_in_executor(
        None, ops.start, tool, cwd, short_name
    )
    if not result.get("ok"):
        raise HTTPException(status_code=500, detail=result.get("error", "Failed to start session"))
    return {"ok": True, "name": result["name"], "pid": result.get("pid")}


@app.post("/api/sessions/{name}/stop")
async def stop_session(name: str):
    async with _cache_lock:
        cached_sessions = list(_cache["sessions"])
    result = await asyncio.get_event_loop().run_in_executor(
        None, ops.stop, name, cached_sessions
    )
    if not result.get("ok"):
        error = result["error"]
        if "not found" in error.lower():
            raise HTTPException(status_code=404, detail=error)
        if "not a managed" in error.lower():
            raise HTTPException(status_code=403, detail=error)
        raise HTTPException(status_code=500, detail=error)
    return result


@app.post("/api/sessions/{name}/send")
async def send_to_session(name: str, body: dict):
    text = body.get("text", "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is required")
    async with _cache_lock:
        cached_sessions = list(_cache["sessions"])
    result = await asyncio.get_event_loop().run_in_executor(
        None, ops.send, name, text, cached_sessions
    )
    if not result.get("ok"):
        error = result["error"]
        if "not found" in error.lower():
            raise HTTPException(status_code=404, detail=error)
        if "not a managed" in error.lower():
            raise HTTPException(status_code=403, detail=error)
        raise HTTPException(status_code=500, detail=error)
    return {"ok": True}


@app.get("/api/sessions/{name:path}")
async def get_session(name: str):
    async with _cache_lock:
        for s in _cache["sessions"]:
            if s["name"] == name:
                result = dict(s)
                if result.get("pid"):
                    result["process_tree"] = await asyncio.get_event_loop().run_in_executor(
                        None, scanner.get_process_tree, result["pid"]
                    )
                    result["recent_project_files"] = await asyncio.get_event_loop().run_in_executor(
                        None, scanner.get_recent_project_files, result.get("cwd") or "", 15
                    )
                else:
                    result["process_tree"] = []
                    result["recent_project_files"] = []
                return result
    raise HTTPException(status_code=404, detail="Session not found")


@app.post("/api/sessions/{name:path}")
async def update_session(name: str, body: dict):
    description = body.get("description")
    if description is not None:
        await asyncio.get_event_loop().run_in_executor(
            None, ops.set_description, name, description
        )
    return {"ok": True}


@app.get("/api/files/recent")
async def recent_files():
    async with _cache_lock:
        return _cache["files"]


# ---------------------------------------------------------------------------
# Serve static dashboard UI
# ---------------------------------------------------------------------------


@app.get("/")
async def root():
    return FileResponse(STATIC_DIR / "index.html")


# Mount static files last so API routes take priority
app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8765)
