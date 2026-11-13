"""Plugin commands for the `agentop` CLI. See docs/v2-design.md, section 12.3.

A plugin adds commands by naming them in its manifest (`cli = ["recall"]`) and
defining `register_cli(cli)` in a `cli.py` beside its `__init__.py`. The CLI
reads the manifests and saved flags itself, with no service:

- A missing plugin's commands don't exist.
- An enabled plugin's `cli.py` is imported and `register_cli` called. Only its
  `cli.py` runs, not its `__init__.py`, so a command starts as fast as a core one.
- A disabled, failed, or incompatible plugin's commands are still listed, and
  running one says why it can't run and what to do.

The core commands never depend on a plugin: a plugin can't take a core
command's name, and a plugin that fails only loses its own commands.
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
import types
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from agentop import paths
from agentop.plugin_manifest import (
    DEFAULT_ROOT,
    MODULE_PREFIX,
    Manifest,
    ManifestError,
    incompatible_reason,
    is_enabled,
    plugin_folders,
    read_flags,
    read_manifest,
)

Handler = Callable[[argparse.Namespace], None]


@dataclass
class _Command:
    name: str
    handler: Handler
    parser: argparse.ArgumentParser
    options: dict


class PluginCli:
    """What `register_cli(cli)` gets: its own folder, and a way to add the commands its manifest names."""

    def __init__(self, manifest: Manifest, data_dir: Path):
        self.id = manifest.id
        self.name = manifest.name
        # The plugin's own folder, ~/.agentop/plugins/{id}/, the same one the service gives it.
        self.data_dir = data_dir
        self._allowed = manifest.cli
        self._commands: dict[str, _Command] = {}

    def command(self, name: str, handler: Handler, **options) -> argparse.ArgumentParser:
        """Add `agentop <name>`, run by handler(args). Returns the parser to add arguments to.

        options are ArgumentParser's: help, description, epilog, formatter_class.
        """
        if name not in self._allowed:
            raise ValueError(f"'{name}' is not among the commands plugin.toml names: {', '.join(self._allowed)}")
        if name in self._commands:
            raise ValueError(f"'{name}' was added twice")
        # Detached until register_cli returns, so a plugin that fails part way adds nothing.
        parser = argparse.ArgumentParser(prog=f"agentop {name}", add_help=False)
        self._commands[name] = _Command(name, handler, parser, options)
        return parser


def add_commands(
    subparsers: argparse._SubParsersAction,
    reserved: set[str],
    root: Path | None = None,
    flags_path: Path | None = None,
) -> dict[str, Handler]:
    """Add every plugin's commands to the CLI. Returns each command's handler, by name.

    reserved holds the core's own command names, which no plugin may take.
    """
    root = root or DEFAULT_ROOT
    flags = read_flags(flags_path or paths.state_dir() / "plugins.json") or {}
    handlers: dict[str, Handler] = {}
    taken = set(reserved)
    for folder in plugin_folders(root):
        try:
            manifest = read_manifest(folder)
        except ManifestError:
            continue  # No manifest, no names: nothing to list.
        # A core command, or one an earlier plugin took, keeps its name.
        names = [n for n in manifest.cli if n not in taken]
        if not names:
            continue
        taken.update(names)
        reason = incompatible_reason(manifest.plugin_api)
        if reason is None and not is_enabled(manifest, flags):
            reason = f"The {manifest.name} plugin is disabled. Enable it in the app, under Settings → Plugins."
        commands: dict[str, _Command] = {}
        if reason is None:
            try:
                commands = _register(folder, manifest)
            except Exception as exc:  # noqa: BLE001 - whatever a plugin raises only loses its commands
                reason = f"The {manifest.name} plugin's commands failed to load: {type(exc).__name__}: {exc}"
        for name in names:
            command = commands.get(name)
            if command is not None:
                subparsers.add_parser(name, parents=[command.parser], **command.options)
                handlers[name] = command.handler
                continue
            why = reason or f"The {manifest.name} plugin names '{name}' but does not add it."
            stub = subparsers.add_parser(name, help=f"(unavailable) from the {manifest.name} plugin")
            # Whatever was typed after it, the answer is the same.
            stub.add_argument("rest", nargs=argparse.REMAINDER, help=argparse.SUPPRESS)
            handlers[name] = _unavailable(why)
    return handlers


def _register(folder: Path, manifest: Manifest) -> dict[str, _Command]:
    """Import the plugin's cli.py, without its __init__.py, and call register_cli."""
    cli_file = folder / "cli.py"
    if not cli_file.is_file():
        raise ImportError("plugin.toml names commands, but there is no cli.py")
    package = f"{MODULE_PREFIX}.{manifest.id}"
    if package not in sys.modules:
        # A bare package, so cli.py's relative imports (from .messages import …)
        # find the plugin's modules, while the __init__.py, which is the
        # service's side and imports FastAPI, does not run.
        shell = types.ModuleType(package)
        shell.__path__ = [str(folder)]
        shell.__package__ = package
        sys.modules[package] = shell
    name = f"{package}.cli"
    spec = importlib.util.spec_from_file_location(name, cli_file)
    if spec is None or spec.loader is None:
        raise ImportError(f"{cli_file} can't be imported")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    register = getattr(module, "register_cli", None)
    if not callable(register):
        raise TypeError("cli.py defines no register_cli(cli)")
    cli = PluginCli(manifest, paths.plugins_dir() / manifest.id)
    register(cli)
    return cli._commands


def _unavailable(reason: str) -> Handler:
    def handler(args: argparse.Namespace) -> None:
        raise SystemExit(f"agentop {args.command}: {reason}")

    return handler
