"""agentop send: find a live session by name, and type a prompt into it."""

import time

import pytest

from agentop import ops
from agentop.results import OpsError, Sent
from agentop.tmux import CapturePane, PasteError, Session


def session(name, managed=True, **fields):
    return {"name": name, "tool": "fakeagent", "managed": managed, "tmux": {"session": name}, **fields}


def test_a_name_matches_ignoring_case_and_suffix():
    sessions = [session("Eiger-2953204"), session("Akimoto-9isl7o")]
    for target in ("eiger", "#Eiger", "EIGER-2953204"):
        assert ops.find_session(target, sessions)["name"] == "Eiger-2953204"


def test_a_full_name_wins_over_a_bare_one():
    sessions = [session("eiger"), session("eiger-e7")]
    assert ops.find_session("eiger", sessions)["name"] == "eiger"


def test_no_active_session_says_so():
    with pytest.raises(OpsError) as error:
        ops.find_session("Eiger", [session("Akimoto-9isl7o")])
    assert str(error.value) == "I don't see an active session called 'Eiger'"
    assert error.value.matches == []


def test_several_matches_are_listed():
    with pytest.raises(OpsError) as error:
        ops.find_session("eiger", [session("Eiger-1"), session("eiger-2")])
    assert error.value.matches == ["Eiger-1", "eiger-2"]


def test_a_session_agentop_did_not_start_gets_nothing():
    with pytest.raises(OpsError, match="not started by agentop"):
        ops.send("eiger", "deploy", [session("eiger-1", managed=False)])


def test_the_prompt_is_typed_into_the_target(fake_agents, tmp_path):
    name = ops.start("fakeagent", str(tmp_path), "eiger").name

    assert ops.send("Eiger", "deploy akimoto") == Sent(name, name)
    deadline = time.monotonic() + 5
    while "got: deploy akimoto" not in CapturePane.screen(name):
        assert time.monotonic() < deadline, "the prompt never arrived"
        time.sleep(0.1)


def test_a_session_a_plugin_owns_gets_nothing():
    # A dialogue's agents are read-only, as in the app's input bar.
    with pytest.raises(OpsError, match="belongs to the dialogue plugin"):
        ops.send("eiger", "deploy", [session("eiger-1", owner="dialogue")])


def test_the_owner_comes_from_the_registry(fake_agents, tmp_path):
    name = ops.start("fakeagent", str(tmp_path), "eiger", owner="dialogue").name
    with pytest.raises(OpsError, match="belongs to the dialogue plugin"):
        ops.send("Eiger", "deploy akimoto")
    time.sleep(0.5)
    assert "got: deploy akimoto" not in CapturePane.screen(name)


def test_a_failed_paste_is_an_error(fake_agents, tmp_path, monkeypatch):
    ops.start("fakeagent", str(tmp_path), "eiger")

    def fail(name, payload, bracketed=False):
        raise PasteError("paste-buffer failed")

    monkeypatch.setattr(Session, "_load_and_paste", staticmethod(fail))
    with pytest.raises(OpsError, match="paste-buffer failed"):
        ops.send("Eiger", "deploy akimoto")
