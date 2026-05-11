"""Local Agent Session Dashboard — FastAPI backend."""

import asyncio
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from dashboard import registry, scanner

# ---------------------------------------------------------------------------
# Cache refreshed by background task
# ---------------------------------------------------------------------------

_cache: dict[str, Any] = {
    "sessions": [],
    "files": [],
    "last_updated": 0,
    # pid -> cpu_percent initialised flag (first call returns 0)
    "_cpu_init": set(),
}
_cache_lock = asyncio.Lock()

REFRESH_INTERVAL = 5  # seconds


async def _refresh_loop():
    while True:
        try:
            reg = await asyncio.get_event_loop().run_in_executor(None, registry.load)
            sessions = await asyncio.get_event_loop().run_in_executor(
                None, scanner.build_sessions, reg
            )
            files = await asyncio.get_event_loop().run_in_executor(
                None, scanner.scan_agent_dirs
            )

            # Refresh CPU readings (second call gives real values)
            live_pids = [s["pid"] for s in sessions if s["pid"]]
            cpu_map = await asyncio.get_event_loop().run_in_executor(
                None, scanner.refresh_cpu_percent, live_pids
            )
            for s in sessions:
                if s["pid"] and s["pid"] in cpu_map:
                    s["cpu_percent"] = round(cpu_map[s["pid"]], 1)

            async with _cache_lock:
                _cache["sessions"] = sessions
                _cache["files"] = files
                _cache["last_updated"] = time.time()
        except Exception as exc:  # noqa: BLE001
            print(f"[dashboard] refresh error: {exc}")
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

STATIC_DIR = Path(__file__).parent / "static"


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
    description = body.get("description", "")
    if not cwd:
        raise HTTPException(status_code=400, detail="cwd is required")
    result = await asyncio.get_event_loop().run_in_executor(
        None, scanner.start_session_with_tool, tool, cwd
    )
    if not result.get("ok"):
        raise HTTPException(status_code=500, detail=result.get("error", "Failed to start session"))
    name = result["name"]
    registry.upsert_session(name, {
        "tool": tool,
        "cwd": cwd,
        "description": description,
        "status": "running",
        "tmux_session": result["tmux_session"],
    })
    return {"ok": True, "name": name, "pid": result.get("pid")}


@app.post("/api/sessions/{name}/start")
async def start_session(name: str, body: dict):
    allowed = {"tool", "cwd", "description", "tmux_session"}
    filtered = {k: v for k, v in body.items() if k in allowed}
    filtered["status"] = "running"
    registry.upsert_session(name, filtered)
    cwd = os.path.expanduser(filtered.get("cwd", ""))
    tmux_name = filtered.get("tmux_session") or name
    if cwd:
        await asyncio.get_event_loop().run_in_executor(
            None, scanner.start_tmux_session, tmux_name, cwd
        )
    return {"ok": True}


@app.post("/api/sessions/{name}/stop")
async def stop_session(name: str):
    entry = registry.get_session(name)
    if entry is None:
        raise HTTPException(status_code=404, detail="Session not found")
    cwd = os.path.expanduser(entry.get("cwd", ""))
    tmux_name = entry.get("tmux_session") or name
    result = await asyncio.get_event_loop().run_in_executor(
        None, scanner.stop_session, cwd, tmux_name
    )
    registry.set_status(name, "stopped")
    return {"ok": True, **result}


@app.get("/api/sessions/{name:path}")
async def get_session(name: str):
    async with _cache_lock:
        for s in _cache["sessions"]:
            if s["name"] == name:
                # Enrich with process tree and recent files on demand
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
    allowed = {"tool", "cwd", "description", "status", "tmux_session", "tags"}
    filtered = {k: v for k, v in body.items() if k in allowed}
    registry.upsert_session(name, filtered)
    return {"ok": True}


@app.delete("/api/sessions/{name:path}")
async def delete_session(name: str):
    deleted = registry.delete_session(name)
    if not deleted:
        raise HTTPException(status_code=404, detail="Session not found in registry")
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

    uvicorn.run(
        "dashboard.main:app",
        host="127.0.0.1",
        port=8765,
        reload=False,
    )
