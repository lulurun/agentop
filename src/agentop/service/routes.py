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
import re
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
from agentop.dialogue import model as dialogue_model
from agentop.dialogue import ops as dialogue_ops
from agentop.tmux import Session

from . import API_VERSION, auth, files, manager, terminal, update
from .config import Settings
from .identity import Identity
from .models import (
    Accepted,
    CreateSession,
    DialogueDetail,
    DialogueSummary,
    DirListing,
    FileListing,
    GitChanges,
    Health,
    PatchPlugin,
    PatchSession,
    PluginInfo,
    SavedSession,
    ServiceInfo,
    SessionModel,
    Ticket,
    TicketRequest,
    ToolInfo,
    UpdateInfo,
)
from .plugins import PluginHost
from .store import SessionStore, StoreError

WS_INVALID_TICKET = 4401
WS_NO_SESSION = 4404
# Long enough for the response to leave before the service goes away.
LIFECYCLE_DELAY_SECONDS = 0.3

_REPO_ROOT = Path(__file__).resolve().parents[3]


def _dialogue_dir_name(dialogue_id: str) -> str:
    """A dialogue ID names a directory that remove deletes, so it must be exactly that name.

    DialogueMeta.load also accepts a fragment of an ID, which is handy on the
    command line but not behind a delete route.
    """
    known = re.fullmatch(r"[A-Za-z0-9_-][A-Za-z0-9._-]*", dialogue_id) and (
        dialogue_model.DIALOGUES_DIR / dialogue_id / "meta.json"
    ).exists()
    if not known:
        raise HTTPException(404, "No such dialogue")
    return dialogue_id


def _install_kind() -> str:
    return "git" if (_REPO_ROOT / ".git").exists() else "unknown"


