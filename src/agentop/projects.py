"""Projects: a group of sessions and the folder they share, ~/.agentop/projects/<name>/. See docs/0_13_projects.md.

agentop's own record of a project is in its `.project/` folder: project.json here, sessions.json in registry.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

from agentop.jsonfile import write_json_atomic
from agentop.paths import projects_dir

DEFAULT = "default"
CORE = "core"
COORDINATOR = "Coordinator"
META_DIR = ".project"
CODE_DIR = "code"

_NAME = re.compile(r"[a-z0-9][a-z0-9_-]{0,95}")


class ProjectError(ValueError):
    pass


def is_valid_name(name: str) -> bool:
    return bool(_NAME.fullmatch(name or ""))


def folder(name: str) -> Path:
    return projects_dir() / name


def meta_dir(name: str) -> Path:
    return folder(name) / META_DIR


def _meta_path(name: str) -> Path:
    return meta_dir(name) / "project.json"


def get(name: str) -> dict | None:
    """The project's record, or None when there is no such project."""
    if not is_valid_name(name):
        return None
    try:
        data = json.loads(_meta_path(name).read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    return {**data, "name": name, "folder": str(folder(name))}


def exists(name: str) -> bool:
    return get(name) is not None


def kept(name: str) -> bool:
    """A folder with no record: a closed project's, which create takes back as it is."""
    return is_valid_name(name) and folder(name).is_dir() and not exists(name)


def list_projects(owner: str | None = None) -> list[dict]:
    """Every project, or one owner's, oldest first."""
    root = projects_dir()
    if not root.is_dir():
        return []
    found = [p for p in (get(d.name) for d in root.iterdir() if d.is_dir()) if p]
    if owner is not None:
        found = [p for p in found if p.get("owner") == owner]
    return sorted(found, key=lambda p: (p.get("created_at") or 0, p["name"]))


def create(name: str, owner: str, welcome: str = "", instructions: str = "") -> dict:
    """Make the folder and its record, or the record only on a closed project's kept folder. Raises ProjectError."""
    if not is_valid_name(name):
        raise ProjectError("A project's name is 1-96 lowercase letters, digits, '-' or '_', starting with a letter or digit")
    if exists(name):
        raise ProjectError(f"There is a project called {name} already")
    meta_dir(name).mkdir(parents=True, exist_ok=True)
    (folder(name) / CODE_DIR).mkdir(exist_ok=True)
    record = {"owner": owner, "created_at": time.time(), "welcome": welcome, "instructions": instructions}
    write_json_atomic(_meta_path(name), record)
    return get(name)


def adopt(name: str, owner: str, welcome: str = "", instructions: str = "") -> dict:
    """Make an existing folder a project, as when a plugin's older data moves in. Leaves its files alone."""
    if not exists(name):
        meta_dir(name).mkdir(parents=True, exist_ok=True)
        record = {"owner": owner, "created_at": folder(name).stat().st_mtime, "welcome": welcome, "instructions": instructions}
        write_json_atomic(_meta_path(name), record)
    (folder(name) / CODE_DIR).mkdir(exist_ok=True)
    return get(name)


def fill_in(name: str, welcome: str, instructions: str) -> dict:
    """Give a project its owner's texts where it has none, as one adopted before it had them."""
    found = get(name)
    if found is None:
        raise ProjectError(f"No project called {name}")
    record = json.loads(_meta_path(name).read_text())
    if not record.get("welcome") and not record.get("instructions"):
        record.update(welcome=welcome, instructions=instructions)
        write_json_atomic(_meta_path(name), record)
    return get(name)


def has_coordinator(name: str) -> bool:
    """The one difference between projects: `default` is driven by the owner, every other one by its Coordinator."""
    return name != DEFAULT


def ensure_default() -> dict:
    found = get(DEFAULT)
    return found or create(DEFAULT, CORE)


def close(name: str) -> None:
    """Drop a project's entry, .project/project.json; its folder is kept, to be created again on. Never deletes a folder."""
    if name == DEFAULT:
        raise ProjectError("The default project can't be closed")
    if not exists(name):
        raise ProjectError(f"No project called {name}")
    _meta_path(name).unlink()


def split_target(target: str) -> tuple[str | None, str]:
    """`other/dev` is (other, dev); `dev` is (None, dev)."""
    head, sep, tail = target.partition("/")
    return (head, tail) if sep and head and tail else (None, target)
