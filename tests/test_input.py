"""The Input bar plugin: prompts and keys reach only a running session agentop started."""

import shutil

import pytest
from fastapi.testclient import TestClient

from agentop import agentclis
from agentop.agentclis.base import AgentCli
from agentop.conversation import Message
from agentop.plugin_manifest import read_manifest
from agentop.plugins.input import reception
from agentop.service.app import create_app
from agentop.service.config import Settings
from agentop.service.identity import Identity
from agentop.service.plugins import DEFAULT_ROOT, PluginHost
from agentop.tmux import CapturePane, Session
from tests.conftest import signed_in
from tests.test_plugins import AUTH, TOKEN, QuietStore


class RecordingAgent(AgentCli):
    name = "recording"

    def __init__(self, calls):
        self.calls = calls

    def send_text(self, session, text):
        self.calls.append(("text", session, text))


def row(session_id, **fields):
    return {
        "id": session_id,
        "tool": "recording",
        "status": "running",
        "managed": True,
        "owner": None,
        "tmux": {"session": f"tmux-{session_id}", "clients": 0},
        **fields,
    }


@pytest.fixture
def calls(monkeypatch):
    calls = []
    monkeypatch.setattr(agentclis, "AGENTS", [*agentclis.AGENTS, RecordingAgent(calls)])
    monkeypatch.setattr(Session, "send_keys", staticmethod(lambda name, *keys: calls.append(("keys", name, *keys))))
    monkeypatch.setattr(Session, "leave_mode", staticmethod(lambda name: calls.append(("leave", name))))
    return calls


@pytest.fixture
def client(tmp_path, calls):
    root = tmp_path / "plugins"
    shutil.copytree(DEFAULT_ROOT / "input", root / "input")
    store = QuietStore()
    host = PluginHost(store, roots=[root], data_dir=tmp_path / "home")
    app = create_app(Settings(), Identity("service-token", "iid", "test"), store, host, clients=signed_in(TOKEN))
    with TestClient(app) as test_client:
        assert test_client.patch("/api/v2/plugins/input", json={"enabled": True}, headers=AUTH).status_code == 200
        store.set_rows(
            {
                "one": row("one"),
                "outside": row("outside", managed=False),
                "dialogue": row("dialogue", readonly=True),
                "gone": row("gone", status="exited", tmux=None),
            }
        )
        yield test_client


def test_on_by_default_and_fills_the_terminal_input_slot():
    manifest = read_manifest(DEFAULT_ROOT / "input")
    assert manifest.default_enabled is True
    assert manifest.ui == ("terminal-input",)


def test_a_prompt_is_typed_the_way_the_tool_takes_a_paste(client, calls):
    r = client.post("/api/v2/plugins/input/sessions/one/prompt", json={"text": "line one\nline two"}, headers=AUTH)
    assert r.status_code == 200
    assert calls == [("leave", "tmux-one"), ("text", "tmux-one", "line one\nline two")]


def test_an_empty_prompt_presses_enter(client, calls):
    assert client.post("/api/v2/plugins/input/sessions/one/prompt", json={"text": "  "}, headers=AUTH).status_code == 200
    assert calls == [("leave", "tmux-one"), ("keys", "tmux-one", "Enter")]


def test_keys_come_from_a_fixed_set(client, calls):
    r = client.post("/api/v2/plugins/input/sessions/one/keys", json={"keys": ["escape", "3", "shift-tab"]}, headers=AUTH)
    assert r.status_code == 200
    assert calls == [("leave", "tmux-one"), ("keys", "tmux-one", "Escape", "3", "BTab")]
    r = client.post("/api/v2/plugins/input/sessions/one/keys", json={"keys": ["kill-server"]}, headers=AUTH)
    assert r.status_code == 422
    assert len(calls) == 2


@pytest.mark.parametrize("session, status", [("nope", 404), ("outside", 409), ("dialogue", 409), ("gone", 409)])
def test_only_a_running_session_agentop_started(client, calls, session, status):
    assert client.post(f"/api/v2/plugins/input/sessions/{session}/prompt", json={"text": "hi"}, headers=AUTH).status_code == status
    assert client.post(f"/api/v2/plugins/input/sessions/{session}/keys", json={"keys": ["enter"]}, headers=AUTH).status_code == status
    assert calls == []


def test_needs_a_token(client, calls):
    assert client.post("/api/v2/plugins/input/sessions/one/prompt", json={"text": "hi"}).status_code == 401
    assert calls == []


def test_claude_gets_a_bracketed_paste_so_line_breaks_stay_in_the_prompt(monkeypatch):
    # A plain tmux paste turns each line break into Enter, and Claude sends a short paste's lines as separate prompts.
    calls = []
    monkeypatch.setattr(Session, "paste_text", staticmethod(lambda name, text: calls.append(("plain", text))))
    monkeypatch.setattr(Session, "paste_text_bracketed", staticmethod(lambda name, text: calls.append(("bracketed", text))))
    monkeypatch.setattr(Session, "send_keys", staticmethod(lambda name, *keys: calls.append(("keys", *keys))))
    agentclis.get_agent("claude").send_text("t", "line one\nline two")
    assert calls == [("bracketed", "line one\nline two"), ("keys", "Enter")]


# Reception: the check before a prompt goes in, and the clear the owner may choose.


def test_short_prompts_commands_and_empty_text_skip_the_model():
    assert reception.quick_pass("please continue") == "short"
    assert reception.quick_pass("/clear") == "a command"
    assert reception.quick_pass("  ") == "empty"
    assert reception.quick_pass("Please build a reception gate for the input bar, with a switch") is None
    assert reception.quick_pass("ok\nand also this") is None


