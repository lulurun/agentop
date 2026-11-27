"""Projects: list them, start a project's Coordinator, and keep the app's layout of each. Creating and removing one is its owner plugin's."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Response

from agentop import agentclis, projects

from ..models import ProjectLayout, ProjectModel, SessionModel
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


async def _layout_project(name: str) -> str:
    if name != projects.DEFAULT and not await asyncio.to_thread(projects.exists, name):
        raise HTTPException(404, "No such project")
    return name


@router.get("/projects/{name}/layout", responses={200: {"description": "The saved layout as written"}, 204: {"description": "None saved"}})
async def get_layout(name: str, ctx: Ctx):
    """As written, so a hand edit with a mistake in one tab still loads; the app reads it leniently."""
    found = await asyncio.to_thread(projects.read_layout, await _layout_project(name))
    return Response(status_code=204) if found is None else found


@router.put("/projects/{name}/layout", status_code=204)
async def put_layout(name: str, layout: ProjectLayout, ctx: Ctx):
    data = layout.model_dump(by_alias=True)
    for area in data["mid-panel"].values():
        for tab in area["tabs"]:
            content = {k: v for k, v in tab["content"].items() if v is not None}
            if "file" in content:
                content["file"] = {k: v for k, v in content["file"].items() if v is not None}
            tab["content"] = content
    await asyncio.to_thread(projects.write_layout, await _layout_project(name), data)
    return Response(status_code=204)
