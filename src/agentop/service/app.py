"""Assemble the service, and run it (`agentop serve`)."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from agentop.guard import HostOriginGuard

from . import config, identity
from .config import Settings
from .identity import Identity
from .legacy import create_legacy_router
from .routes import Context, create_v2_app
from .store import SessionStore

LOG = logging.getLogger(__name__)

_OLD_UI_DIR = Path(__file__).resolve().parents[3] / "html"


def create_app(
    settings: Settings | None = None,
    ident: Identity | None = None,
    store: SessionStore | None = None,
) -> FastAPI:
    settings = settings or config.load()
    ident = ident or identity.load_or_create(settings.name)
    store = store or SessionStore()

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        await store.startup()
        yield
        await store.shutdown()

    app = FastAPI(title="agentop", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.mount("/api/v2", create_v2_app(Context(settings, ident, store)))

    # The old dashboard has no token, so it must never be listening on a network.
    if settings.is_loopback:
        app.add_middleware(HostOriginGuard, exempt_prefixes=("/api/v2",))
        app.include_router(create_legacy_router(store))
        if _OLD_UI_DIR.is_dir():

            @app.get("/")
            async def root():
                return FileResponse(_OLD_UI_DIR / "index.html")

            app.mount("/", StaticFiles(directory=str(_OLD_UI_DIR), html=True), name="static")

    return app


def serve(settings: Settings | None = None) -> None:
    import uvicorn

    settings = settings or config.load()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(name)s: %(message)s")
    if not settings.is_loopback and not settings.tls:
        LOG.warning(
            "Listening on %s without TLS: the token and all terminal traffic can be read on the network. "
            "Prefer an SSH tunnel, or set tls_cert and tls_key.",
            settings.host,
        )
    uvicorn.run(
        create_app(settings),
        host=settings.host,
        port=settings.port,
        ssl_certfile=settings.tls_cert,
        ssl_keyfile=settings.tls_key,
    )


if __name__ == "__main__":
    serve()
