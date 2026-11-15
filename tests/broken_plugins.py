"""Plugins that are broken on purpose, each in its own way.

tests/test_plugins.py checks that each one fails with its reason and leaves the
core working. To run the other suites with all of them installed:

    python -m tests.broken_plugins /tmp/broken-plugins
    AGENTOP_PLUGIN_DIRS=/tmp/broken-plugins pytest
    (cd ui && AGENTOP_PLUGIN_DIRS=/tmp/broken-plugins npx playwright test)
"""

from __future__ import annotations

import json
import shutil
import sys
import textwrap
from pathlib import Path

from agentop.service.plugins import REGISTER_TIMEOUT_SECONDS


def manifest(plugin_id, **overrides):
    fields = {
        "id": plugin_id,
        "name": plugin_id.title(),
        "version": "0.1.0",
        "description": f"The {plugin_id} plugin",
        "plugin_api": 1,
        "default_enabled": True,
        **overrides,
    }
    return "".join(f"{k} = {json.dumps(v)}\n" for k, v in fields.items())


def write_plugin(root: Path, plugin_id: str, code: str | None, toml: str | None = None, **overrides) -> Path:
    folder = root / plugin_id
    folder.mkdir(parents=True)
    (folder / "plugin.toml").write_text(manifest(plugin_id, **overrides) if toml is None else toml)
    if code is not None:
        (folder / "__init__.py").write_text(textwrap.dedent(code))
    return folder


# Each fails while loading: (code, plugin.toml, part of the reason it fails with).
# register-hangs sleeps past the time limit that tests/test_plugins.py sets, 1s.
BROKEN = {
    "bad-toml": (None, "id = [unclosed\n", "plugin.toml can't be read"),
    "no-name": ("def register(api): pass\n", 'id = "no-name"\nversion = "1"\nplugin_api = 1\n', "'name' must be a str"),
    "wrong-id": ("def register(api): pass\n", manifest("other"), "but the folder is 'wrong-id'"),
    "bad-slot": ("def register(api): pass\n", manifest("bad-slot", ui=["toolbar"]), "unknown UI slot toolbar"),
    "no-init": (None, None, "No __init__.py"),
    "no-register": ("X = 1\n", None, "defines no register"),
    "import-raises": ("raise RuntimeError('boom at import')\n", None, "RuntimeError: boom at import"),
    "register-raises": ("def register(api):\n    raise ValueError('boom in register')\n", None, "ValueError: boom in register"),
    "register-hangs": ("import time\ndef register(api):\n    time.sleep(3)\n", None, "register() took longer"),
    "bad-event": ("def register(api):\n    api.on('nope', print)\n", None, "Unknown event 'nope'"),
}

# Each loads, then fails while running.
FAILS_LATER = {
    "route-raises": "def register(api):\n    @api.router.get('/boom')\n    def boom():\n        raise RuntimeError('route boom')\n",
    "task-crashes": "async def crash():\n    raise RuntimeError('task boom')\n\ndef register(api):\n    api.task(crash)\n",
    "handler-raises": "def register(api):\n    def handler(rows):\n        raise RuntimeError('handler boom')\n    api.on('scan', handler)\n",
    # Claims a UI slot but has no UI in the app.
    "ui-missing": ("def register(api): pass\n", manifest("ui-missing", ui=["session-badge", "right-panel"])),
}


def install(root: Path) -> list[str]:
    """Write every broken plugin into root, replacing earlier copies of them only. Returns their ids."""
    root.mkdir(parents=True, exist_ok=True)
    for plugin_id in [*BROKEN, *FAILS_LATER]:
        shutil.rmtree(root / plugin_id, ignore_errors=True)
    for plugin_id, (code, toml, _) in BROKEN.items():
        if plugin_id == "register-hangs":
            # Past the service's own time limit, so it fails there too.
            code = f"import time\ndef register(api):\n    time.sleep({REGISTER_TIMEOUT_SECONDS + 1:g})\n"
        write_plugin(root, plugin_id, code, toml)
    for plugin_id, spec in FAILS_LATER.items():
        code, toml = spec if isinstance(spec, tuple) else (spec, None)
        write_plugin(root, plugin_id, code, toml)
    return sorted([*BROKEN, *FAILS_LATER])


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: python -m tests.broken_plugins FOLDER")
    target = Path(sys.argv[1]).expanduser().resolve()
    ids = install(target)
    print(f"Wrote {len(ids)} broken plugins to {target}. Run the suites with AGENTOP_PLUGIN_DIRS={target}")
