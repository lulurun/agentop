import time

import pytest

from agentop import ops, registry
from agentop.results import OpsError, StartFailed
from agentop.tmux import CapturePane, Session


@pytest.fixture
def cwd(tmp_path):
    return str(tmp_path)


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


def test_claude_starts_only_with_sonnet_5_5():
    from agentop.agentclis.claude import ClaudeAgentCli

    claude = ClaudeAgentCli()
    assert claude.model_id("Sonnet 5.5") == claude.model_id("sonnet 5.5") == "claude-sonnet-5-5"
    assert claude._build_cmd(claude.launch_cmd, {"model": "claude-sonnet-5-5"}).endswith(" --model claude-sonnet-5-5")
    with pytest.raises(ValueError, match='"Sonnet 5.5"'):
        claude.model_id("opus")


def test_codex_and_antigravity_take_no_model():
    from agentop.agentclis.antigravity import AntigravityAgentCli
    from agentop.agentclis.codex import CodexAgentCli

    for agent in (CodexAgentCli(), AntigravityAgentCli()):
        with pytest.raises(ValueError, match="only its default"):
            agent.model_id("Sonnet 5.5")
