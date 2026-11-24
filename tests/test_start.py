import time
from types import SimpleNamespace

import pytest

from agentop import ops, registry
from agentop.results import OpsError, StartFailed
from agentop.tmux import CapturePane, Session


@pytest.fixture
def cwd(tmp_path):
    return str(tmp_path)


def test_cli_can_disable_claude_remote_control(monkeypatch, capsys, cwd):
    from agentop import cli

    seen = {}

    class FakeOps:
        def current_session(self):
            return None

        def get_sessions(self, with_meta=False):
            return []

        def start(self, tool, working_dir, short_name, params, project):
            seen.update(tool=tool, cwd=working_dir, name=short_name, params=params, project=project)
            return SimpleNamespace(name="dev-abc123", pid=123)

    monkeypatch.setattr(cli, "_ops", lambda: FakeOps())
    args = SimpleNamespace(
        tool="claude",
        cwd=cwd,
        session_name="dev",
        project="alpha",
        prompt=None,
        model=None,
        untracked=False,
        no_remote_control=True,
    )

    cli.cmd_start(args)

    assert seen["params"] == {"remote_control": False}
    assert seen["project"] == "alpha"
    assert "Started:  dev-abc123" in capsys.readouterr().out


def test_start_uses_the_same_name_throughout(fake_agents, cwd):
    steps = []
    result = ops.start("fakeagent", cwd, "demo", on_progress=steps.append)
    assert result.pid
    assert result.name.startswith("demo-")
    assert Session.has(result.name)
    assert steps == ["creating", "launching", "waiting", "confirming"]


def test_a_chosen_name_is_used(fake_agents, cwd):
    assert ops.start("fakeagent", cwd, "demo", name="demo-abc123").name == "demo-abc123"


def test_start_registers_the_session(fake_agents, cwd):
    name = ops.start("fakeagent", cwd, "demo").name
    record = registry.load()["sessions"][name]
    assert record["managed"] and record["status"] == "running"
    assert record["tool"] == "fakeagent" and record["cwd"] == cwd
    assert record["display_name"] == "demo"
    assert time.time() - record["created_at"] < 10


def test_missing_binary_fails_fast_and_keeps_the_error_visible(fake_agents, cwd):
    started = time.monotonic()
    with pytest.raises(StartFailed, match="not installed") as failed:
        ops.start("brokenagent", cwd, "demo")
    assert time.monotonic() - started < 3
    assert "not found" in CapturePane.screen(failed.value.name)
    assert registry.load()["sessions"][failed.value.name]["status"] == "failed"


def test_no_agent_process_is_a_timeout_not_a_success(fake_agents, cwd):
    with pytest.raises(StartFailed, match="did not start") as failed:
        ops.start("slowagent", cwd, "demo")
    assert Session.has(failed.value.name)


def test_mouse_is_set_on_the_session(fake_agents, cwd):
    from agentop import tmux

    name = ops.start("fakeagent", cwd, "demo").name
    assert b"mouse on" in tmux.run("show-options", "-t", f"={name}:", "mouse").stdout


def test_get_sessions_finds_the_agent(fake_agents, cwd):
    name = ops.start("fakeagent", cwd, "demo").name
    rows = {s["name"]: s for s in ops.get_sessions()}
    assert rows[name]["managed"] and rows[name]["tool"] == "fakeagent"


def test_stop_ends_the_session(fake_agents, cwd):
    name = ops.start("fakeagent", cwd, "demo").name
    assert ops.stop(name).tmux_killed
    assert not Session.has(name)


def test_resume_goes_through_the_same_start(fake_agents, cwd):
    result = ops.resume_session("fakeagent", "abc", cwd, "demo")
    assert result.agent_session_id == "abc"
    assert registry.load()["sessions"][result.name]["status"] == "running"


def test_an_unknown_tool_is_an_error(fake_agents, cwd):
    with pytest.raises(OpsError, match="Unknown tool"):
        ops.start("nosuchtool", cwd, "demo")


def test_each_tool_starts_with_its_catalogue_models():
    from agentop.agentclis.antigravity import AntigravityAgentCli
    from agentop.agentclis.base import TIERS
    from agentop.agentclis.claude import ClaudeAgentCli
    from agentop.agentclis.codex import CodexAgentCli

    claude = ClaudeAgentCli()
    assert claude.model_id("Sonnet 5.5") == claude.model_id("sonnet 5.5") == "claude-sonnet-5-5"
    assert claude.model_id("claude-opus-5-5") == "claude-opus-5-5"
    assert claude._build_cmd(claude.launch_cmd, {"model": "claude-opus-5-5"}).endswith(" --model claude-opus-5-5")
    with pytest.raises(ValueError, match='"Opus 5.5"'):
        claude.model_id("gpt-6.2-sol")
    for agent in (claude, CodexAgentCli(), AntigravityAgentCli()):
        assert sorted({m.tier for m in agent.models}) == sorted(TIERS)
    assert CodexAgentCli().model_id("GPT-6.2-Sol") == "gpt-6.2-sol"