@dataclass
class Context:
    settings: Settings
    identity: Identity
    store: SessionStore
    tickets: auth.TicketStore = field(default_factory=auth.TicketStore)
    started_at: float = field(default_factory=time.time)
    background: set = field(default_factory=set)
    # Set when the identity came from service.json. Then a token rotated by the
    # CLI takes effect at once, with no restart, and the old one stops working.
    identity_file: Path | None = None
    _identity_mtime: int | None = None
    plugins: PluginHost | None = None

    def current_identity(self) -> Identity:
        if self.identity_file is None:
            return self.identity
        try:
            mtime = self.identity_file.stat().st_mtime_ns
        except OSError:
            return self.identity  # keep the last good one rather than locking everyone out
        if mtime != self._identity_mtime:
            try:
                data = json.loads(self.identity_file.read_text())
                self.identity = Identity(data["token"], data["instance_id"], data["name"])
            except (OSError, ValueError, KeyError):
                return self.identity
            self._identity_mtime = mtime
        return self.identity


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
        if not auth.bearer_matches(authorization, ctx.current_identity().token):
            raise HTTPException(401, "Missing or invalid token", {"WWW-Authenticate": "Bearer"})

    authed = [Depends(require_token)]
    if ctx.plugins is not None:
        # Plugin routes go under /plugins/{id}/, behind the same token.
        ctx.plugins.attach(v2, authed)

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

    def capabilities() -> tuple[manager.ServiceManager, dict[str, bool]]:
        current = manager.for_process(os.getpid())
        updatable = current.can_control and _install_kind() == "git"
        return current, {"stop": current.can_control, "restart": current.can_control, "update": updatable}

    async def after_responding(action) -> None:
        await asyncio.sleep(LIFECYCLE_DELAY_SECONDS)
        await asyncio.to_thread(action)

    def lifecycle(action: str):
        async def route():
            current, allowed = await asyncio.to_thread(capabilities)
            if not allowed[action]:
                raise HTTPException(409, "This service can't control itself.")
            task = asyncio.create_task(after_responding(getattr(current, action)))
            ctx.background.add(task)
            task.add_done_callback(ctx.background.discard)
            return {"status": action}

        return route

    # Admin actions. Whoever runs this service, systemd or `agentop serve`, the
    # service acts on itself. There is no start: a stopped service can't answer.
    v2.post("/admin/restart", response_model=Accepted, status_code=202, dependencies=authed)(lifecycle("restart"))
    v2.post("/admin/stop", response_model=Accepted, status_code=202, dependencies=authed)(lifecycle("stop"))

    @v2.get("/admin/update", response_model=UpdateInfo, dependencies=authed)
    async def update_info():
        running = await asyncio.to_thread(update.is_running)
        return {
            **await asyncio.to_thread(update.available, _REPO_ROOT),
            "running": running,
            "last_run": update.read_status(),
        }

    @v2.post("/admin/update", response_model=Accepted, status_code=202, dependencies=authed)
    async def start_update():
        current, allowed = await asyncio.to_thread(capabilities)
        if not allowed["update"]:
            raise HTTPException(409, "This service can't update itself. It needs to run from a git checkout.")
        scheme = "https" if ctx.settings.tls else "http"
        health_url = f"{scheme}://127.0.0.1:{ctx.settings.port}/api/v2/health"
        try:
            await asyncio.to_thread(update.launch, _REPO_ROOT, health_url, current)
        except update.UpdateError as exc:
            raise HTTPException(409, str(exc)) from exc
        return {"status": "updating"}

    @v2.get("/service", response_model=ServiceInfo, dependencies=authed)
    async def service():
        current, allowed = await asyncio.to_thread(capabilities)
        return {
            **health_body(),
            "commit": await asyncio.to_thread(_git_commit),
            "uptime_seconds": int(time.time() - ctx.started_at),
            "started_at": ctx.started_at,
            "pid": os.getpid(),
            "tmux_version": await asyncio.to_thread(Session.version),
            "manager": current.name,
            "capabilities": allowed,
            "install_kind": _install_kind(),
            "tls": ctx.settings.tls,
            "plugins": len(ctx.plugins.plugins) if ctx.plugins else 0,
        }

    @v2.get("/plugins", response_model=list[PluginInfo], dependencies=authed)
    async def list_plugins():
        return ctx.plugins.list() if ctx.plugins else []

    @v2.patch("/plugins/{plugin_id}", response_model=PluginInfo, dependencies=authed)
    async def patch_plugin(plugin_id: str, body: PatchPlugin):
        if ctx.plugins is None:
            raise HTTPException(404, "No such plugin")
        try:
            return await ctx.plugins.set_enabled(plugin_id, body.enabled)
        except KeyError:
            raise HTTPException(404, "No such plugin") from None

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

    @v2.get("/saved-sessions", response_model=list[SavedSession], dependencies=authed)
    async def saved_sessions(tool: str | None = None, limit: int = 50):
        return await asyncio.to_thread(ops.get_saved_sessions, tool, limit, ctx.store.last_scan)

    @v2.delete("/saved-sessions/{tool}/{session_id:path}", dependencies=authed)
    async def delete_saved_session(tool: str, session_id: str):
        result = await asyncio.to_thread(ops.delete_saved_session, tool, session_id)
        if not result["ok"]:
            raise HTTPException(400, result["error"])
        return result

    @v2.get("/dialogues", response_model=list[DialogueSummary], dependencies=authed)
    async def list_dialogues():
        return await asyncio.to_thread(dialogue_ops.list_dialogues)

    @v2.get("/dialogues/{dialogue_id}", response_model=DialogueDetail, dependencies=authed)
    async def get_dialogue(dialogue_id: str):
        detail = await asyncio.to_thread(dialogue_ops.get_dialogue, _dialogue_dir_name(dialogue_id))
        if detail is None:
            raise HTTPException(404, "No such dialogue")
        return detail

    @v2.post("/dialogues/{dialogue_id}/stop", dependencies=authed)
    async def stop_dialogue(dialogue_id: str):
        result = await asyncio.to_thread(dialogue_ops.stop_dialogue, _dialogue_dir_name(dialogue_id))
        if not result["ok"]:
            raise HTTPException(404, result["error"])
        return result

    @v2.delete("/dialogues/{dialogue_id}", dependencies=authed)
    async def remove_dialogue(dialogue_id: str):
        result = await asyncio.to_thread(dialogue_ops.remove_dialogue, _dialogue_dir_name(dialogue_id))
        if not result["ok"]:
            raise HTTPException(404, result["error"])
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
