"""History: the past sessions agentop started, the active-session limit, and resuming."""

import time

import pytest

from agentop import history, registry
from agentop.service.config import Settings
from agentop.service.store import SessionStore, agent_fields
from test_service_live import run_service

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
        "dialogue-1": record(agent_session_id="D", owner="dialogue"),
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


def test_resume_name_is_the_name_and_when_it_was_last_used():
    last = time.mktime((2026, 11, 3, 9, 5, 0, 0, 0, -1))
    assert history.resume_name({"name": "fix-auth", "last_active": last}) == "fix-auth 2026-11-03 09:05"
    assert len(history.resume_name({"name": "x" * 80, "last_active": last})) == history.NAME_MAX


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


def test_resume_from_history_names_it_and_takes_it_out_of_history(fake_agents, used):
    used["AGENT-1"] = time.time() - 3600
    registry.upsert_session(
        "demo-1", record(agent_session_id="AGENT-1", display_name="demo", ended_at=time.time() - 3000)
    )
    with run_service() as service:
        body = service.http.get("/history").json()
        assert [i["id"] for i in body["items"]] == ["demo-1"]
        assert body["window_seconds"] == 30 * DAY

        response = service.http.post("/history/demo-1/resume")
        assert response.status_code == 202, response.text
        session = response.json()
        assert session["display_name"] == history.resume_name(body["items"][0])
        service.wait_for(session["id"], "running")
        assert service.http.get(f"/sessions/{session['id']}").json()["display_name"].startswith("demo 20")
        assert service.http.get("/history").json()["items"] == []

        assert service.http.post("/history/nope/resume").status_code == 404
