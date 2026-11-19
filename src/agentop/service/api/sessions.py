"""Live sessions: list, start, rename, stop, close, and what is in each one's folder and transcripts."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException

from agentop import files, transcripts

from ..models import (
    CreateSession,
    FileListing,
    GitChanges,
    PatchSession,
    SessionModel,
    TranscriptListing,
)
from .context import AUTHED, Context, Ctx

router = APIRouter(dependencies=AUTHED)


@router.get("/sessions", response_model=list[SessionModel])
async def list_sessions(ctx: Ctx):
    return ctx.store.list()


@router.post("/sessions", response_model=SessionModel, status_code=202)
async def create_session(body: CreateSession, ctx: Ctx):
    resume_id = body.resume.session_id if body.resume else None
    return await ctx.store.create(body.tool, body.cwd, body.name, resume_id)


@router.get("/sessions/{session_id}", response_model=SessionModel)
async def get_session(session_id: str, ctx: Ctx):
    row = ctx.store.get(session_id)
    if row is None:
        raise HTTPException(404, "Session not found")
    return row


@router.patch("/sessions/{session_id}", response_model=SessionModel)
async def patch_session(session_id: str, body: PatchSession, ctx: Ctx):
    return await ctx.store.update(session_id, body.display_name, body.description)


@router.post("/sessions/{session_id}/stop")
async def stop_session(session_id: str, ctx: Ctx):
    return await ctx.store.stop(session_id)


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str, ctx: Ctx):
    return await ctx.store.close(session_id)


def _working_directory(ctx: Context, session_id: str) -> str:
    row = ctx.store.get(session_id)
    if row is None:
        raise HTTPException(404, "Session not found")
    if not row["cwd"]:
        raise HTTPException(404, "This session has no known working directory")
    return row["cwd"]


async def _listing(root: str, path: str) -> dict:
    try:
        return await asyncio.to_thread(files.list_directory, root, path)
    except files.OutsideRoot:
        raise HTTPException(403, "That path is outside the session's directory") from None
    except (FileNotFoundError, NotADirectoryError):
        raise HTTPException(404, "No such directory") from None
    except PermissionError:
        raise HTTPException(403, "Permission denied") from None


@router.get("/sessions/{session_id}/files", response_model=FileListing)
async def session_files(session_id: str, ctx: Ctx, path: str = ""):
    return await _listing(_working_directory(ctx, session_id), path)


@router.get("/sessions/{session_id}/memory", response_model=FileListing)
async def session_memory(session_id: str, ctx: Ctx, path: str = ""):
    """The notes the agent tool keeps for this session's project, such as Claude Code's memory folder."""
    row = ctx.store.get(session_id)
    if row is None:
        raise HTTPException(404, "Session not found")
    root = await asyncio.to_thread(transcripts.memory_folder, row.get("tool"), row.get("session_id"), row.get("cwd"))
    if root is None:
        raise HTTPException(404, "This session's tool keeps no memory folder")
    return await _listing(str(root), path)


@router.get("/sessions/{session_id}/changes", response_model=GitChanges)
async def session_changes(session_id: str, ctx: Ctx):
    try:
        return await asyncio.to_thread(files.git_changes, _working_directory(ctx, session_id))
    except FileNotFoundError:
        raise HTTPException(404, "No such directory") from None


@router.get("/sessions/{session_id}/transcripts", response_model=TranscriptListing)
async def session_transcripts(session_id: str, ctx: Ctx):
    row = ctx.store.get(session_id)
    if row is None:
        raise HTTPException(404, "Session not found")
    return await asyncio.to_thread(transcripts.list_transcripts, row.get("tool"), row.get("session_id"))
