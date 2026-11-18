"""The /api/v2 application. Mounted by app.py, and reachable from any origin.

Access is decided by a client's token alone, because a client may be loaded
from anywhere. The service token only adds a client (auth.py). That is safe because nothing here relies on an ambient credential:
without cookies, a foreign page has nothing to ride on. See docs/design.md, section 7.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .api import admin, clients, history, sessions, sockets
from .api.context import AUTHED, REPO_ROOT, Context
from .store import StoreError

__all__ = ["REPO_ROOT", "Context", "create_v2_app"]


def create_v2_app(ctx: Context) -> FastAPI:
    v2 = FastAPI(title="agentop", version=admin.version(), docs_url=None, redoc_url=None)
    v2.state.ctx = ctx
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

    v2.include_router(clients.router)
    if ctx.plugins is not None:
        # Plugin routes go under /plugins/{id}/, behind the same token.
        ctx.plugins.attach(v2, AUTHED, clients.sign_in_for(ctx))
    for area in (admin, sessions, history, sockets):
        v2.include_router(area.router)
    return v2
