"""Where agentop keeps its own files.

Everything agentop writes lives under one folder, ~/.agentop:

    config.toml        settings you edit
    service.json       this service's name and token (mode 0600)
    state/             what agentop rewrites as it runs: sessions.json and its
                       lock, plugins.json, update-status.json, update.pid
    logs/              serve.log, update.log
    plugins/<id>/      each plugin's own files: store.json from `api.store`,
                       and anything else under `api.data_dir`

New kinds of files go in one of these, not at the top.
"""

from __future__ import annotations

import os
from pathlib import Path

HOME_ENV = "AGENTOP_HOME"
DEFAULT_HOME = "~/.agentop"


def data_dir() -> Path:
    """The agentop folder. AGENTOP_HOME moves it, for tests and throwaway instances."""
    return Path(os.environ.get(HOME_ENV) or DEFAULT_HOME).expanduser()


def state_dir() -> Path:
    return data_dir() / "state"


def logs_dir() -> Path:
    return data_dir() / "logs"


def plugins_dir() -> Path:
    return data_dir() / "plugins"
