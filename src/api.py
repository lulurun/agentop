"""Local Agent Session Dashboard — FastAPI backend."""

import asyncio
import fcntl
import os
import pty
import struct
import termios
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
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
            sessions = await asyncio.to_thread(ops.get_sessions)
            files = await asyncio.to_thread(scanner.scan_agent_dirs)
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
        return list(_cache["sessions"])


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
    result = await asyncio.to_thread(ops.start, tool, cwd, short_name)
    if not result.get("ok"):
        raise HTTPException(status_code=500, detail=result.get("error", "Failed to start session"))
    return {"ok": True, "name": result["name"], "pid": result.get("pid")}


@app.get("/api/saved-sessions")
async def list_saved_sessions(tool: str | None = None, limit: int = 50):
    async with _cache_lock:
        live = list(_cache["sessions"])
    return await asyncio.to_thread(ops.get_saved_sessions, tool, limit, live)


@app.delete("/api/saved-sessions/{tool}/{session_id:path}")
async def delete_saved_session(tool: str, session_id: str):
    result = await asyncio.to_thread(ops.delete_saved_session, tool, session_id)
    if not result["ok"]:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@app.post("/api/sessions/resume")
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


@app.patch("/api/sessions/{name:path}/description")
async def set_session_description(name: str, body: dict):
    description = (body.get("description") or "").strip()
    result = await asyncio.to_thread(ops.set_description, name, description)
    if not result.get("ok"):
        raise HTTPException(status_code=404, detail=result.get("error"))
    async with _cache_lock:
        for s in _cache["sessions"]:
            if s["name"] == name:
                if description:
                    s["description"] = description
                else:
                    s.pop("description", None)
                break
    return {"ok": True}


@app.post("/api/sessions/{name}/stop")
async def stop_session(name: str):
    async with _cache_lock:
        cached_sessions = list(_cache["sessions"])
    result = await asyncio.to_thread(ops.stop, name, cached_sessions)
    if not result.get("ok"):
        error = result["error"]
        if "not found" in error.lower():
            raise HTTPException(status_code=404, detail=error)
        if "not a managed" in error.lower():
            raise HTTPException(status_code=403, detail=error)
        raise HTTPException(status_code=500, detail=error)
    return result


@app.get("/api/sessions/{name:path}")
async def get_session(name: str):
    async with _cache_lock:
        for s in _cache["sessions"]:
            if s["name"] == name:
                result = dict(s)
                if result.get("pid"):
                    result["process_tree"] = await asyncio.to_thread(scanner.get_process_tree, result["pid"])
                    result["recent_project_files"] = await asyncio.to_thread(
                        scanner.get_recent_project_files, result.get("cwd") or "", 15
                    )
                else:
                    result["process_tree"] = []
                    result["recent_project_files"] = []
                return result
    raise HTTPException(status_code=404, detail="Session not found")


@app.get("/api/files/recent")
async def recent_files():
    async with _cache_lock:
        return list(_cache["files"])


# ---------------------------------------------------------------------------
# Terminal WebSocket — PTY bridge to a tmux session
# ---------------------------------------------------------------------------


def _set_pty_size(fd: int, cols: int, rows: int) -> None:
    packed = struct.pack("HHHH", rows, cols, 0, 0)
    fcntl.ioctl(fd, termios.TIOCSWINSZ, packed)


@app.websocket("/ws/sessions/{name}/terminal")
async def session_terminal(ws: WebSocket, name: str):
    await ws.accept()

    # Resolve tmux session name from cache
    async with _cache_lock:
        session = next((s for s in _cache["sessions"] if s["name"] == name), None)
    if session is None:
        await ws.send_text("Session not found.\r\n")
        await ws.close()
        return

    tmux_name = (session.get("tmux") or {}).get("session") or name

    master_fd, slave_fd = pty.openpty()
    try:
        _set_pty_size(master_fd, 220, 50)
        proc = await asyncio.create_subprocess_exec(
            "tmux", "attach-session", "-t", tmux_name,
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            close_fds=True,
        )
    except Exception as exc:
        os.close(master_fd)
        os.close(slave_fd)
        await ws.send_text(f"Failed to attach: {exc}\r\n")
        await ws.close()
        return

    os.close(slave_fd)

    loop = asyncio.get_event_loop()

    async def pty_to_ws():
        """Read PTY output and forward to WebSocket."""
        while True:
            try:
                data = await loop.run_in_executor(None, os.read, master_fd, 4096)
            except OSError:
                break
            try:
                await ws.send_bytes(data)
            except Exception:
                break

    async def ws_to_pty():
        """Read WebSocket input and write to PTY (or handle resize JSON)."""
        import json
        while True:
            try:
                msg = await ws.receive()
            except (WebSocketDisconnect, Exception):
                break
            if msg["type"] == "websocket.disconnect":
                break
            if "bytes" in msg and msg["bytes"] is not None:
                try:
                    os.write(master_fd, msg["bytes"])
                except OSError:
                    break
            elif "text" in msg and msg["text"] is not None:
                try:
                    payload = json.loads(msg["text"])
                    if payload.get("type") == "resize":
                        cols = int(payload.get("cols", 80))
                        rows = int(payload.get("rows", 24))
                        _set_pty_size(master_fd, cols, rows)
                except Exception:
                    pass

    try:
        done, pending = await asyncio.wait(
            [
                asyncio.ensure_future(pty_to_ws()),
                asyncio.ensure_future(ws_to_pty()),
            ],
            return_when=asyncio.FIRST_COMPLETED,
        )
    finally:
        for t in pending:
            t.cancel()
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        os.close(master_fd)
        try:
            await ws.close()
        except Exception:
            pass


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

    uvicorn.run(app, host="127.0.0.1", port=9775)
