"""Plugin commands in the agentop CLI: added from the manifest, without the service or the plugin's __init__.py."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import textwrap

import pytest

from agentop import plugin_cli


def plugin(root, plugin_id, cli_py=None, init_py=None, **manifest):
    folder = root / plugin_id
    folder.mkdir(parents=True)
    fields = {"id": plugin_id, "name": plugin_id.title(), "version": "1", "plugin_api": 1, **manifest}
    lines = []
    for key, value in fields.items():
        lines.append(f"{key} = {json.dumps(value)}")
    (folder / "plugin.toml").write_text("\n".join(lines) + "\n")
    # The service's side: the CLI must never run it.
    (folder / "__init__.py").write_text(init_py or "raise RuntimeError('the CLI ran __init__.py')\n")
    if cli_py is not None:
        (folder / "cli.py").write_text(textwrap.dedent(cli_py))
    return folder


GREET = """
    from .words import HELLO

    def register_cli(cli):
        parser = cli.command("greet", lambda args: print(HELLO, args.who, cli.data_dir.name), help="Say hello")
        parser.add_argument("who")
"""


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTOP_HOME", str(tmp_path / "home"))
    root = tmp_path / "plugins"
    root.mkdir()
    # Each test's plugins are new modules.
    for name in [m for m in sys.modules if m.startswith("agentop.plugins.p-")]:
        monkeypatch.delitem(sys.modules, name)
    return root


def build(root, flags=None, reserved=("list", "serve")):
    parser = argparse.ArgumentParser(prog="agentop")
    sub = parser.add_subparsers(dest="command")
    for name in reserved:
        sub.add_parser(name)
    flags_path = root.parent / "flags.json"
    flags_path.write_text(json.dumps(flags or {}))
    handlers = plugin_cli.add_commands(sub, set(reserved), root=root, flags_path=flags_path)
    return parser, handlers


def run(root, *argv, **kwargs):
    parser, handlers = build(root, **kwargs)
    args = parser.parse_args(argv)
    handlers[args.command](args)


def test_an_enabled_plugin_adds_its_command_without_running_init(env, capsys):
    folder = plugin(env, "p-greet", GREET, cli=["greet"])
    (folder / "words.py").write_text("HELLO = 'hello'\n")
    run(env, "greet", "world")
    assert capsys.readouterr().out == "hello world p-greet\n"


def test_a_disabled_plugin_keeps_its_command_and_says_how_to_enable_it(env):
    plugin(env, "p-greet", GREET, cli=["greet"])
    with pytest.raises(SystemExit, match="disabled.*Settings → Plugins"):
        run(env, "greet", "anything", "--at-all", flags={"p-greet": {"enabled": False}})


def test_default_enabled_false_counts_as_disabled(env):
    plugin(env, "p-greet", GREET, cli=["greet"], default_enabled=False)
    with pytest.raises(SystemExit, match="disabled"):
        run(env, "greet")


def test_an_incompatible_plugin_says_so(env):
    plugin(env, "p-greet", GREET, cli=["greet"], plugin_api=99)
    with pytest.raises(SystemExit, match="plugin API 99"):
        run(env, "greet")


def test_a_failing_cli_py_loses_only_its_own_commands(env, capsys):
    plugin(env, "p-broken", "raise ValueError('boom')\n", cli=["broken"])
    folder = plugin(env, "p-greet", GREET, cli=["greet"])
    (folder / "words.py").write_text("HELLO = 'hi'\n")
    with pytest.raises(SystemExit, match="failed to load: ValueError: boom"):
        run(env, "broken")
    run(env, "greet", "you")
    assert capsys.readouterr().out.startswith("hi you")


def test_a_plugin_that_fails_part_way_adds_nothing(env):
    half = """
        def register_cli(cli):
            cli.command("one", lambda args: print("one"))
            raise RuntimeError("then it broke")
    """
    plugin(env, "p-half", half, cli=["one"])
    parser, handlers = build(env)
    with pytest.raises(SystemExit, match="then it broke"):
        handlers["one"](parser.parse_args(["one"]))


def test_a_command_the_manifest_does_not_name_is_refused(env):
    sneaky = """
        def register_cli(cli):
            cli.command("other", lambda args: None)
    """
    plugin(env, "p-sneaky", sneaky, cli=["named"])
    parser, handlers = build(env)
    assert "other" not in handlers
    with pytest.raises(SystemExit, match="not among the commands"):
        handlers["named"](parser.parse_args(["named"]))


def test_a_named_command_never_added_says_so(env):
    plugin(env, "p-lazy", "def register_cli(cli):\n    pass\n", cli=["lazy"])
    with pytest.raises(SystemExit, match="names 'lazy' but does not add it"):
        run(env, "lazy")


def test_no_cli_py_is_a_failure(env):
    plugin(env, "p-nofile", None, cli=["nofile"])
    with pytest.raises(SystemExit, match="no cli.py"):
        run(env, "nofile")


def test_a_core_command_keeps_its_name(env):
    takeover = """
        def register_cli(cli):
            cli.command("mine", lambda args: print("mine"))
    """
    plugin(env, "p-takeover", takeover, cli=["list", "mine"])
    parser, handlers = build(env)
    assert "list" not in handlers and "mine" in handlers


def test_two_plugins_with_one_name_the_first_keeps_it(env, capsys):
    plugin(env, "p-a", "def register_cli(cli):\n    cli.command('same', lambda args: print('a'))\n", cli=["same"])
    plugin(env, "p-b", "def register_cli(cli):\n    cli.command('same', lambda args: print('b'))\n", cli=["same"])
    run(env, "same")
    assert capsys.readouterr().out == "a\n"


def test_a_missing_or_broken_manifest_adds_nothing(env):
    (env / "p-empty").mkdir()
    (env / "p-bad").mkdir()
    (env / "p-bad" / "plugin.toml").write_text("not toml [")
    _, handlers = build(env)
    assert handlers == {}


def test_bad_command_names_are_a_manifest_error(env):
    plugin(env, "p-bad", GREET, cli=["Bad Name"])
    _, handlers = build(env)
    assert handlers == {}


def test_the_memory_plugin_provides_recall(tmp_path):
    out = subprocess.run(
        [sys.executable, "-m", "agentop.cli", "--help"],
        env={"AGENTOP_HOME": str(tmp_path), "PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "PYTHONPATH": "src"},
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert "recall" in out
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "plugins.json").write_text(json.dumps({"memory": {"enabled": False}}))
    result = subprocess.run(
        [sys.executable, "-m", "agentop.cli", "recall", "atama"],
        env={"AGENTOP_HOME": str(tmp_path), "PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "PYTHONPATH": "src"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert "Memory plugin is disabled" in result.stderr
