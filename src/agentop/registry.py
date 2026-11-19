"""Managed-session registry: each project's .project/sessions.json, keyed by tmux session name.

Both the CLI and the service write these files, so every read-modify-write holds
one lock on a separate file. Locking a JSON file itself would not work: writes
replace it, so two processes would hold locks on different inodes.
"""

from __future__ import annotations

import fcntl
import json
import logging
import re
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from agentop import projects
from agentop.jsonfile import move_aside, write_json_atomic
from agentop.paths import projects_dir, state_dir

LOG = logging.getLogger(__name__)

# The least time an ended session's record is kept: History lists it from here.
ENDED_RETENTION_SECONDS = 30 * 24 * 3600

_lock = threading.Lock()
# The date and time agentop used to add to a resumed session's name.
_RESUMED_AT = re.compile(r"\s+\d{4}-\d{2}-\d{2} \d{2}:\d{2}$")


def registry_path(project: str = projects.DEFAULT) -> Path:
    return projects.meta_dir(project) / "sessions.json"


def _legacy_path() -> Path:
    return state_dir() / "sessions.json"


@contextmanager
def _exclusive():
    """Hold the registry lock, across threads and across processes."""
    state_dir().mkdir(parents=True, exist_ok=True)
    with _lock, open(state_dir() / "sessions.lock", "w") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        _migrate()
        yield


class RegistryError(RuntimeError):
    """The registry can't be read, so it isn't written: a write would replace every record with nothing."""


def _parse(text: str) -> dict:
    data = json.loads(text)
    if not isinstance(data, dict) or not isinstance(data.setdefault("sessions", {}), dict):
        raise TypeError("not a registry")
    return data


def _project_names() -> list[str]:
    root = projects_dir()
    if not root.is_dir():
        return []
    return sorted(d.name for d in root.iterdir() if (d / projects.META_DIR / "sessions.json").is_file())


def _read(project: str) -> dict:
    path = registry_path(project)
    try:
        return _parse(path.read_text())["sessions"]
    except FileNotFoundError:
        return {}
    except (OSError, ValueError, TypeError) as exc:
        LOG.warning("%s can't be read (%s); listing no records", path, exc)
        return {}


def load() -> dict:
    """Every project's records, each with its `project`. An unreadable file reads as empty; only a write moves it aside."""
    if _legacy_path().exists():
        with _exclusive():
            pass
    sessions = {}
    for project in _project_names():
        for name, record in _read(project).items():
            sessions[name] = {**record, "project": project}
    return {"sessions": sessions}


def _load_for_update(project: str) -> dict:
    """One project's file, to change and save. Call it holding the lock. Raises RegistryError rather than return an empty one.

    A file that isn't a registry is moved aside first, so it can be recovered by
    hand and the next write starts a new one. A read that fails (a full disk, a
    permission) leaves the file where it is.
    """
    path = registry_path(project)
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


def _save(project: str, data: dict):
    if project == projects.DEFAULT:
        projects.ensure_default()
    elif not projects.exists(project):
        raise RegistryError(f"No project called {project}")
    write_json_atomic(registry_path(project), data)


def _project_of(name: str) -> str | None:
    return next((p for p in _project_names() if name in _read(p)), None)


def upsert_session(name: str, fields: dict):
    """Merge fields into a record. A value of None removes that field.

    `project` places a new record; a record stays in the project it is in.
    """
    fields = dict(fields)
    wanted = fields.pop("project", None)
    with _exclusive():
        project = _project_of(name) or wanted or projects.DEFAULT
        data = _load_for_update(project)
        record = data["sessions"].setdefault(name, {})
        for key, value in fields.items():
            if value is None:
                record.pop(key, None)
            else:
                record[key] = value
        _save(project, data)


def move_session(name: str, project: str):
    """Move a record to another project, as when a plugin's older sessions move into their project."""
    with _exclusive():
        source = _project_of(name)
        if source is None or source == project:
            return
        data = _load_for_update(source)
        record = data["sessions"].pop(name)
        target = _load_for_update(project)
        target["sessions"][name] = record
        _save(project, target)
        _save(source, data)


def remove_session(name: str):
    with _exclusive():
        project = _project_of(name)
        if project is None:
            return
        data = _load_for_update(project)
        data["sessions"].pop(name, None)
        _save(project, data)


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
        for project in _project_names():
            data = _load_for_update(project)
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
                _save(project, data)
    return removed


def remove_agent_session(tool: str, agent_session_id: str) -> list[str]:
    """Drop every record of the agent session, as when its transcript is deleted. Returns their names."""
    removed = []
    with _exclusive():
        for project in _project_names():
            data = _load_for_update(project)
            names = [
                name
                for name, record in data["sessions"].items()
                if record.get("tool") == tool and record.get("agent_session_id") == agent_session_id
            ]
            for name in names:
                del data["sessions"][name]
            if names:
                _save(project, data)
                removed += names
    return removed


def _migrate() -> None:
    """Move ~/.agentop/state/sessions.json, from before projects, into the default project. Call it holding the lock.

    A plugin's sessions (`owner`) become read-only and uncounted; the date a resume
    added to a name goes.
    """
    legacy = _legacy_path()
    if not legacy.exists():
        return
    try:
        old = _parse(legacy.read_text())["sessions"]
    except (OSError, ValueError, TypeError) as exc:
        aside = move_aside(legacy, f"isn't a valid registry ({exc})")
        LOG.warning("%s was moved to %s, not into the default project", legacy, aside)
        return
    for record in old.values():
        if record.pop("owner", None):
            record.update(readonly=True, counted=False)
        if record.get("display_name"):
            record["display_name"] = _RESUMED_AT.sub("", record["display_name"]).strip() or record["display_name"]
    data = _load_for_update(projects.DEFAULT)
    data["sessions"] = {**old, **data["sessions"]}
    _save(projects.DEFAULT, data)
    legacy.rename(legacy.with_name("sessions.json.migrated"))
    LOG.info("Moved %d session records into the default project", len(old))
