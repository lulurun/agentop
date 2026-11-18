"""The two sockets: session events, and a session's terminal. Each takes a one-use ticket, and closes when its client is signed out."""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from agentop.tmux import Session

from .. import auth, terminal
from .context import Context, Ctx

WS_INVALID_TICKET = 4401
WS_NO_SESSION = 4404
# A client's sockets close soon after it is revoked or its token runs out.
WS_SIGNED_OUT = 4403
CLIENT_CHECK_SECONDS = 5.0

router = APIRouter()


async def _reject(ws: WebSocket, code: int) -> None:
    # Accept first: a browser cannot see a close code from a refused handshake.
    await ws.accept()
    await ws.close(code)


async def _while_signed_in(ctx: Context, ws: WebSocket, client_id: str, work) -> None:
    """Run a socket's work until it ends, or until its client is revoked or expires, which closes it."""

    async def watch():
        while ctx.clients.is_active(client_id):
            await asyncio.sleep(CLIENT_CHECK_SECONDS)

    tasks = [asyncio.ensure_future(work), asyncio.ensure_future(watch())]
    signed_out = False
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        signed_out = tasks[1] in done
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    if signed_out:
        try:
            await ws.close(WS_SIGNED_OUT)
        except (RuntimeError, WebSocketDisconnect):
            pass  # already closed


@router.websocket("/ws/events")
async def events(ws: WebSocket, ctx: Ctx, ticket: str | None = None):
    client_id = ctx.tickets.consume(ticket, auth.EVENTS_SCOPE)
    if client_id is None:
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

    async def serve_events():
        tasks = [asyncio.ensure_future(send_events()), asyncio.ensure_future(wait_for_disconnect())]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    try:
        await _while_signed_in(ctx, ws, client_id, serve_events())
    finally:
        ctx.store.unsubscribe(queue)


@router.websocket("/ws/sessions/{session_id}/terminal")
async def session_terminal(
    ws: WebSocket,
    session_id: str,
    ctx: Ctx,
    ticket: str | None = None,
    cols: int = 80,
    rows: int = 24,
    readonly: bool = False,
):
    client_id = ctx.tickets.consume(ticket, auth.terminal_scope(session_id))
    if client_id is None:
        await _reject(ws, WS_INVALID_TICKET)
        return
    # Ask tmux, not the store: a session can be attached before the next tick sees it.
    tmux_session = ctx.store.tmux_session(session_id)
    if not await asyncio.to_thread(Session.has, tmux_session):
        await _reject(ws, WS_NO_SESSION)
        return
    await ws.accept()
    # A plugin's session, such as a dialogue's agent, is only ever watched: typing would break what drives it.
    read_only = readonly or bool((ctx.store.get(session_id) or {}).get("owner"))

    async def bridge():
        try:
            await terminal.bridge(ws, tmux_session, cols, rows, read_only)
        except WebSocketDisconnect:
            pass

    await _while_signed_in(ctx, ws, client_id, bridge())
