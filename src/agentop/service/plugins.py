"""The plugin loader. See docs/v2-design.md, section 12.

A plugin is a folder under `agentop/plugins/` with a `plugin.toml` manifest and
an `__init__.py` that defines `register(api)`. The loader finds them at service
start, loads each one in isolation, and keeps a list with their states.

The rule everything here follows: a plugin can fail, be removed, or be disabled,
and the only visible effect is that its feature disappears. So nothing a plugin
does can raise into the core, block the event loop for long, or stop another
plugin from loading.

`register(api)` does not act directly. It declares routes, event handlers, and
tasks on the `PluginAPI` it is given, and the loader switches them on only once
`register` has returned in time. A plugin that fails part way through leaves
nothing behind.
"""

from __future__ import annotations

import asyncio
import importlib.util
import inspect
import json
import logging
import os
import sys
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, HTTPException

from agentop import paths
from agentop.plugin_manifest import (  # noqa: F401 - DEFAULT_ROOT and the rest are imported from here too
    DEFAULT_ROOT,
    MODULE_PREFIX,
    PLUGIN_API,
    SUPPORTED_PLUGIN_APIS,
    UI_SLOTS,
    Manifest,
    ManifestError,
    incompatible_reason,
    is_enabled,
    plugin_folders,
    read_flags,
    read_manifest,
)

LOG = logging.getLogger(__name__)

REGISTER_TIMEOUT_SECONDS = 5.0
HANDLER_TIMEOUT_SECONDS = 10.0
# This many handler errors inside the window moves a plugin to `failed`.
MAX_HANDLER_ERRORS = 5
HANDLER_ERROR_WINDOW_SECONDS = 60.0

EVENTS = frozenset({"session.started", "session.exited", "session.closed", "scan"})

ENABLED, DISABLED, FAILED, INCOMPATIBLE = "enabled", "disabled", "failed", "incompatible"
RESTART_TO_RETRY = "Restart the service to try loading it again."


class PluginStore:
    """A plugin's own data: one JSON file, `~/.agentop/plugins/{id}/store.json`."""

    def __init__(self, path: Path):
        self.path = path

    def load(self) -> Any:
        """The saved data, or None if nothing has been saved."""
        try:
            return json.loads(self.path.read_text())
        except FileNotFoundError:
            return None

    def save(self, data: Any) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2))
        os.replace(tmp, self.path)


class PluginAPI:
    """What `register(api)` gets: a narrow surface, not the service internals."""

    def __init__(self, plugin: Plugin, host: PluginHost):
        self._plugin = plugin
        self._host = host
        self.id = plugin.id
        self.router = APIRouter()
        self.log = logging.getLogger(f"{MODULE_PREFIX}.{plugin.id}")
        # The plugin's own folder, for files of its own. Made when first written to.
        self.data_dir = host.data_dir / plugin.id
        self.store = PluginStore(self.data_dir / "store.json")
        self._handlers: list[tuple[str, Callable]] = []
        self._tasks: list[Callable] = []

    def on(self, event: str, handler: Callable) -> None:
        """Call `handler(payload)` on a core event. It runs off the event loop, in isolation."""
        if event not in EVENTS:
            raise ValueError(f"Unknown event '{event}'. Known: {', '.join(sorted(EVENTS))}")
        if inspect.iscoroutinefunction(handler):
            raise TypeError("Event handlers must be plain functions; they run in a thread")
        self._handlers.append((event, handler))

    def task(self, factory: Callable) -> None:
        """Run `factory()`, an async function, as a supervised background task.

        A function rather than a coroutine, so that it can be started again
        when the plugin is enabled again. If it raises, the plugin fails.
        """
        if not inspect.iscoroutinefunction(factory):
            raise TypeError("api.task takes an async function, not a coroutine")
        self._tasks.append(factory)

    def sessions(self) -> list[dict]:
        """A snapshot of the session list. Changing it changes nothing."""
        return self._host.store.list()

    def emit(self, data: Any) -> None:
        """Send `{"type": "plugin", "id": ..., "data": ...}` to clients, for the plugin's UI."""
        if self._plugin.state == ENABLED:
            self._host.publish({"type": "plugin", "id": self.id, "data": data})


@dataclass
class Plugin:
    id: str
    folder: Path
    manifest: Manifest | None = None
    state: str = DISABLED
    error: str | None = None
    # Whether import and register were tried, and whether they succeeded.
    attempted: bool = False
    loaded: bool = False
    # Set by any failure, and cleared only by a restart.
    broken: bool = False
    last_error: str | None = None
    api: PluginAPI | None = None
    tasks: list[asyncio.Task] = field(default_factory=list)
    errors: deque = field(default_factory=deque)

    def info(self) -> dict:
        m = self.manifest
        return {
            "id": self.id,
            "name": m.name if m else self.id,
            "version": m.version if m else "",
            "description": m.description if m else "",
            "state": self.state,
            "error": self.error,
            "ui": list(m.ui) if m else [],
        }


