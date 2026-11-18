"""History: past sessions agentop started, to see, resume or delete. The listing itself is agentop.history's."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException

from agentop import history, ops, registry
from agentop.results import OpsError

from .. import transcripts
from ..models import HistoryListing, HistoryStats, SessionModel
from .context import AUTHED, Context, Ctx

router = APIRouter(dependencies=AUTHED)


def _past_sessions(ctx: Context) -> list[dict]:
    return history.past_sessions(ctx.store.list(), ctx.settings.history_window_seconds)


async def _past_session(ctx: Context, history_id: str) -> dict:
    found = next((i for i in await asyncio.to_thread(_past_sessions, ctx) if i["id"] == history_id), None)
    if found is None:
        raise HTTPException(404, "No such past session")
    return found


@router.get("/history", response_model=HistoryListing)
async def list_history(ctx: Ctx):
    return {
        "items": await asyncio.to_thread(_past_sessions, ctx),
        "window_seconds": ctx.settings.history_window_seconds,
        "active": ctx.store.active_count(),
        "max_active": ctx.store.max_active,
    }


@router.get("/history/{history_id}/stats", response_model=HistoryStats)
async def history_stats(history_id: str, ctx: Ctx):
    item = await _past_session(ctx, history_id)
    return await asyncio.to_thread(transcripts.transcript_stats, item["tool"], item["session_id"])


@router.post("/history/{history_id}/resume", response_model=SessionModel, status_code=202)
async def resume_history(history_id: str, ctx: Ctx):
    item = await _past_session(ctx, history_id)
    if not item["cwd"]:
        raise HTTPException(400, "This session's directory isn't known")
    name = history.resume_name(item)
    return await ctx.store.create(item["tool"], item["cwd"], None, item["session_id"], display_name=name)


@router.delete("/history/{history_id}")
async def delete_history(history_id: str, ctx: Ctx):
    item = await _past_session(ctx, history_id)
    try:
        await asyncio.to_thread(ops.delete_saved_session, item["tool"], item["session_id"])
    except OpsError as exc:
        raise HTTPException(400, str(exc)) from exc
    await asyncio.to_thread(registry.remove_agent_session, item["tool"], item["session_id"])
    return {"ok": True}
