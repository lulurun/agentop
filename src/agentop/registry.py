"""Manual session registry backed by ~/.agent-dashboard/sessions.json."""

import fcntl
import json
import os
import tempfile
import threading
from pathlib import Path

REGISTRY_PATH = Path("~/.agent-dashboard/sessions.json").expanduser()

_lock = threading.Lock()


def _ensure_dir():
    REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)


def load() -> dict:
    _ensure_dir()
    if not REGISTRY_PATH.exists():
        return {"sessions": {}}
    try:
        with open(REGISTRY_PATH) as f:
            fcntl.flock(f, fcntl.LOCK_SH)
            try:
                data = json.load(f)
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)
        if "sessions" not in data:
            data["sessions"] = {}
        return data
    except (json.JSONDecodeError, OSError):
        return {"sessions": {}}


def save(data: dict):
    _ensure_dir()
    dir_path = REGISTRY_PATH.parent
    fd, tmp_path = tempfile.mkstemp(dir=dir_path, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                json.dump(data, f, indent=2)
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)
        os.replace(tmp_path, REGISTRY_PATH)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def upsert_session(name: str, fields: dict):
    with _lock:
        data = load()
        existing = data["sessions"].get(name, {})
        existing.update({k: v for k, v in fields.items() if v is not None})
        data["sessions"][name] = existing
        save(data)


def set_description(name: str, description: str | None):
    with _lock:
        data = load()
        if name not in data.get("sessions", {}):
            return False
        if description:
            data["sessions"][name]["description"] = description
        else:
            data["sessions"][name].pop("description", None)
        save(data)
        return True
