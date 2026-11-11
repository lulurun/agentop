"""Where agentop keeps its own files.

Everything agentop writes lives under one folder, ~/.agentop:

    config.toml        settings you edit
    service.json       this service's name and token (mode 0600)
    state/             what agentop rewrites as it runs: sessions.json and its
                       lock, plugins.json, update-status.json, update.pid
    logs/              serve.log, update.log
    plugins/<id>/      each plugin's own files: store.json from `api.store`,
                       and anything else under `api.data_dir`. Saved dialogues
                       are in plugins/dialogue/dialogues/

New kinds of files go in one of these, not at the top.
"""

from __future__ import annotations

import os
from pathlib import Path

HOME_ENV = "AGENTOP_HOME"
DEFAULT_HOME = "~/.agentop"
# Before 2026-11-11 the folder was ~/.agent-dashboard, with every file at the top.
LEGACY_HOME = "~/.agent-dashboard"


def _unmoved() -> Path | None:
    """The old folder, while the service hasn't moved it yet. Until then everything uses it as it was."""
    if os.environ.get(HOME_ENV):
        return None
    legacy = Path(LEGACY_HOME).expanduser()
    if legacy.is_dir() and not Path(DEFAULT_HOME).expanduser().exists():
        return legacy
    return None


def data_dir() -> Path:
    """The agentop folder. AGENTOP_HOME moves it, for tests and throwaway instances."""
    return _unmoved() or Path(os.environ.get(HOME_ENV) or DEFAULT_HOME).expanduser()


def state_dir() -> Path:
    return _unmoved() or data_dir() / "state"


def logs_dir() -> Path:
    return _unmoved() or data_dir() / "logs"


def dialogues_dir() -> Path:
    # Dialogues are becoming a plugin (design 12.9), so they already live in its folder.
    unmoved = _unmoved()
    return unmoved / "dialogues" if unmoved else plugins_dir() / "dialogue" / "dialogues"


def plugins_dir() -> Path:
    return data_dir() / "plugins"


# Where each file of the flat layout went.
_MOVES = {
    "sessions.json": "state/sessions.json",
    "sessions.lock": "state/sessions.lock",
    "plugins.json": "state/plugins.json",
    "update-status.json": "state/update-status.json",
    "update.pid": "state/update.pid",
    "serve.log": "logs/serve.log",
    "update.log": "logs/update.log",
    "memory": "plugins/memory",
    "dialogues": "plugins/dialogue/dialogues",
}


def _move(src: Path, dst: Path) -> None:
    if src.exists() and not dst.exists():
        dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.rename(src, dst)
        except OSError:
            pass  # Another agentop process got there first.


def migrate() -> None:
    """Bring an older layout into the current one. Cheap, and safe to run every time.
    `agentop serve` runs it at start, once the old service has stopped.

    ~/.agent-dashboard is renamed to ~/.agentop. Nothing is left at the old name.
    """
    legacy = _unmoved()
    base = Path(os.environ.get(HOME_ENV) or DEFAULT_HOME).expanduser()
    if legacy:
        try:
            os.rename(legacy, base)
        except OSError:
            pass
    if not base.is_dir():
        return
    for old, new in _MOVES.items():
        _move(base / old, base / new)
    plugins = plugins_dir()
    if plugins.is_dir():
        for store in plugins.glob("*.json"):
            _move(store, plugins / store.stem / "store.json")
