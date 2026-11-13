"""agentop send: find a live session by name, and type a prompt into it."""

import time

from agentop import ops
from agentop.tmux import CapturePane


def session(name, managed=True):
    return {"name": name, "tool": "fakeagent", "managed": managed, "tmux": {"session": name}}


def test_a_name_matches_ignoring_case_and_suffix():
    sessions = [session("Eiger-2953204"), session("Akimoto-9isl7o")]
    for target in ("eiger", "#Eiger", "EIGER-2953204"):
        found = ops.find_session(target, sessions)
        assert found["ok"] and found["session"]["name"] == "Eiger-2953204"


def test_a_full_name_wins_over_a_bare_one():
    sessions = [session("eiger"), session("eiger-e7")]
    assert ops.find_session("eiger", sessions)["session"]["name"] == "eiger"


def test_no_active_session_says_so():
    found = ops.find_session("Eiger", [session("Akimoto-9isl7o")])
    assert not found["ok"]
    assert found["error"] == "I don't see an active session called 'Eiger'"


def test_several_matches_are_listed():
    found = ops.find_session("eiger", [session("Eiger-1"), session("eiger-2")])
    assert not found["ok"]
    assert found["matches"] == ["Eiger-1", "eiger-2"]


def test_a_session_agentop_did_not_start_gets_nothing():
    result = ops.send("eiger", "deploy", [session("eiger-1", managed=False)])
    assert not result["ok"] and "not started by agentop" in result["error"]


def test_the_prompt_is_typed_into_the_target(fake_agents, tmp_path):
    name = ops.start("fakeagent", str(tmp_path), "eiger")["name"]

    result = ops.send("Eiger", "deploy akimoto")

    assert result == {"ok": True, "name": name, "tmux": name}
    deadline = time.monotonic() + 5
    while "got: deploy akimoto" not in CapturePane.screen(name):
        assert time.monotonic() < deadline, "the prompt never arrived"
        time.sleep(0.1)
