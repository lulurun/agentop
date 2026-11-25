"""Projects: list them, and start a project's Coordinator. Creating and removing one is its owner plugin's."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException

from agentop import agentclis, projects

from ..models import ProjectModel, SessionModel
from .context import AUTHED, Context, Ctx

router = APIRouter(dependencies=AUTHED)


async def available_tools(ctx: Context) -> list[str]:
    if ctx.available is None:
        found = await asyncio.to_thread(agentclis.installed, agentclis.AGENTS)
        ctx.available = [agent.name for agent in found]
    return ctx.available


@router.get("/projects", response_model=list[ProjectModel])
async def list_projects(ctx: Ctx, owner: str | None = None):
    return await asyncio.to_thread(projects.list_projects, owner)


@router.get("/projects/{name}", response_model=ProjectModel)
async def get_project(name: str, ctx: Ctx):
    found = await asyncio.to_thread(projects.get, name)
    if found is None:
        raise HTTPException(404, "No such project")
    return found


@router.post("/projects/{name}/coordinator", response_model=SessionModel, status_code=202)
async def start_coordinator(name: str, ctx: Ctx):
    """Get its Coordinator; Projects never start one through this route."""
    row = await ctx.store.ensure_coordinator(name, await available_tools(ctx))
    if row is None:
        raise HTTPException(409, "No agent tool is installed")
    return row
