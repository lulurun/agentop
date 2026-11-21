"""History: the past sessions agentop started, the active-session limit, and resuming."""

import time

import pytest
from test_service_live import run_service

from agentop import history, registry
from agentop.service.config import Settings
from agentop.service.store import SessionStore, agent_fields

DAY = 86400.0
NOW = 1_800_000_000.0


def record(**fields):
    return {"managed": True, "tool": "fakeagent", "cwd": "/tmp", "created_at": NOW - 5 * DAY, **fields}


@pytest.fixture
def used(monkeypatch):
    """When each agent session's transcript was last written, by ID. Missing means it has none."""
    times: dict[str, float] = {}
    monkeypatch.setattr(history, "last_active", lambda tool, agent_id: times.get(agent_id))
    return times


def ids(items):
    return [i["id"] for i in items]


def test_only_sessions_used_within_the_window_newest_first(used):
    records = {
        "a-1": record(agent_session_id="A", display_name="alpha"),
        "b-2": record(agent_session_id="B"),
        "old-3": record(agent_session_id="OLD"),
        "gone-4": record(agent_session_id="GONE"),
    }
    used.update(A=NOW - 2 * DAY, B=NOW - 3600, OLD=NOW - 31 * DAY)
    items = history.list_history(records, set(), set(), 30 * DAY, now=NOW)
    assert ids(items) == ["b-2", "a-1"]
    assert items[1]["name"] == "alpha" and items[1]["session_id"] == "A"
    assert items[0]["name"] == "b"


def test_not_history_unless_agentop_started_it_and_it_has_ended(used):
    records = {
        "external": {"tool": "fakeagent", "agent_session_id": "E"},
        "dialogue-1": record(agent_session_id="D", readonly=True),
        "no-id-2": record(),
        "listed-3": record(agent_session_id="L"),
        "running-4": record(agent_session_id="R"),
        # Resumed: the old record's agent session is in use by a listed one.
        "before-5": record(agent_session_id="X"),
        "resumed-6": record(agent_session_id="X"),
    }
    used.update(E=NOW, D=NOW, L=NOW, R=NOW, X=NOW)
    items = history.list_history(records, {"listed-3", "resumed-6"}, {"R"}, 30 * DAY, now=NOW)
    assert items == []


def test_a_session_resumed_twice_is_listed_once_as_its_latest(used):
    records = {
        "first-1": record(agent_session_id="S", display_name="fix", created_at=NOW - 9 * DAY),
        "second-2": record(agent_session_id="S", display_name="fix 2026-01-01 10:00", created_at=NOW - DAY),
    }
    used["S"] = NOW - 3600
    items = history.list_history(records, set(), set(), 30 * DAY, now=NOW)
    assert ids(items) == ["second-2"]
    assert items[0]["name"] == "fix"


def test_the_agent_session_id_is_recorded_for_managed_sessions_only():
    rows = {
        "m-1": {"managed": True, "session_id": "S1", "ai_title": "Fix auth", "tool": "fakeagent"},
        "same-2": {"managed": True, "session_id": "S2"},
        "ext": {"managed": False, "session_id": "S3"},
    }
    records = {"m-1": {"tool": "fakeagent"}, "same-2": {"agent_session_id": "S2"}, "ext": {}}
    assert agent_fields(rows, records) == {"m-1": {"agent_session_id": "S1", "ai_title": "Fix auth"}}