class PluginHost:
    """Finds, loads, enables, and disables plugins, and keeps their list."""

    def __init__(self, store, root: Path | None = None, data_dir: Path | None = None):
        self.store = store
        self.root = root or DEFAULT_ROOT
        base = data_dir or paths.data_dir()
        self.data_dir = base / "plugins"
        self.flags_path = base / "state" / "plugins.json"
        self.plugins: dict[str, Plugin] = {}
        self._app: FastAPI | None = None
        self._auth: list = []
        self._running: set[asyncio.Task] = set()
        self._started = False

    # ------------------------------------------------------------------ lifecycle

    def attach(self, app: FastAPI, auth_dependencies: list) -> None:
        """Where plugin routes are mounted, and the auth they inherit."""
        self._app = app
        self._auth = auth_dependencies

    async def startup(self) -> None:
        self.plugins = {}
        flags = self._read_flags()
        for folder in self._folders():
            plugin = Plugin(folder.name, folder)
            self.plugins[plugin.id] = plugin
            try:
                plugin.manifest = read_manifest(folder)
            except ManifestError as exc:
                self._fail(plugin, str(exc), publish=False)
                continue
            reason = incompatible_reason(plugin.manifest.plugin_api)
            if reason:
                plugin.state = INCOMPATIBLE
                plugin.error = reason
                continue
            if is_enabled(plugin.manifest, flags):
                await self._load(plugin)
                if plugin.loaded:
                    self._open(plugin)
        self._started = True
        self.store.add_listener(self._on_store_event)
        LOG.info(
            "Plugins: %s",
            ", ".join(f"{p.id} ({p.state})" for p in self.plugins.values()) or "none",
        )

    async def shutdown(self) -> None:
        self.store.remove_listener(self._on_store_event)
        for plugin in self.plugins.values():
            await self._cancel_tasks(plugin)
        for task in list(self._running):
            task.cancel()
        await asyncio.gather(*self._running, return_exceptions=True)

    # ------------------------------------------------------------------ reading

    def list(self) -> list[dict]:
        return [p.info() for p in sorted(self.plugins.values(), key=lambda p: p.id)]

    def get(self, plugin_id: str) -> dict | None:
        plugin = self.plugins.get(plugin_id)
        return plugin.info() if plugin else None

    def is_enabled(self, plugin_id: str) -> bool:
        plugin = self.plugins.get(plugin_id)
        return plugin is not None and plugin.state == ENABLED

    # ------------------------------------------------------------------ enable and disable

    async def set_enabled(self, plugin_id: str, enabled: bool) -> dict:
        plugin = self.plugins.get(plugin_id)
        if plugin is None:
            raise KeyError(plugin_id)
        if plugin.manifest is None:
            return plugin.info()  # a broken manifest has nothing to switch
        self._write_flag(plugin_id, enabled)
        if plugin.state == INCOMPATIBLE:
            return plugin.info()

        if not enabled:
            if plugin.state == ENABLED:
                await self._cancel_tasks(plugin)
            plugin.state = DISABLED
            plugin.error = None
            plugin.errors.clear()
        elif plugin.state != ENABLED:
            if plugin.broken:
                # Half-initialised plugin state is kept out of the running service.
                plugin.state = FAILED
                plugin.error = f"{plugin.last_error} {RESTART_TO_RETRY}"
            elif plugin.loaded:
                self._open(plugin)
            else:
                await self._load(plugin)
                if plugin.loaded:
                    self._open(plugin)
        self._publish_list()
        return plugin.info()

    # ------------------------------------------------------------------ internals

    def _folders(self) -> list[Path]:
        return plugin_folders(self.root)

    async def _load(self, plugin: Plugin) -> None:
        """Import the plugin and call register(api), with a time limit."""
        plugin.attempted = True
        api = PluginAPI(plugin, self)
        try:
            await asyncio.wait_for(asyncio.to_thread(self._import_and_register, plugin, api), REGISTER_TIMEOUT_SECONDS)
        except TimeoutError:
            self._fail(plugin, f"register() took longer than {REGISTER_TIMEOUT_SECONDS:g}s", publish=False)
            return
        except Exception as exc:
            LOG.exception("Plugin %s failed to load", plugin.id)
            self._fail(plugin, f"{type(exc).__name__}: {exc}", publish=False)
            return
        plugin.api = api
        plugin.loaded = True
        if self._app is not None:
            gate = self._gate(plugin.id)
            self._app.include_router(
                api.router,
                prefix=f"/plugins/{plugin.id}",
                dependencies=[*self._auth, Depends(gate)],
                include_in_schema=False,
            )

    def _import_and_register(self, plugin: Plugin, api: PluginAPI) -> None:
        name = f"{MODULE_PREFIX}.{plugin.id}"
        for key in [k for k in sys.modules if k == name or k.startswith(name + ".")]:
            del sys.modules[key]
        init = plugin.folder / "__init__.py"
        spec = importlib.util.spec_from_file_location(name, init, submodule_search_locations=[str(plugin.folder)])
        if spec is None or spec.loader is None or not init.exists():
            raise ImportError("No __init__.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        try:
            spec.loader.exec_module(module)
            register = getattr(module, "register", None)
            if not callable(register):
                raise TypeError("The plugin defines no register(api)")
            if inspect.iscoroutinefunction(register):
                raise TypeError("register(api) must be a plain function")
            register(api)
        except BaseException:
            sys.modules.pop(name, None)
            raise

    def _gate(self, plugin_id: str):
        def gate() -> None:
            if not self.is_enabled(plugin_id):
                raise HTTPException(404, "Not Found")

        return gate

    def _open(self, plugin: Plugin) -> None:
        plugin.state = ENABLED
        plugin.error = None
        plugin.errors.clear()
        for factory in plugin.api._tasks:
            task = asyncio.get_running_loop().create_task(self._supervise(plugin, factory))
            plugin.tasks.append(task)

    async def _supervise(self, plugin: Plugin, factory: Callable) -> None:
        try:
            await factory()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            plugin.api.log.exception("Task failed")
            self._fail(plugin, f"A background task failed: {type(exc).__name__}: {exc}")

    async def _cancel_tasks(self, plugin: Plugin) -> None:
        current = asyncio.current_task()
        tasks = [t for t in plugin.tasks if t is not current]
        plugin.tasks = []
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    def _fail(self, plugin: Plugin, error: str, publish: bool = True) -> None:
        if plugin.state == FAILED:
            return
        LOG.warning("Plugin %s failed: %s", plugin.id, error)
        plugin.state = FAILED
        plugin.error = error
        plugin.broken = True
        plugin.last_error = error
        current = asyncio.current_task() if _in_loop() else None
        for task in plugin.tasks:
            if task is not current:
                task.cancel()
        plugin.tasks = []
        if publish:
            self._publish_list()

    # ------------------------------------------------------------------ events

    def publish(self, event: dict) -> None:
        self.store.publish(event)

    def _publish_list(self) -> None:
        if self._started:
            self.publish({"type": "plugins"})

    def _on_store_event(self, event: str, payload: Any) -> None:
        """Called by the store on the event loop. Starts handlers and never waits for them."""
        for plugin in self.plugins.values():
            if plugin.state != ENABLED:
                continue
            for name, handler in plugin.api._handlers:
                if name == event:
                    task = asyncio.get_running_loop().create_task(self._run_handler(plugin, handler, payload))
                    self._running.add(task)
                    task.add_done_callback(self._running.discard)

    async def _run_handler(self, plugin: Plugin, handler: Callable, payload: Any) -> None:
        try:
            await asyncio.wait_for(asyncio.to_thread(handler, payload), HANDLER_TIMEOUT_SECONDS)
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            plugin.api.log.warning("Event handler took longer than %gs", HANDLER_TIMEOUT_SECONDS)
            self._count_error(plugin, "an event handler timed out")
        except Exception as exc:
            plugin.api.log.exception("Event handler failed")
            self._count_error(plugin, f"{type(exc).__name__}: {exc}")

    def _count_error(self, plugin: Plugin, last: str) -> None:
        now = time.monotonic()
        plugin.errors.append(now)
        while plugin.errors and now - plugin.errors[0] > HANDLER_ERROR_WINDOW_SECONDS:
            plugin.errors.popleft()
        if len(plugin.errors) >= MAX_HANDLER_ERRORS and plugin.state == ENABLED:
            self._fail(plugin, f"{len(plugin.errors)} event handler errors in a minute; the last: {last}")

    # ------------------------------------------------------------------ flags

    def _read_flags(self) -> dict:
        flags = read_flags(self.flags_path)
        if flags is None:
            LOG.warning("%s can't be read; using each plugin's default", self.flags_path)
            return {}
        return flags

    def _write_flag(self, plugin_id: str, enabled: bool) -> None:
        flags = self._read_flags()
        flags[plugin_id] = {**(flags.get(plugin_id) or {}), "enabled": enabled}
        self.flags_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.flags_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(flags, indent=2))
        os.replace(tmp, self.flags_path)


def _in_loop() -> bool:
    try:
        asyncio.get_running_loop()
        return True
    except RuntimeError:
        return False
