"""JSON state files: written so a crash never leaves half a file, and moved aside, not overwritten, when unreadable."""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Any

LOG = logging.getLogger(__name__)


def write_json_atomic(path: Path, data: Any, indent: int | None = 2) -> None:
    """Replace path with data as JSON, all or nothing. The file is 0600, as mkstemp makes it, never briefly readable by others."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=indent)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    # Without this a crash can lose the rename, and with it the write.
    try:
        dir_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except OSError:
        pass


def move_aside(path: Path, why: str) -> Path:
    """Rename an unreadable state file out of the way, so starting over doesn't destroy it. Returns where it went."""
    aside = path.with_name(f"{path.name}.corrupt-{time.strftime('%Y%m%d-%H%M%S')}")
    os.replace(path, aside)
    LOG.error("%s %s; moved it to %s and starting over", path, why, aside)
    return aside