def test_records_are_kept_at_least_as_long_as_the_window(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTOP_HOME", str(tmp_path))
    registry.upsert_session("old", {"managed": True, "ended_at": NOW - 40 * DAY})
    assert registry.mark_ended_and_prune(set(), now=NOW, retention=60 * DAY) == []
    assert registry.mark_ended_and_prune(set(), now=NOW, retention=1) == ["old"]


def test_removing_an_agent_session_drops_all_its_records(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTOP_HOME", str(tmp_path))
    registry.upsert_session("a", {"tool": "fakeagent", "agent_session_id": "S"})
    registry.upsert_session("b", {"tool": "fakeagent", "agent_session_id": "S"})
    registry.upsert_session("c", {"tool": "fakeagent", "agent_session_id": "T"})
    assert sorted(registry.remove_agent_session("fakeagent", "S")) == ["a", "b"]
    assert list(registry.load()["sessions"]) == ["c"]


def test_history_window_setting(monkeypatch):
    assert Settings().history_window_seconds == 30 * DAY
    assert Settings(history_window="7d").history_window_seconds == 7 * DAY
    with pytest.raises(ValueError):
        _ = Settings(history_window="none").history_window_seconds


# ---------------------------------------------------------------------- with the service


def test_no_session_starts_past_the_limit(fake_agents, monkeypatch):
    import test_service_live

    monkeypatch.setattr(
        test_service_live, "SessionStore", lambda tick_seconds: SessionStore(tick_seconds, max_active=1)
    )
    with run_service() as service:
        service.create(name="one")
        response = service.http.post("/sessions", json={"tool": "fakeagent", "cwd": "/tmp", "name": "two"})
        assert response.status_code == 409
        assert "Close one" in response.json()["detail"]
        body = service.http.get("/history").json()
        assert (body["active"], body["max_active"]) == (1, 1)


def test_resume_from_history_keeps_its_name_and_takes_it_out_of_history(fake_agents, used):
    used["AGENT-1"] = time.time() - 3600
    registry.upsert_session(
        "demo-1", record(agent_session_id="AGENT-1", display_name="demo", ended_at=time.time() - 3000)
    )
    with run_service() as service:
        body = service.http.get("/history").json()
        assert [i["id"] for i in body["items"]] == ["demo-1"]
        assert body["window_seconds"] == 30 * DAY
        stats = service.http.get("/history/demo-1/stats").json()
        assert (stats["files"], stats["prompts"], stats["token_usage"]) == (0, 0, None)
        assert service.http.get("/history/nope/stats").status_code == 404

        response = service.http.post("/history/demo-1/resume")
        assert response.status_code == 202, response.text
        session = response.json()
        assert session["display_name"] == "demo" and session["project"] == "default"
        service.wait_for(session["id"], "running")
        assert service.http.get(f"/sessions/{session['id']}").json()["display_name"] == "demo"
        assert service.http.get("/history").json()["items"] == []
        # The name is the running session's now, so a new session can't take it.
        taken = service.http.post("/sessions", json={"tool": "fakeagent", "cwd": "/tmp", "name": "demo"})
        assert taken.status_code == 409

        assert service.http.post("/history/nope/resume").status_code == 404


# ---------------------------------------------------------------------- telling agentop's sessions apart


def test_claude_is_started_with_a_session_id_agentop_picks():
    from agentop.agentclis.claude import ClaudeAgentCli

    agent = ClaudeAgentCli()
    session_id = agent.new_session_id()
    assert len(session_id) == 36 and agent.new_session_id() != session_id
    assert agent.start_cmd(session_id) == f"{agent.launch_cmd} --session-id {session_id}"


def test_claude_remote_control_can_be_disabled():
    from agentop.agentclis.claude import ClaudeAgentCli

    agent = ClaudeAgentCli()
    start = agent._build_cmd(agent.start_cmd("session-1"), {"remote_control": False})
    resume = agent._build_cmd(agent.get_resume_cmd("session-1"), {"remote_control": False})

    assert "--remote-control" not in start
    assert "--remote-control" not in resume
    assert "--dangerously-skip-permissions" in start
    assert "--dangerously-skip-permissions" in resume


def test_a_start_records_the_agent_and_any_id_it_was_given(fake_agents, tmp_path, monkeypatch):
    from agentop import ops

    agent = fake_agents["fakeagent"]
    name = ops.start("fakeagent", str(tmp_path), "plain").name
    record = registry.load()["sessions"][name]
    assert record["agent_pid"] and record["agent_started"] > NOW - 10 * 365 * DAY
    assert "agent_session_id" not in record

    monkeypatch.setattr(agent, "new_session_id", lambda: "picked-1")
    monkeypatch.setattr(agent, "start_cmd", lambda session_id: f"fakeagent {session_id}")
    name = ops.start("fakeagent", str(tmp_path), "given").name
    assert registry.load()["sessions"][name]["agent_session_id"] == "picked-1"

    name = ops.resume_session("fakeagent", "resumed-1", str(tmp_path), "again").name
    assert registry.load()["sessions"][name]["agent_session_id"] == "resumed-1"


def test_an_ended_session_without_an_id_gets_it_from_the_tools_records(monkeypatch):
    class Agent:
        def session_id_after(self, pid, started):
            return {(11, 100.0): "T-11"}.get((pid, started))

    monkeypatch.setattr(history, "get_agent", lambda tool: Agent() if tool == "codex" else None)
    records = {
        "ended-1": record(tool="codex", agent_pid=11, agent_started=100.0),
        "unknown-2": record(tool="codex", agent_pid=12, agent_started=100.0),
        "listed-3": record(tool="codex", agent_pid=11, agent_started=100.0),
        "known-4": record(tool="codex", agent_pid=11, agent_started=100.0, agent_session_id="K"),
        "old-5": {"managed": True, "ended_at": NOW},
    }
    assert history.resolve_agent_ids(records, {"listed-3"}) == {"ended-1": {"agent_session_id": "T-11"}}


def test_codex_finds_the_thread_of_a_process_that_has_exited(tmp_path, monkeypatch):
    import sqlite3

    from agentop.agentclis.codex import CodexAgentCli

    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".codex").mkdir()
    with sqlite3.connect(tmp_path / ".codex/logs_2.sqlite") as db:
        db.execute(
            "CREATE TABLE logs (id INTEGER PRIMARY KEY AUTOINCREMENT, ts INT, thread_id TEXT, process_uuid TEXT)"
        )
        db.execute("INSERT INTO logs (ts, thread_id, process_uuid) VALUES (90, 'EARLIER', 'pid:4242:a')")
        db.execute("INSERT INTO logs (ts, thread_id, process_uuid) VALUES (150, 'THREAD', 'pid:4242:b')")
    assert CodexAgentCli().session_id_after(4242, 100.0) == "THREAD"
    assert CodexAgentCli().session_id_after(4243, 100.0) is None


def test_antigravity_finds_the_conversation_in_the_log_of_its_run(tmp_path, monkeypatch):
    from datetime import datetime

    from agentop.agentclis import antigravity

    log_dir = tmp_path / ".gemini" / "antigravity-cli" / "log"
    log_dir.mkdir(parents=True)
    started = datetime(2026, 11, 15, 21, 56, 1).timestamp()
    conversation = "d27f8d20-bfdb-4cdc-b297-c4e21978d562"
    (log_dir / "cli-20261115_215601.log").write_text(
        "I1115 conversation_manager.go:1] Starting new conversation (agent=false)\n"
        f"I1115 conversation_manager.go:2] Created conversation {conversation}\n"
        "I1115 server.go:3] Full redraw completed (rerenderAll) for conversation  (epoch 0, items 1)\n"
    )
    agent = antigravity.AntigravityAgentCli(home=tmp_path)
    assert agent.session_id_after(1, started) == conversation
    assert agent.session_id_after(1, started + 3600) is None


def test_a_running_session_records_its_directory_and_process_for_later():
    import os

    import psutil

    rows = {"old-1": {"managed": True, "tool": "antigravity", "cwd": "/w", "pid": os.getpid()}}
    records = {"old-1": {"managed": True}}
    fields = agent_fields(rows, records)["old-1"]
    assert fields == {
        "tool": "antigravity",
        "cwd": "/w",
        "agent_pid": os.getpid(),
        "agent_started": psutil.Process(os.getpid()).create_time(),
    }
    records["old-1"].update(fields)
    assert agent_fields(rows, records) == {}


def test_a_past_session_is_found_by_its_name_for_its_files(monkeypatch):
    records = {"a-1": record(agent_session_id="A"), "dialogue-2": record(agent_session_id="D", readonly=True), "new-3": record()}
    monkeypatch.setattr(registry, "load", lambda: {"sessions": records})
    assert history.record("a-1") == {"id": "a-1", "tool": "fakeagent", "session_id": "A", "cwd": "/tmp"}
    assert [history.record(n) for n in ("dialogue-2", "new-3", "missing")] == [None, None, None]
