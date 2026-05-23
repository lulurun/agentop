"""Manual session registry backed by ~/.agent-dashboard/sessions.json."""

import json
from pathlib import Path

REGISTRY_PATH = Path("~/.agent-dashboard/sessions.json").expanduser()


def _ensure_dir():
    REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)


def load() -> dict:
    _ensure_dir()
    if not REGISTRY_PATH.exists():
        return {"sessions": {}}
    try:
        with open(REGISTRY_PATH) as f:
            data = json.load(f)
        if "sessions" not in data:
            data["sessions"] = {}
        return data
    except (json.JSONDecodeError, OSError):
        return {"sessions": {}}


def save(data: dict):
    _ensure_dir()
    with open(REGISTRY_PATH, "w") as f:
        json.dump(data, f, indent=2)


def get_session(name: str) -> dict | None:
    data = load()
    return data["sessions"].get(name)


def upsert_session(name: str, fields: dict):
    data = load()
    existing = data["sessions"].get(name, {})
    existing.update({k: v for k, v in fields.items() if v is not None})
    data["sessions"][name] = existing
    save(data)


def delete_session(name: str) -> bool:
    data = load()
    if name not in data["sessions"]:
        return False
    del data["sessions"][name]
    save(data)
    return True


def set_status(name: str, status: str):
    data = load()
    if name not in data["sessions"]:
        data["sessions"][name] = {}
    data["sessions"][name]["status"] = status
    save(data)
