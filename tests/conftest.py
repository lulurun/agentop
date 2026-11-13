"""Test fixtures. Everything runs on a private tmux server and a private state directory."""

import os
import stat
import subprocess
import uuid

import pytest

from agentop import agentclis, ops, scanner, tmux
from agentop.agentclis.base import AgentCli


class FakeAgent(AgentCli):
    """An agent whose binary is a small script, so tests never start a real one."""

    name = "fakeagent"
    start_timeout = 5.0

    @property
    def launch_cmd(self):
        return "fakeagent"

    def get_resume_cmd(self, session_id):
        return f"fakeagent --resume {session_id}"


class BrokenAgent(FakeAgent):
    """Its binary does not exist."""

    name = "brokenagent"

    @property
    def launch_cmd(self):
        return "no-such-agent-binary --flag"


class SlowAgent(FakeAgent):
    """Its binary runs but never looks like the agent, so the start times out."""

    name = "slowagent"
    start_timeout = 1.0

    @property
    def launch_cmd(self):
        return "sleep 30"


@pytest.fixture(autouse=True)
def no_real_model(monkeypatch):
    """No test sends a prompt to a real model: the Memory plugin summarizes with whatever tool is installed."""

    def refuse(self, *args, **kwargs):
        raise RuntimeError("tests never call a real model")

    monkeypatch.setattr(AgentCli, "ask", refuse)


@pytest.fixture
def tmux_server(tmp_path, monkeypatch):
    """A private tmux server, with fakeagent on PATH, and agentop state in tmp_path."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    script = bin_dir / "fakeagent"
    script.write_text('#!/bin/sh\necho "fake agent ready"\nwhile read line; do echo "got: $line"; done\n')
    script.chmod(script.stat().st_mode | stat.S_IEXEC)

    socket = f"agentop-test-{uuid.uuid4().hex[:8]}"
    monkeypatch.setenv(tmux.SOCKET_ENV, socket)
    monkeypatch.setenv("AGENTOP_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    monkeypatch.setenv("SHELL", "/bin/sh")
    monkeypatch.setenv("HOME", str(tmp_path))

    # The keeper stops the server from exiting when a test has no sessions left.
    tmux.run("-f", "/dev/null", "new-session", "-d", "-s", "keeper", "-x", "80", "-y", "24")
    tmux.run("set-option", "-g", "default-shell", "/bin/sh")

    yield socket

    subprocess.run(["tmux", "-L", socket, "kill-server"], capture_output=True, check=False)


@pytest.fixture
def fake_agents(tmux_server, monkeypatch):
    agents = [FakeAgent(), BrokenAgent(), SlowAgent()]
    by_name = {a.name: a for a in agents}
    monkeypatch.setattr(agentclis, "AGENTS", agents)
    monkeypatch.setattr(scanner, "AGENTS", agents)
    monkeypatch.setattr(ops, "AGENTS", agents)
    monkeypatch.setattr(scanner, "get_agent", by_name.get)
    monkeypatch.setattr(ops, "get_agent", by_name.get)
    return by_name
