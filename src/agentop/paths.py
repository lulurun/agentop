"""Where agentop keeps its state."""

from __future__ import annotations

import os
from pathlib import Path

HOME_ENV = "AGENTOP_HOME"


def data_dir() -> Path:
    """Return the state directory. AGENTOP_HOME moves it, for tests and throwaway instances."""
    return Path(os.environ.get(HOME_ENV) or "~/.agent-dashboard").expanduser()
