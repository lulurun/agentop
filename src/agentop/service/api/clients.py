"""Signing in: clients, their tokens, and the one-use tickets sockets take."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException

from .. import auth
from ..models import (
    AddClient,
    ClientInfo,
    ClientToken,
    LoginMethod,
    Ticket,
    TicketRequest,
)
from .context import Context, Ctx, Me

router = APIRouter()


def _token_body(ctx: Context, client: auth.Client, token: str) -> dict:
    return {
        "client_id": client.id,
        "token": token,
        "expires_in": max(0, int(client.expires_at - time.time())),
        "ttl": int(ctx.settings.client_ttl_seconds),
    }


def _client_body(client: auth.Client, me: auth.Client) -> dict:
    fields = ("id", "name", "created_at", "last_seen", "expires_at", "ends_at")
    return {**{f: getattr(client, f) for f in fields}, "current": client.id == me.id}


def sign_in_for(ctx: Context) -> Callable[[str], dict]:
    """How a login plugin signs a client in: the same as with the service token."""

    def sign_in(name: str) -> dict:
        client, token = ctx.clients.add(name[:80] or "Client", ctx.settings.client_ttl_seconds, ctx.settings.client_max_age_seconds)
        return _token_body(ctx, client, token)

    return sign_in


@router.post("/auth/clients", response_model=ClientToken, status_code=201)
async def add_client(body: AddClient, ctx: Ctx, authorization: Annotated[str | None, Header()] = None):
    """Add a client with the service token. Nothing else accepts the service token."""
    if ctx.clients.add_blocked():
        raise HTTPException(429, "Too many wrong tokens. Wait a minute and try again.")
    if not auth.bearer_matches(authorization, ctx.current_identity().token):
        ctx.clients.add_failed()
        raise HTTPException(401, "Missing or invalid service token", {"WWW-Authenticate": "Bearer"})
    ttl = ctx.settings.client_ttl_seconds
    if body.ttl_seconds is not None:
        ttl = min(ttl, body.ttl_seconds)
    client, token = ctx.clients.add(body.name, ttl, ctx.settings.client_max_age_seconds)
    return _token_body(ctx, client, token)


@router.get("/auth/methods", response_model=list[LoginMethod])
async def login_methods(ctx: Ctx):
    """The ways to sign in, for the sign-in screen. No token needed. The service token is always one."""
    plugins = ctx.plugins.login_methods() if ctx.plugins else []
    return [{"id": "token", "name": "Service token"}, *plugins]


@router.post("/auth/renew", response_model=ClientToken)
async def renew(ctx: Ctx, me: Me, authorization: Annotated[str | None, Header()] = None):
    client, token = ctx.clients.renew(me.id, ctx.settings.client_ttl_seconds, auth.bearer_value(authorization))
    return _token_body(ctx, client, token)


@router.get("/auth/clients", response_model=list[ClientInfo])
async def list_clients(ctx: Ctx, me: Me):
    return [_client_body(c, me) for c in ctx.clients.list()]


@router.delete("/auth/clients/{client_id}", status_code=204)
async def revoke_client(client_id: str, ctx: Ctx, _me: Me):
    """Revoke a client, this one included (to sign out). Its open sockets close within seconds."""
    if not ctx.clients.revoke(client_id):
        raise HTTPException(404, "No such client")


@router.post("/tickets", response_model=Ticket)
async def issue_ticket(body: TicketRequest, ctx: Ctx, me: Me):
    if body.purpose == "terminal":
        if not body.session_id:
            raise HTTPException(400, "session_id is required")
        scope = auth.terminal_scope(body.session_id)
    else:
        scope = auth.EVENTS_SCOPE
    return {"ticket": ctx.tickets.issue(scope, me.id), "expires_in": int(auth.TICKET_TTL_SECONDS)}
