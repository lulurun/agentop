"""Managed-session registry backed by ~/.agentop/state/sessions.json.

Both the CLI and the service write this file, so every read-modify-write holds
a lock on a separate file. Locking the JSON file itself would not work: writes
replace it, so two processes would hold locks on different inodes.
"""

from __future__ import annotations

import fcntl
import json
import logging
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from agentop.jsonfile import move_aside, write_json_atomic
from agentop.paths import state_dir

LOG = logging.getLogger(__name__)

# The least time an ended session's record is kept: History lists it from here.
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


class RegistryError(RuntimeError):
    """The registry can't be read, so it isn't written: a write would replace every record with nothing."""


def _parse(text: str) -> dict:
    data = json.loads(text)
    if not isinstance(data, dict) or not isinstance(data.setdefault("sessions", {}), dict):
        raise TypeError("not a registry")
    return data


def load() -> dict:
    """The registry, to read. An unreadable one reads as empty; only a write, under the lock, moves it aside."""
    path = registry_path()
    try:
        return _parse(path.read_text())
    except FileNotFoundError:
        return {"sessions": {}}
    except (OSError, ValueError, TypeError) as exc:
        LOG.warning("%s can't be read (%s); listing no records", path, exc)
        return {"sessions": {}}


def _load_for_update() -> dict:
    """The registry, to change and save. Call it holding the lock. Raises RegistryError rather than return an empty one.

    A file that isn't a registry is moved aside first, so it can be recovered by
    hand and the next write starts a new one. A read that fails (a full disk, a
    permission) leaves the file where it is.
    """
    path = registry_path()
    try:
        text = path.read_text()
    except FileNotFoundError:
        return {"sessions": {}}
    except OSError as exc:
        raise RegistryError(f"{path} can't be read: {exc}") from exc
    try:
        return _parse(text)
    except (ValueError, TypeError) as exc:
        aside = move_aside(path, f"isn't a valid registry ({exc})")
        raise RegistryError(f"{path} wasn't a valid registry; it was moved to {aside}") from exc


def save(data: dict):
    write_json_atomic(registry_path(), data)


def upsert_session(name: str, fields: dict):
    """Merge fields into a record. A value of None removes that field."""
    with _exclusive():
        data = _load_for_update()
        record = data["sessions"].setdefault(name, {})
        for key, value in fields.items():
            if value is None:
                record.pop(key, None)
            else:
                record[key] = value
        save(data)


def remove_session(name: str):
    with _exclusive():
        data = _load_for_update()
        if data["sessions"].pop(name, None) is not None:
            save(data)


def mark_ended_and_prune(live_names: set[str], now: float | None = None, retention: float | None = None) -> list[str]:
    """Stamp records whose tmux session is gone, and drop those that have been gone too long.

    `retention` is how long that is, at least ENDED_RETENTION_SECONDS.
    Failed starts are kept: they are the user's to dismiss.
    Returns the names that were removed.
    """
    now = time.time() if now is None else now
    retention = max(retention or 0, ENDED_RETENTION_SECONDS)
    removed = []
    with _exclusive():
        data = _load_for_update()
        changed = False
        for name, record in list(data["sessions"].items()):
            if name in live_names or record.get("status") in ("starting", "failed"):
                continue
            ended_at = record.get("ended_at")
            if ended_at is None:
                record["ended_at"] = now
                changed = True
            elif now - ended_at > retention:
                del data["sessions"][name]
                removed.append(name)
                changed = True
        if changed:
            save(data)
    return removed


def remove_agent_session(tool: str, agent_session_id: str) -> list[str]:
    """Drop every record of the agent session, as when its transcript is deleted. Returns their names."""
    with _exclusive():
        data = _load_for_update()
        names = [
            name
            for name, record in data["sessions"].items()
            if record.get("tool") == tool and record.get("agent_session_id") == agent_session_id
        ]
        for name in names:
            del data["sessions"][name]
        if names:
            save(data)
    return names
