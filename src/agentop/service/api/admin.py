"""The service itself: health, restart, stop, update, what it runs, its plugins and tools, and the folders it can see."""

from __future__ import annotations

import asyncio
import os
import re
import subprocess
import time
from importlib import metadata
from pathlib import Path

from fastapi import APIRouter, HTTPException

from agentop import agentclis
from agentop.tmux import Session

from .. import API_VERSION, manager, update
from ..models import (
    Accepted,
    DirListing,
    Health,
    PatchPlugin,
    PluginInfo,
    ServiceInfo,
    ToolInfo,
    UpdateInfo,
)
from .context import AUTHED, REPO_ROOT, Context, Ctx

# Long enough for the response to leave before the service goes away.
LIFECYCLE_DELAY_SECONDS = 0.3

router = APIRouter()


WEB_INDEX = Path(__file__).resolve().parents[1] / "webdist" / "index.html"
_ENTRY = re.compile(r'src="[^"]*assets/([^"/]+\.js)"')


def ui_build() -> str | None:
    """Read on every call: an update or build-ui.sh replaces the app without a restart."""
    try:
        found = _ENTRY.search(WEB_INDEX.read_text())
    except OSError:
        return None
    return found.group(1) if found else None


def version() -> str:
    try:
        return metadata.version("agentop")
    except metadata.PackageNotFoundError:
        return "0.0.0"


def _install_kind() -> str:
    return "git" if (REPO_ROOT / ".git").exists() else "unknown"


def _git_commit() -> str | None:
    if not (REPO_ROOT / ".git").exists():
        return None
    try:
        r = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        return r.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def _capabilities() -> tuple[manager.ServiceManager, dict[str, bool]]:
    current = manager.for_process(os.getpid())
    updatable = current.can_control and _install_kind() == "git"
    return current, {"stop": current.can_control, "restart": current.can_control, "update": updatable}


# No token needed, so it answers whether the service is up and nothing else.
@router.get("/health", response_model=Health)
async def health():
    return {"status": "ok"}


async def _after_responding(action) -> None:
    await asyncio.sleep(LIFECYCLE_DELAY_SECONDS)
    await asyncio.to_thread(action)


async def _lifecycle(ctx: Context, action: str) -> dict:
    current, allowed = await asyncio.to_thread(_capabilities)
    if not allowed[action]:
        raise HTTPException(409, "This service can't control itself.")
    task = asyncio.create_task(_after_responding(getattr(current, action)))
    ctx.background.add(task)
    task.add_done_callback(ctx.background.discard)
    return {"status": action}


# Whoever runs this service, systemd or `agentop serve`, the service acts on
# itself. There is no start: a stopped service can't answer.
@router.post("/admin/restart", response_model=Accepted, status_code=202, dependencies=AUTHED)
async def restart(ctx: Ctx):
    return await _lifecycle(ctx, "restart")


@router.post("/admin/stop", response_model=Accepted, status_code=202, dependencies=AUTHED)
async def stop(ctx: Ctx):
    return await _lifecycle(ctx, "stop")


@router.get("/admin/update", response_model=UpdateInfo, dependencies=AUTHED)
async def update_info(ctx: Ctx):
    running = await asyncio.to_thread(update.is_running)
    return {
        **await asyncio.to_thread(update.available, REPO_ROOT, ctx.settings.update_from),
        "running": running,
        "last_run": update.read_status(),
    }


@router.post("/admin/update", response_model=Accepted, status_code=202, dependencies=AUTHED)
async def start_update(ctx: Ctx):
    current, allowed = await asyncio.to_thread(_capabilities)
    if not allowed["update"]:
        raise HTTPException(409, "This service can't update itself. It needs to run from a git checkout.")
    scheme = "https" if ctx.settings.tls else "http"
    health_url = f"{scheme}://127.0.0.1:{ctx.settings.port}/api/v2/health"
    try:
        await asyncio.to_thread(update.launch, REPO_ROOT, health_url, current, ctx.settings.update_from)
    except update.UpdateError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"status": "updating"}


@router.get("/service", response_model=ServiceInfo, dependencies=AUTHED)
async def service(ctx: Ctx):
    current, allowed = await asyncio.to_thread(_capabilities)
    return {
        "version": version(),
        "api_version": API_VERSION,
        "name": ctx.identity.name,
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
        "ui_build": await asyncio.to_thread(ui_build),
    }


@router.get("/plugins", response_model=list[PluginInfo], dependencies=AUTHED)
async def list_plugins(ctx: Ctx):
    return ctx.plugins.list() if ctx.plugins else []


@router.patch("/plugins/{plugin_id}", response_model=PluginInfo, dependencies=AUTHED)
async def patch_plugin(plugin_id: str, body: PatchPlugin, ctx: Ctx):
    if ctx.plugins is None:
        raise HTTPException(404, "No such plugin")
    try:
        return await ctx.plugins.set_enabled(plugin_id, body.enabled)
    except KeyError:
        raise HTTPException(404, "No such plugin") from None


@router.get("/tools", response_model=list[ToolInfo], dependencies=AUTHED)
async def tools(ctx: Ctx):
    if ctx.available is None:
        found = await asyncio.to_thread(agentclis.installed, agentclis.AGENTS)
        ctx.available = [agent.name for agent in found]
    return [
        {
            "name": agent.name,
            "installed": agent.name in ctx.available,
            "supports_resume": agent.get_resume_cmd("x") is not None,
        }
        for agent in agentclis.AGENTS
    ]


@router.get("/fs/dirs", response_model=DirListing, dependencies=AUTHED)
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
