"""Assemble the service, and run it (`agentop serve`)."""

from __future__ import annotations

import logging
import mimetypes
import os
import signal
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import auth, config, host, identity, manager
from .config import Settings
from .identity import Identity
from .plugins import PluginHost
from .routes import Context, create_v2_app
from .store import SessionStore

LOG = logging.getLogger(__name__)
GRACEFUL_SHUTDOWN_SECONDS = 5

_WEB_DIR = Path(__file__).resolve().parent / "webdist"
# Python knows this type only from 3.13; browsers ignore a manifest served as text/plain.
mimetypes.add_type("application/manifest+json", ".webmanifest")


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
    available: list[str] | None = None,
    clients: auth.ClientStore | None = None,
) -> FastAPI:
    settings = settings or config.load()
    # A given identity is a test's: its clients live in memory unless it gives a store too.
    clients = clients or auth.ClientStore(None if ident else auth.clients_path())
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
    app.mount("/api/v2", create_v2_app(Context(settings, ident, store, clients=clients, identity_file=identity_file, plugins=plugins, available=available)))

    # The app was at /app/ until 2026-11-14. Relative, so it also works behind a proxy's path prefix.
    @app.get("/app", include_in_schema=False)
    @app.get("/app/{rest:path}", include_in_schema=False)
    async def moved(rest: str | None = None):
        up = "./" if rest is None else "../" * (rest.count("/") + 1)
        return RedirectResponse(up + (rest or ""), status_code=308)

    if _WEB_DIR.is_dir():
        app.mount("/", AppFiles(directory=str(_WEB_DIR), html=True), name="web")

    return app


def serve(settings: Settings | None = None) -> None:
    import uvicorn

    settings = settings or config.load()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(name)s: %(message)s")
    if not settings.is_loopback and not settings.tls:
        LOG.error(
            "Refusing to listen on %s without TLS: the token and all terminal traffic could be read on the network. "
            "Bind 127.0.0.1 and use an SSH tunnel or an HTTPS proxy, or set tls_cert and tls_key. See README.md.",
            settings.host,
        )
        raise SystemExit(1)
    try:
        settings.client_ttl_seconds, settings.client_max_age_seconds  # noqa: B018
    except ValueError as e:
        LOG.error("%s (in config.toml)", e)
        raise SystemExit(1) from None
    try:
        tools = host.prepare()
    except host.NoAgentTools as e:
        LOG.error("%s", e)
        raise SystemExit(1) from None
    server = uvicorn.Server(
        uvicorn.Config(
            create_app(settings, available=[t.name for t in tools]),
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
