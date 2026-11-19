"""Projects: a group of sessions and the folder they share, ~/.agentop/projects/<name>/. See docs/project.md.

agentop's own record of a project is in its `.project/` folder: project.json here, sessions.json in registry.
"""

from __future__ import annotations

import json
import re
import shutil
import time
from pathlib import Path

from agentop.jsonfile import write_json_atomic
from agentop.paths import projects_dir

DEFAULT = "default"
CORE = "core"
COORDINATOR = "Coordinator"
META_DIR = ".project"

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
    """Make the folder and its record. Raises ProjectError. The Coordinator is started by coordinator.start_for."""
    if not is_valid_name(name):
        raise ProjectError("A project's name is 1-96 lowercase letters, digits, '-' or '_', starting with a letter or digit")
    if exists(name):
        raise ProjectError(f"There is a project called {name} already")
    meta_dir(name).mkdir(parents=True, exist_ok=True)
    record = {"owner": owner, "created_at": time.time(), "welcome": welcome, "instructions": instructions}
    write_json_atomic(_meta_path(name), record)
    return get(name)


def adopt(name: str, owner: str) -> dict:
    """Make an existing folder a project, as when a plugin's older data moves in. Leaves its files alone."""
    if not exists(name):
        meta_dir(name).mkdir(parents=True, exist_ok=True)
        write_json_atomic(_meta_path(name), {"owner": owner, "created_at": folder(name).stat().st_mtime})
    return get(name)


def ensure_default() -> dict:
    found = get(DEFAULT)
    return found or create(DEFAULT, CORE)


def remove(name: str, delete_folder: bool = False) -> None:
    """Forget a project. Its sessions are the caller's to stop first. Raises ProjectError."""
    if name == DEFAULT:
        raise ProjectError("The default project can't be removed")
    if not exists(name):
        raise ProjectError(f"No project called {name}")
    if delete_folder:
        shutil.rmtree(folder(name))
    else:
        shutil.rmtree(meta_dir(name))


def split_target(target: str) -> tuple[str | None, str]:
    """`other/dev` is (other, dev); `dev` is (None, dev)."""
    head, sep, tail = target.partition("/")
    return (head, tail) if sep and head and tail else (None, target)
