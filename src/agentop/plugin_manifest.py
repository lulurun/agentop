"""What the core knows about a plugin without running any of its code: its manifest, and its saved flag.

Shared by the service's loader (service/plugins.py) and the CLI (plugin_cli.py).
It imports nothing heavy, since every `agentop` command reads it.
"""

from __future__ import annotations

import json
import logging
import os
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

LOG = logging.getLogger(__name__)

# The plugin API this core offers. A plugin declares the version it was written for.
PLUGIN_API = 1
SUPPORTED_PLUGIN_APIS = range(1, PLUGIN_API + 1)

DEFAULT_ROOT = Path(__file__).resolve().parent / "plugins"
# More folders to find plugins in, separated by ':'. The built-in folder is always searched first.
DIRS_ENV = "AGENTOP_PLUGIN_DIRS"
MODULE_PREFIX = "agentop.plugins"

UI_SLOTS = frozenset({"session-badge", "workspace", "right-panel", "settings-section", "file-viewer"})

_ID = re.compile(r"[a-z0-9-]+")
# A command name: what follows `agentop`.
_COMMAND = re.compile(r"[a-z][a-z0-9-]*")


class ManifestError(Exception):
    pass


@dataclass(frozen=True)
class Manifest:
    id: str
    name: str
    version: str
    description: str
    plugin_api: int
    default_enabled: bool
    ui: tuple[str, ...]
    # The `agentop` commands the plugin adds. Named here so the CLI can list a
    # disabled or failed plugin's commands without running its code.
    cli: tuple[str, ...]


def read_manifest(folder: Path) -> Manifest:
    """Read and check a plugin.toml without running any of the plugin's code."""
    try:
        data = tomllib.loads((folder / "plugin.toml").read_text())
    except FileNotFoundError:
        raise ManifestError("No plugin.toml") from None
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ManifestError(f"plugin.toml can't be read: {exc}") from None

    def need(key: str, kind: type, default: Any = None):
        value = data.get(key, default)
        # bool is an int in Python, so check it first.
        if value is None or (kind is int and isinstance(value, bool)) or not isinstance(value, kind):
            raise ManifestError(f"plugin.toml: '{key}' must be a {kind.__name__}")
        return value

    def names(key: str) -> list[str]:
        value = need(key, list, [])
        if not all(isinstance(item, str) for item in value):
            raise ManifestError(f"plugin.toml: '{key}' must be a list of names")
        return value

    plugin_id = need("id", str)
    if not _ID.fullmatch(plugin_id):
        raise ManifestError("plugin.toml: 'id' may only contain a-z, 0-9 and '-'")
    if plugin_id != folder.name:
        raise ManifestError(f"plugin.toml: 'id' is '{plugin_id}' but the folder is '{folder.name}'")
    ui = names("ui")
    unknown = sorted(set(ui) - UI_SLOTS)
    if unknown:
        raise ManifestError(f"plugin.toml: unknown UI slot {', '.join(unknown)}")
    cli = names("cli")
    bad = [name for name in cli if not _COMMAND.fullmatch(name)]
    if bad:
        raise ManifestError(f"plugin.toml: bad command name {', '.join(bad)}: a-z, 0-9 and '-', starting with a letter")
    return Manifest(
        id=plugin_id,
        name=need("name", str),
        version=need("version", str),
        description=need("description", str, ""),
        plugin_api=need("plugin_api", int),
        default_enabled=need("default_enabled", bool, True),
        ui=tuple(ui),
        cli=tuple(dict.fromkeys(cli)),
    )


def plugin_roots() -> list[Path]:
    """The folders plugins are found in: the built-in one, then each one in AGENTOP_PLUGIN_DIRS."""
    roots = [DEFAULT_ROOT]
    for part in os.environ.get(DIRS_ENV, "").split(os.pathsep):
        root = Path(part).expanduser().resolve() if part.strip() else None
        if root is not None and root not in roots:
            roots.append(root)
    return roots


def plugin_folders(roots: list[Path]) -> list[Path]:
    """The folders under the roots that may be plugins. When two share a name, the first root's wins."""
    folders: dict[str, Path] = {}
    for root in roots:
        try:
            entries = sorted(root.iterdir())
        except (FileNotFoundError, NotADirectoryError):
            continue
        for entry in entries:
            # __pycache__, hidden folders, and stray files are not plugins.
            if not entry.is_dir() or entry.name.startswith(("_", ".")):
                continue
            if entry.name in folders:
                LOG.warning("Plugin folder %s skipped: %s has the same name", entry, folders[entry.name])
                continue
            folders[entry.name] = entry
    return list(folders.values())


def incompatible_reason(version: int) -> str | None:
    """Why a plugin written for this plugin API can't run here, or None if it can."""
    if version in SUPPORTED_PLUGIN_APIS:
        return None
    side = "the plugin" if version < SUPPORTED_PLUGIN_APIS.start else "agentop"
    return f"Written for plugin API {version}; this agentop supports {PLUGIN_API}. Update {side}."


def read_flags(path: Path) -> dict | None:
    """The saved enabled flags, {} when none are saved, or None when the file can't be read."""
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def is_enabled(manifest: Manifest, flags: dict) -> bool:
    flag = flags.get(manifest.id)
    if isinstance(flag, dict) and isinstance(flag.get("enabled"), bool):
        return flag["enabled"]
    return manifest.default_enabled
