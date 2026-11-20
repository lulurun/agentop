"""The Input bar plugin: prompts and keys reach only a running session agentop started."""

import shutil

import pytest
from fastapi.testclient import TestClient

from agentop import agentclis
from agentop.agentclis.base import AgentCli
from agentop.plugin_manifest import read_manifest
from agentop.service.app import create_app
from agentop.service.config import Settings
from agentop.service.identity import Identity
from agentop.service.plugins import DEFAULT_ROOT, PluginHost
from agentop.tmux import Session
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
