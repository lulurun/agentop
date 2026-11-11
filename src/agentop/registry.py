"""Managed-session registry backed by ~/.agentop/state/sessions.json.

Both the CLI and the service write this file, so every read-modify-write holds
a lock on a separate file. Locking the JSON file itself would not work: writes
replace it, so two processes would hold locks on different inodes.
"""

from __future__ import annotations

import fcntl
import json
import os
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from agentop.paths import state_dir

ENDED_RETENTION_SECONDS = 30 * 24 * 3600

_lock = threading.Lock()


def registry_path() -> Path:
    return state_dir() / "sessions.json"


def _ensure_dir():
    registry_path().parent.mkdir(parents=True, exist_ok=True)


@contextmanager
def _exclusive():
    """Hold the registry lock, across threads and across processes."""
    _ensure_dir()
    with _lock, open(registry_path().with_suffix(".lock"), "w") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        yield


def load() -> dict:
    path = registry_path()
    if not path.exists():
        return {"sessions": {}}
    try:
        with open(path) as f:
            data = json.load(f)
        if "sessions" not in data:
            data["sessions"] = {}
        return data
    except (json.JSONDecodeError, OSError):
        return {"sessions": {}}


def save(data: dict):
    _ensure_dir()
    path = registry_path()
    fd, tmp_path = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def upsert_session(name: str, fields: dict):
    """Merge fields into a record. A value of None removes that field."""
    with _exclusive():
        data = load()
        record = data["sessions"].setdefault(name, {})
        for key, value in fields.items():
            if value is None:
                record.pop(key, None)
            else:
                record[key] = value
        save(data)


def remove_session(name: str):
    with _exclusive():
        data = load()
        if data["sessions"].pop(name, None) is not None:
            save(data)


def set_description(name: str, description: str | None):
    with _exclusive():
        data = load()
        if name not in data["sessions"]:
            return False
        if description:
            data["sessions"][name]["description"] = description
        else:
            data["sessions"][name].pop("description", None)
        save(data)
        return True


def mark_ended_and_prune(live_names: set[str], now: float | None = None) -> list[str]:
    """Stamp records whose tmux session is gone, and drop those that have been gone too long.

    Failed starts are kept: they are the user's to dismiss.
    Returns the names that were removed.
    """
    now = time.time() if now is None else now
    removed = []
    with _exclusive():
        data = load()
        changed = False
        for name, record in list(data["sessions"].items()):
            if name in live_names or record.get("status") in ("starting", "failed"):
                continue
            ended_at = record.get("ended_at")
            if ended_at is None:
                record["ended_at"] = now
                changed = True
            elif now - ended_at > ENDED_RETENTION_SECONDS:
                del data["sessions"][name]
                removed.append(name)
                changed = True
        if changed:
            save(data)
    return removed