def test_the_excerpt_keeps_the_first_prompt_and_the_latest_messages():
    messages = [Message("r", "user", "the first goal")]
    messages += [Message("r", "user" if i % 2 else "assistant", f"message {i} " + "x" * 3000) for i in range(40)]
    messages.append(Message("r", "tool", "tool output is left out"))
    text, prompts = reception.conversation_excerpt(messages)
    assert text.startswith("[owner] the first goal")
    assert "earlier messages left out" in text
    assert "message 39" in text and "tool output" not in text
    assert prompts == 21
    assert len(text) < reception.HEAD_CHARS + reception.TAIL_CHARS + 200


def test_the_answer_must_carry_a_verdict():
    assert reception.parse('Sure: {"current_work": "a release", "verdict": "Clear", "reason": "new topic"}') == {
        "verdict": "clear", "reason": "new topic", "work": "a release", "message": ""
    }
    with pytest.raises(ValueError):
        reception.parse('{"verdict": "maybe"}')
    with pytest.raises(ValueError):
        reception.parse("no json")


def test_state_gives_the_objective_and_next(tmp_path):
    (tmp_path / "STATE.md").write_text("# State\n\n## Objective\n\nShip it.\n\n## Decisions\n\nLots.\n\n## Next\n\nTest it.\n")
    assert reception.state_excerpt(tmp_path) == "Objective: Ship it. Next: Test it."
    assert reception.state_excerpt(tmp_path / "none") == ""


def test_on_by_default_for_all_sessions(monkeypatch):
    monkeypatch.setattr(reception.projects, "get", lambda name: {"owner": "projects" if name == "p" else "dialogue"})
    coordinator = {"id": "c", "project": "p", "display_name": "Coordinator"}
    assert reception.enabled(None, coordinator)
    assert reception.enabled(None, {"id": "d", "project": "p", "display_name": "dev"})
    assert reception.enabled(None, {"id": "c", "project": "talk", "display_name": "Coordinator"})
    saved = reception.set_enabled(None, coordinator, False)
    assert saved == {"reception": {"p/Coordinator": False}}
    assert not reception.enabled(saved, coordinator)
    assert reception.set_enabled(saved, coordinator, True) == {"reception": {}}


def test_a_check_needs_enough_context_before_asking(monkeypatch, tmp_path):
    asked = []
    row = {"id": "s", "tool": "claude", "session_id": "x", "display_name": "dev"}
    long = "Please write the deployment guide for the new service, with the rollback steps"
    monkeypatch.setattr(reception.transcripts, "read_conversation", lambda tool, sid: [Message("r", "user", "hi")])
    result = reception.check(row, long, tmp_path, ask=lambda p, s: asked.append(p) or "")
    assert (result["verdict"], result["reason"], asked) == ("pass", "little context yet", [])

    said = [Message("r", "user", f"fix the login bug, step {i}") for i in range(4)]
    monkeypatch.setattr(reception.transcripts, "read_conversation", lambda tool, sid: said)
    result = reception.check(row, long, tmp_path, ask=lambda p, s: asked.append(p) or '{"verdict": "clear", "reason": "new work"}')
    assert (result["verdict"], result["reason"]) == ("clear", "new work")
    assert "<new_message>" in asked[0] and "fix the login bug" in asked[0]
    failed = reception.check(row, long, tmp_path, ask=lambda p, s: "no idea")
    assert failed["verdict"] == "pass" and failed["reason"].startswith("the check failed")


def test_a_cleared_coordinator_is_told_to_read_its_notes(monkeypatch, tmp_path):
    monkeypatch.setattr(reception.projects, "get", lambda name: {"owner": "projects"})
    for name in ("AGENTS.md", "STATE.md"):
        (tmp_path / name).write_text("x")
    row = {"id": "c", "project": "p", "display_name": "Coordinator", "cwd": str(tmp_path)}
    assert reception.resume_note(row).startswith(
        "[agentop] Your context was cleared before this message. Read AGENTS.md, run agentop coordinator status"
    )
    assert reception.resume_note({**row, "display_name": "dev"}) == ""


def test_the_check_route_passes_while_reception_is_off(client, calls):
    client.patch("/api/v2/plugins/input/sessions/one/reception", json={"enabled": False}, headers=AUTH)
    r = client.post("/api/v2/plugins/input/sessions/one/check", json={"text": "a long prompt about something else entirely"}, headers=AUTH)
    assert r.status_code == 200
    assert r.json()["verdict"] == "pass"
    assert client.get("/api/v2/plugins/input/sessions/one/reception", headers=AUTH).json() == {"enabled": False, "default": True}
    r = client.patch("/api/v2/plugins/input/sessions/one/reception", json={"enabled": True}, headers=AUTH)
    assert r.json()["enabled"] is True
    assert client.post("/api/v2/plugins/input/sessions/nope/check", json={"text": "x"}, headers=AUTH).status_code == 404


def test_clear_and_send_clears_first_then_types_the_prompt(client, calls, monkeypatch):
    monkeypatch.setattr(CapturePane, "screen", staticmethod(lambda name: "same"))
    r = client.post(
        "/api/v2/plugins/input/sessions/one/prompt", json={"text": "new work", "clear": True, "check": "abc"}, headers=AUTH
    )
    assert r.status_code == 200
    assert [c for c in calls if c[0] == "text"] == [("text", "tmux-one", "/clear"), ("text", "tmux-one", "new work")]
