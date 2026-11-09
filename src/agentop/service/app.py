"""Assemble the service, and run it (`agentop serve`)."""

from __future__ import annotations

import logging
import os
import signal
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from agentop.guard import HostOriginGuard

from . import config, identity, manager
from .config import Settings
from .identity import Identity
from .legacy import create_legacy_router
from .plugins import PluginHost
from .routes import Context, create_v2_app
from .store import SessionStore

LOG = logging.getLogger(__name__)
GRACEFUL_SHUTDOWN_SECONDS = 5

_OLD_UI_DIR = Path(__file__).resolve().parents[3] / "html"
_WEB_DIR = Path(__file__).resolve().parent / "webdist"


class AppFiles(StaticFiles):
    """Static files that can't go stale.

    With no Cache-Control, a browser decides for itself how long a file stays
    fresh, based on its age, and may not ask the server again for weeks. So pages
    are always revalidated. Files under assets/ have a content hash in their
    name, so they can be kept forever.
    """

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        hashed = path.startswith("assets/")
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable" if hashed else "no-cache"
        return response


def create_app(
    settings: Settings | None = None,
    ident: Identity | None = None,
    store: SessionStore | None = None,
    plugins: PluginHost | None = None,
) -> FastAPI:
    settings = settings or config.load()
    identity_file = None if ident else identity.identity_path()
    ident = ident or identity.load_or_create(settings.name)
    store = store or SessionStore()
    plugins = plugins or PluginHost(store)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        await store.startup()
        # After the first scan, so plugins don't hear every existing session as new.
        await plugins.startup()
        yield
        await plugins.shutdown()
        await store.shutdown()

    app = FastAPI(title="agentop", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.mount("/api/v2", create_v2_app(Context(settings, ident, store, identity_file=identity_file, plugins=plugins)))
    if _WEB_DIR.is_dir():
        app.mount("/app", AppFiles(directory=str(_WEB_DIR), html=True), name="web")

    # The old dashboard has no token, so it must never be listening on a network.
    if settings.is_loopback:
        app.add_middleware(HostOriginGuard, exempt_prefixes=("/api/v2", "/app"))
        app.include_router(create_legacy_router(store))
        if _OLD_UI_DIR.is_dir():

            @app.get("/")
            async def root():
                return FileResponse(_OLD_UI_DIR / "index.html")

            app.mount("/", AppFiles(directory=str(_OLD_UI_DIR), html=True), name="static")

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
    server = uvicorn.Server(
        uvicorn.Config(
            create_app(settings),
            host=settings.host,
            port=settings.port,
            ssl_certfile=settings.tls_cert,
            ssl_keyfile=settings.tls_key,
            # Open terminals would otherwise hold a restart up indefinitely.
            timeout_graceful_shutdown=GRACEFUL_SHUTDOWN_SECONDS,
        )
    )
    restarting = False

    def on_restart(signum, frame):
        nonlocal restarting
        restarting = True
        server.should_exit = True

    signal.signal(manager.RESTART_SIGNAL, on_restart)
    manager.serving_here(lambda: setattr(server, "should_exit", True))
    server.run()
    if restarting:
        # Same process, terminal and arguments, with whatever code is installed now.
        LOG.info("Restarting")
        logging.shutdown()
        sys.stdout.flush()
        sys.stderr.flush()
        os.execv(sys.executable, [sys.executable, *sys.orig_argv[1:]])


if __name__ == "__main__":
    serve()
