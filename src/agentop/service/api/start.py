"""Sessions' start from a prompt: probe a prompt, launch what it chose, list what was started, and the settings (docs/0_04_start.md)."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException

from agentop import agentclis, projects, starting

from ..models import SessionModel, StartDecision, StartLaunch, StartProbe, StartRecent, StartSettings, StartSettingsPatch, StartTool
from .context import AUTHED, Ctx
from .projects import available_tools

router = APIRouter(prefix="/start", dependencies=AUTHED)


@router.post("/probe", response_model=StartDecision)
async def probe(body: StartProbe, ctx: Ctx):
    installed = await available_tools(ctx)
    return await asyncio.to_thread(starting.probe, body.prompt, installed)


@router.post("/launch", response_model=SessionModel, status_code=202)
async def launch(body: StartLaunch, ctx: Ctx):
    name = ctx.store.free_name(projects.DEFAULT, starting.slug(body.name) or "session")
    await ctx.store.make_room()
    row = await ctx.store.create(
        body.tool, body.cwd, name, project=projects.DEFAULT, prompt=body.prompt, model=body.model, description=body.title or None
    )
    await asyncio.to_thread(
        starting.record,
        {"event": "launched", "probe": body.probe, "session": row["id"], "name": name, "title": body.title,
         "tool": body.tool, "model": body.model, "cwd": row["cwd"], "changed": body.changed},
    )
    return row


@router.get("/recent", response_model=list[StartRecent])
async def recent():
    return await asyncio.to_thread(starting.recent)


@router.get("/models", response_model=list[StartTool])
async def models(ctx: Ctx):
    installed = await available_tools(ctx)
    seen = await asyncio.to_thread(starting.quotas, installed)
    return [
        {
            "name": agent.name,
            "installed": agent.name in installed,
            "can_probe": agent.ask_cmd("") is not None,
            "ask_model": agent.ask_model,
            "models": [vars(m) for m in agent.models],
            "quota": seen.get(agent.name),
        }
        for agent in agentclis.AGENTS
    ]


@router.get("/settings", response_model=StartSettings)
async def get_settings():
    return await asyncio.to_thread(starting.settings)


@router.put("/settings", response_model=StartSettings)
async def put_settings(body: StartSettingsPatch):
    try:
        return await asyncio.to_thread(starting.save_settings, body.model_dump(exclude_unset=True))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
