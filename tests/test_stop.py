"""Stopping an agent: clear its input, ask it to exit, and force it only when it won't."""

import time

from agentop import agent_instance
from agentop.agent_instance import AgentInstance
from agentop.tmux import Session
from tests.conftest import FakeAgent

# Ignores Ctrl-C like a real agent CLI, logs each line it reads, and quits on /exit.
POLITE = """trap '' INT
echo ready
while read line; do
  echo "$line" >> {log}
  [ "$line" = /exit ] && exit 0
done
"""

# Ignores Ctrl-C and /exit alike.
STUBBORN = """trap '' INT
echo ready
while read line; do :; done
"""


def launch(tmp_path, name, script):
    path = tmp_path / f"{name}.sh"
    path.write_text(script.format(log=tmp_path / f"{name}.log"))
    assert Session.new(name, str(tmp_path))
    Session.send_keys(name, f"sh {path}", "Enter")
    instance = AgentInstance(FakeAgent(), name)
    deadline = time.monotonic() + 5
    while not instance.agent_running():
        assert time.monotonic() < deadline, "the agent never started"
        time.sleep(0.1)
    time.sleep(0.3)  # let it reach `read`
    return instance


def test_a_draft_is_cleared_before_exit(tmux_server, tmp_path):
    instance = launch(tmp_path, "polite", POLITE)
    Session.send_keys("polite", "half a draft")

    result = instance.stop()

    assert result == {"sent_exit": True, "exited": True, "forced": False, "tmux_killed": True}
    assert (tmp_path / "polite.log").read_text().splitlines() == ["/exit"]
    assert not Session.has("polite")


def test_an_agent_that_wont_exit_is_forced(tmux_server, tmp_path, monkeypatch):
    monkeypatch.setattr(agent_instance, "_EXIT_TIMEOUT", 1.0)
    instance = launch(tmp_path, "stubborn", STUBBORN)

    result = instance.stop()

    assert result == {"sent_exit": True, "exited": False, "forced": True, "tmux_killed": True}
    assert not Session.has("stubborn")


def test_an_agent_that_quits_on_ctrl_c_is_not_sent_exit(fake_agents, tmp_path):
    assert Session.new("quick", str(tmp_path))
    Session.send_keys("quick", "fakeagent", "Enter")
    instance = AgentInstance(FakeAgent(), "quick")
    deadline = time.monotonic() + 5
    while not instance.agent_running():
        assert time.monotonic() < deadline
        time.sleep(0.1)

    result = instance.stop()

    assert result == {"sent_exit": False, "exited": True, "forced": False, "tmux_killed": True}


def test_a_session_with_no_agent_is_just_closed(tmux_server, tmp_path):
    assert Session.new("idle", str(tmp_path))
    result = AgentInstance(FakeAgent(), "idle").stop()
    assert result == {"sent_exit": False, "exited": True, "forced": False, "tmux_killed": True}
