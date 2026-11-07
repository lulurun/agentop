"""The /api/v2 application. Mounted by app.py, and reachable from any origin.

Access is decided by the token alone, because a client may be loaded from
anywhere. That is safe because nothing here relies on an ambient credential:
without cookies, a foreign page has nothing to ride on. See docs/v2-design.md,
section 7.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from importlib import metadata
from pathlib import Path

from fastapi import (
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Request,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from agentop import ops
from agentop.agentclis import AGENTS
from agentop.tmux import Session

from . import API_VERSION, auth, files, terminal
from .config import Settings
from .identity import Identity
from .models import (
    CreateSession,
    DirListing,
    FileListing,
    GitChanges,
    Health,
    PatchSession,
    ServiceInfo,
    SessionModel,
    Ticket,
    TicketRequest,
    ToolInfo,
)
from .store import SessionStore, StoreError

WS_INVALID_TICKET = 4401
WS_NO_SESSION = 4404

_REPO_ROOT = Path(__file__).resolve().parents[3]


@dataclass
class Context:
    settings: Settings
    identity: Identity
    store: SessionStore
    tickets: auth.TicketStore = field(default_factory=auth.TicketStore)
    started_at: float = field(default_factory=time.time)


def _version() -> str:
    try:
        return metadata.version("agentop")
    except metadata.PackageNotFoundError:
        return "0.0.0"


def _git_commit() -> str | None:
    if not (_REPO_ROOT / ".git").exists():
        return None
    try:
        r = subprocess.run(
            ["git", "-C", str(_REPO_ROOT), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        return r.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def create_v2_app(ctx: Context) -> FastAPI:
    v2 = FastAPI(title="agentop", version=_version(), docs_url=None, redoc_url=None)
    # Outside auth, so that a preflight succeeds. Credentials stay off: tokens
    # are attached deliberately by the client, never sent by the browser.
    v2.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["Authorization", "Content-Type"],
    )

    @v2.exception_handler(StoreError)
    async def store_error(_request: Request, exc: StoreError):
        return JSONResponse({"detail": exc.message}, status_code=exc.status)

    def require_token(authorization: str | None = Header(default=None)) -> None:
        if not auth.bearer_matches(authorization, ctx.identity.token):
            raise HTTPException(401, "Missing or invalid token", {"WWW-Authenticate": "Bearer"})

    authed = [Depends(require_token)]

    def health_body() -> dict:
        return {
            "status": "ok",
            "version": _version(),
            "api_version": API_VERSION,
            "instance_id": ctx.identity.instance_id,
            "name": ctx.identity.name,
        }

    @v2.get("/health", response_model=Health)
    async def health():
        return health_body()

    @v2.get("/service", response_model=ServiceInfo, dependencies=authed)
    async def service():
        return {
            **health_body(),
            "commit": await asyncio.to_thread(_git_commit),
            "uptime_seconds": int(time.time() - ctx.started_at),
            "pid": os.getpid(),
            "tmux_version": await asyncio.to_thread(Session.version),
            "manager": "none",
            "capabilities": {"stop": False, "restart": False, "update": False},
            "install_kind": "git" if (_REPO_ROOT / ".git").exists() else "unknown",
            "tls": ctx.settings.tls,
        }

    @v2.post("/tickets", response_model=Ticket, dependencies=authed)
    async def issue_ticket(body: TicketRequest):
        if body.purpose == "terminal":
            if not body.session_id:
                raise HTTPException(400, "session_id is required")
            scope = auth.terminal_scope(body.session_id)
        else:
            scope = auth.EVENTS_SCOPE
        return {"ticket": ctx.tickets.issue(scope), "expires_in": int(auth.TICKET_TTL_SECONDS)}

    @v2.get("/tools", response_model=list[ToolInfo], dependencies=authed)
    async def tools():
        return [
            {
                "name": agent.name,
                "installed": shutil.which(agent.launch_cmd.split()[0]) is not None,
                "supports_resume": agent.get_resume_cmd("x") is not None,
            }
            for agent in AGENTS
        ]

    @v2.get("/fs/dirs", response_model=DirListing, dependencies=authed)
    async def list_dirs(path: str = "~", hidden: bool = False):
        """Browse the service's filesystem. The path may be on a machine the client cannot see."""
        resolved = Path(os.path.abspath(os.path.expanduser(path)))
        try:
            entries = await asyncio.to_thread(lambda: sorted(e.name for e in os.scandir(resolved) if e.is_dir()))
        except FileNotFoundError:
            raise HTTPException(404, f"No such directory: {resolved}") from None
        except NotADirectoryError:
            raise HTTPException(400, f"Not a directory: {resolved}") from None
        except PermissionError:
            raise HTTPException(403, f"Permission denied: {resolved}") from None
        return {
            "path": str(resolved),
            "parent": str(resolved.parent) if resolved.parent != resolved else None,
            "dirs": [name for name in entries if hidden or not name.startswith(".")],
        }

    @v2.get("/sessions", response_model=list[SessionModel], dependencies=authed)
    async def list_sessions():
        return ctx.store.list()

    @v2.post("/sessions", response_model=SessionModel, status_code=202, dependencies=authed)
    async def create_session(body: CreateSession):
        resume_id = body.resume.session_id if body.resume else None
        return await ctx.store.create(body.tool, body.cwd, body.name, resume_id)

    @v2.get("/sessions/{session_id}", response_model=SessionModel, dependencies=authed)
    async def get_session(session_id: str):
        row = ctx.store.get(session_id)
        if row is None:
            raise HTTPException(404, "Session not found")
        return row

    @v2.patch("/sessions/{session_id}", response_model=SessionModel, dependencies=authed)
    async def patch_session(session_id: str, body: PatchSession):
        return await ctx.store.update(session_id, body.display_name, body.description)

    @v2.post("/sessions/{session_id}/stop", dependencies=authed)
    async def stop_session(session_id: str):
        return await ctx.store.stop(session_id)

    @v2.delete("/sessions/{session_id}", dependencies=authed)
    async def delete_session(session_id: str):
        return await ctx.store.close(session_id)

    def working_directory(session_id: str) -> str:
        row = ctx.store.get(session_id)
        if row is None:
            raise HTTPException(404, "Session not found")
        if not row["cwd"]:
            raise HTTPException(404, "This session has no known working directory")
        return row["cwd"]

    @v2.get("/sessions/{session_id}/files", response_model=FileListing, dependencies=authed)
    async def session_files(session_id: str, path: str = ""):
        try:
            return await asyncio.to_thread(files.list_directory, working_directory(session_id), path)
        except files.OutsideRoot:
            raise HTTPException(403, "That path is outside the session's directory") from None
        except (FileNotFoundError, NotADirectoryError):
            raise HTTPException(404, "No such directory") from None
        except PermissionError:
            raise HTTPException(403, "Permission denied") from None

    @v2.get("/sessions/{session_id}/changes", response_model=GitChanges, dependencies=authed)
    async def session_changes(session_id: str):
        try:
            return await asyncio.to_thread(files.git_changes, working_directory(session_id))
        except FileNotFoundError:
            raise HTTPException(404, "No such directory") from None

    @v2.get("/saved-sessions", dependencies=authed)
    async def saved_sessions(tool: str | None = None, limit: int = 50):
        return await asyncio.to_thread(ops.get_saved_sessions, tool, limit, ctx.store.last_scan)

    @v2.delete("/saved-sessions/{tool}/{session_id:path}", dependencies=authed)
    async def delete_saved_session(tool: str, session_id: str):
        result = await asyncio.to_thread(ops.delete_saved_session, tool, session_id)
        if not result["ok"]:
            raise HTTPException(400, result["error"])
        return result

    async def _reject(ws: WebSocket, code: int) -> None:
        # Accept first: a browser cannot see a close code from a refused handshake.
        await ws.accept()
        await ws.close(code)

    @v2.websocket("/ws/events")
    async def events(ws: WebSocket, ticket: str | None = None):
        if not ctx.tickets.consume(ticket, auth.EVENTS_SCOPE):
            await _reject(ws, WS_INVALID_TICKET)
            return
        await ws.accept()
        queue = ctx.store.subscribe()

        async def send_events():
            while True:
                await ws.send_text(json.dumps(await queue.get()))

        async def wait_for_disconnect():
            while (await ws.receive())["type"] != "websocket.disconnect":
                pass

        tasks = [asyncio.ensure_future(send_events()), asyncio.ensure_future(wait_for_disconnect())]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            ctx.store.unsubscribe(queue)
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    @v2.websocket("/ws/sessions/{session_id}/terminal")
    async def session_terminal(ws: WebSocket, session_id: str, ticket: str | None = None, cols: int = 80, rows: int = 24):
        if not ctx.tickets.consume(ticket, auth.terminal_scope(session_id)):
            await _reject(ws, WS_INVALID_TICKET)
            return
        # Ask tmux, not the store: a session can be attached before the next tick sees it.
        tmux_session = ctx.store.tmux_session(session_id)
        if not await asyncio.to_thread(Session.has, tmux_session):
            await _reject(ws, WS_NO_SESSION)
            return
        await ws.accept()
        try:
            await terminal.bridge(ws, tmux_session, cols, rows)
        except WebSocketDisconnect:
            pass

    return v2
