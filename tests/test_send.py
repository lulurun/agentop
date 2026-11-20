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


def test_a_read_only_session_gets_nothing():
    # A dialogue's agents are read-only, as in the app's input bar.
    with pytest.raises(OpsError, match="is read-only"):
        ops.send("eiger", "deploy", [session("eiger-1", readonly=True)])


def test_a_name_is_looked_up_in_the_senders_project():
    sessions = [
        session("dev-a1", project="alpha", display_name="dev"),
        session("dev-b2", project="beta", display_name="dev"),
    ]
    assert ops.find_session("dev", sessions, "alpha")["name"] == "dev-a1"
    assert ops.find_session("Dev", sessions, "beta")["name"] == "dev-b2"
    assert ops.find_session("beta/dev", sessions, "alpha")["name"] == "dev-b2"
    with pytest.raises(OpsError, match="in gamma"):
        ops.find_session("dev", sessions, "gamma")


def test_read_only_comes_from_the_registry(fake_agents, tmp_path):
    name = ops.start("fakeagent", str(tmp_path), "eiger", readonly=True, counted=False).name
    with pytest.raises(OpsError, match="is read-only"):
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


def test_prompts_into_one_session_queue_so_none_lands_between_a_paste_and_its_enter(monkeypatch):
    import threading

    from agentop.agentclis.base import AgentCli

    typed = []

    class Slow(AgentCli):
        name = "slow"

        def send_text(self, session, text):
            typed.append(text)
            time.sleep(0.05)
            typed.append("Enter")

    monkeypatch.setattr(Session, "leave_mode", staticmethod(lambda name: None))
    agent = Slow()
    threads = [threading.Thread(target=agent.deliver, args=("coordinator", f"p{n}")) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert typed[1::2] == ["Enter"] * 4
    assert sorted(typed[0::2]) == ["p0", "p1", "p2", "p3"]


@pytest.fixture
def service(monkeypatch):
    from fastapi.testclient import TestClient

    from agentop.service.app import create_app
    from agentop.service.config import Settings
    from agentop.service.identity import Identity
    from tests.conftest import signed_in
    from tests.test_plugins import TOKEN, QuietStore

    app = create_app(Settings(), Identity("service-token", "iid", "test"), QuietStore(), clients=signed_in(TOKEN))
    with TestClient(app) as client:
        yield client


def test_the_service_sends_by_name_and_says_why_it_cant(service, monkeypatch):
    from tests.test_plugins import AUTH

    sent = []

    def fake_send(target, text, project=None):
        if target == "nobody":
            raise OpsError("I don't see an active session called 'nobody'")
        sent.append((target, text, project))
        return Sent("Coordinator-a1", "Coordinator-a1")

    monkeypatch.setattr(ops, "send", fake_send)
    r = service.post("/api/v2/send", json={"target": "coordinator", "text": "done", "project": "alpha"}, headers=AUTH)
    assert r.status_code == 200 and r.json() == {"name": "Coordinator-a1", "tmux": "Coordinator-a1"}
    assert sent == [("coordinator", "done", "alpha")]
    r = service.post("/api/v2/send", json={"target": "nobody", "text": "done"}, headers=AUTH)
    assert r.status_code == 409
    assert r.json()["detail"] == {"message": "I don't see an active session called 'nobody'", "matches": []}
    assert service.post("/api/v2/send", json={"target": "coordinator", "text": "done"}).status_code == 401


def _send_args(**fields):
    import argparse

    return argparse.Namespace(**{"target": "coordinator", "prompt": "done", "no_header": True, "project": "alpha", **fields})


def test_agentop_send_goes_through_the_service(monkeypatch, capsys):
    from agentop import cli

    asked = []
    monkeypatch.setattr(cli, "_service_api", lambda *a, **k: asked.append(a[1:]) or (200, {"name": "C-1", "tmux": "C-1"}))
    monkeypatch.setattr(ops, "send", lambda *a, **k: pytest.fail("typed directly while the service answers"))
    cli.cmd_send(_send_args())
    assert asked == [("POST", "/send", {"target": "coordinator", "text": "done", "project": "alpha"})]
    assert "Sent to C-1." in capsys.readouterr().out


def test_agentop_send_types_directly_when_no_service_answers(monkeypatch, capsys):
    from agentop import cli

    monkeypatch.setattr(cli, "_service_api", lambda *a, **k: None)
    monkeypatch.setattr(ops, "send", lambda target, text, project=None: Sent("C-1", "C-1"))
    cli.cmd_send(_send_args())
    assert "Sent to C-1." in capsys.readouterr().out


def test_agentop_send_reports_the_services_refusal(monkeypatch, capsys):
    from agentop import cli

    detail = {"message": "Several active sessions are called 'dev': dev-1, dev-2", "matches": ["dev-1", "dev-2"]}
    monkeypatch.setattr(cli, "_service_api", lambda *a, **k: (409, {"detail": detail}))
    with pytest.raises(SystemExit):
        cli.cmd_send(_send_args())
    err = capsys.readouterr().err
    assert "Several active sessions" in err and "agentop list" not in err
