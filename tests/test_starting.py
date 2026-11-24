"""Sessions' start from a prompt: the probe's answer, the chooser, tidying, settings, and the launch route."""

import json
from pathlib import Path
import time

import pytest

from agentop import agentclis, registry, starting
from agentop.agentclis.antigravity import AntigravityAgentCli
from agentop.agentclis.claude import ClaudeAgentCli
from agentop.agentclis.codex import CodexAgentCli

INSTALLED = ["claude", "codex", "antigravity"]
ORDER = ["codex", "claude", "antigravity"]


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTOP_HOME", str(tmp_path / "agentop"))
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path


def answer(**fields):
    base = {"title": "Fix the login bug", "name": "fix-login", "kind": "code", "tier": "complex", "tool": None,
            "model": None, "cwd": "none", "cwd_sure": True, "reason": "a bug"}
    return "Here it is:\n" + json.dumps({**base, **fields})


def test_an_answer_is_checked_and_a_bad_field_falls_back(tmp_path):
    repo = tmp_path / "api"
    repo.mkdir()
    got = starting.parse(answer(cwd=str(repo), tier="huge", kind="poem", name="Fix Login!"), [], INSTALLED)
    assert got["cwd"] == str(repo) and got["tier"] == "standard" and got["kind"] == "code" and got["name"] == "fix-login"
    assert starting.parse(answer(cwd=str(tmp_path / "missing")), [], INSTALLED)["cwd"] is None
    assert starting.parse(answer(cwd="none"), [], INSTALLED)["cwd"] == "none"
    with pytest.raises(ValueError):
        starting.parse("no json here", [], INSTALLED)


def test_a_named_tool_or_model_is_kept_only_when_real():
    assert starting.parse(answer(tool="agy"), [], INSTALLED)["tool"] == "antigravity"
    got = starting.parse(answer(model="Opus 5.5"), [], INSTALLED)
    assert (got["tool"], got["model"]) == ("claude", "claude-opus-5-5")
    got = starting.parse(answer(tool="codex", model="Opus 5.5"), [], INSTALLED)
    assert (got["tool"], got["model"]) == ("codex", None)
    assert starting.parse(answer(tool="cursor"), [], INSTALLED)["tool"] is None
    assert starting.parse(answer(tool="codex"), [], ["claude"])["tool"] is None


def test_the_task_is_the_prompt_less_the_ask_for_a_session():
    asked = "please open a new session for dev agentop"
    assert starting.parse(answer(task=""), [], INSTALLED, asked)["task"] == ""
    assert starting.parse(answer(task="fix the login bug"), [], INSTALLED, "open codex to fix the login bug")["task"] == "fix the login bug"
    # No task, or one longer than the prompt, hands the prompt over as typed.
    assert starting.parse(answer(), [], INSTALLED, asked)["task"] == asked
    assert starting.parse(answer(task=asked + " and add tests"), [], INSTALLED, asked)["task"] == asked
    long = "x " * starting.TASK_CHARS
    assert starting.parse(answer(task="x"), [], INSTALLED, long)["task"] == long
    assert '"task": null' in starting.build_prompt(long, [], INSTALLED)


def quota(used):
    return [{"name": "5h", "used_percent": used, "resets_at": None}]


def test_the_chooser_takes_the_tier_on_the_tool_with_quota_left():
    full_codex = {"codex": quota(100.0), "claude": None, "antigravity": None}
    got = starting.choose("complex", INSTALLED, full_codex, ORDER)
    assert (got["tool"], got["model"]) == ("claude", "claude-opus-5-5")
    assert "codex at 100%" in got["why"] and got["why"].endswith("so claude")
    # Near ties go to the owner's order.
    got = starting.choose("light", INSTALLED, {"codex": quota(30.0), "claude": quota(25.0), "antigravity": None}, ORDER)
    assert (got["tool"], got["model"]) == ("codex", "gpt-6-luna")
    # A clear lead wins over the order.
    got = starting.choose("standard", INSTALLED, {"codex": quota(70.0), "claude": quota(10.0)}, ORDER)
    assert got["tool"] == "claude"


def test_a_named_tool_or_model_wins_over_the_chooser():
    seen = {"codex": quota(100.0)}
    assert starting.choose("complex", INSTALLED, seen, ORDER, "codex")["model"] == "gpt-6.2-sol"
    got = starting.choose("light", INSTALLED, seen, ORDER, "claude", "claude-opus-5-5")
    assert (got["tool"], got["model"], got["why"]) == ("claude", "claude-opus-5-5", "named in the prompt")


def test_a_probe_that_fails_still_gives_a_start(home, monkeypatch):
    monkeypatch.setattr(starting, "quotas", lambda installed: {})

    def broken(text, system):
        raise RuntimeError("agy took over 40s")

    got = starting.probe("please look at why the deploy script hangs on step three", INSTALLED, ask=broken)
    assert got["error"] == "agy took over 40s" and got["cwd"] is None and got["tier"] == "standard"
    assert got["title"].startswith("please look at why") and got["tool"] == "codex"
    assert got["task"] == "please look at why the deploy script hangs on step three"
    events = [json.loads(line) for line in (home / "agentop/projects/default/.project/starts.jsonl").read_text().splitlines()]
    assert events[-1]["event"] == "probe" and events[-1]["error"] == "agy took over 40s"


def test_a_task_without_files_runs_in_the_project_folder(home, monkeypatch):
    monkeypatch.setattr(starting, "quotas", lambda installed: {})
    got = starting.probe("what is the weather in Tokyo", INSTALLED, ask=lambda text, system: answer(tier="light"))
    assert got["cwd"] == str(home / "agentop/projects/default") and got["cwd_sure"]


def test_candidates_are_recent_folders_then_repositories(home):
    recent, old, repo = home / "recent", home / "old", home / "workspace" / "agentop"
    for folder in (recent, old, repo / ".git"):
        folder.mkdir(parents=True)
    (home / "workspace" / "notes").mkdir()
    now = time.time()
    registry.upsert_session("a-111111", {"project": "default", "cwd": str(recent), "created_at": now - 60})
    registry.upsert_session("b-222222", {"project": "default", "cwd": str(old), "created_at": now - 90 * 86400})
    assert starting.candidates(home, now) == [str(recent), str(repo)]


def row(name, last, status="running", project="default", counted=True):
    return {"id": name, "project": project, "managed": True, "counted": counted, "status": status,
            "created_at": last, "tmux": {"session": name}}


def test_tidying_closes_only_idle_default_sessions():
    now = 1_000_000.0
    rows = [row("old", 0), row("fresh", 0), row("coord", 0, project="big"), row("agent", 0, counted=False), row("boot", 0, status="starting")]
    activity = {"old": now - 25 * 3600, "fresh": now - 3600, "coord": now - 99 * 3600, "agent": now - 99 * 3600}
    assert [r["id"] for r in starting.idle_to_close(rows, activity, 24, now)] == ["old"]
    assert starting.idle_to_close(rows, activity, 0, now) == []


def test_room_is_made_from_the_session_idle_longest_if_idle_long_enough():
    now = 1_000_000.0
    rows = [row("a", 0), row("b", 0)]
    assert starting.room_to_make(rows, {"a": now - 700, "b": now - 3000}, now)["id"] == "b"
    assert starting.room_to_make(rows, {"a": now - 60, "b": now - 120}, now) is None


def test_settings_are_checked_and_kept(home):
    assert starting.settings()["probe_tool"] == "antigravity"
    saved = starting.save_settings({"probe_tool": "claude", "probe_model": "haiku 5.5", "tool_order": ["claude"], "idle_hours": 12})
    assert saved["probe_model"] == "claude-haiku-5-5" and saved["tool_order"][0] == "claude" and len(saved["tool_order"]) == 3
    assert starting.settings()["idle_hours"] == 12
    with pytest.raises(ValueError):
        starting.save_settings({"idle_hours": -1})


def test_recent_lists_launches_with_why_they_closed(home):
    starting.record({"event": "launched", "session": "fix-a1", "title": "Fix it", "tool": "codex"})
    starting.record({"event": "launched", "session": "ask-b2", "title": "Ask", "tool": "claude"})
    starting.record({"event": "closed", "session": "fix-a1", "why": "idle for 24h"})
    got = starting.recent()
    assert [r["session"] for r in got] == ["ask-b2", "fix-a1"] and got[1]["closed_why"] == "idle for 24h"


def test_codex_quota_comes_from_its_newest_rollout(tmp_path):
    day = tmp_path / ".codex/sessions/2026/11/24"
    day.mkdir(parents=True)
    future = time.time() + 3600
    limits = {"primary": {"used_percent": 40.0, "window_minutes": 300, "resets_at": future},
              "secondary": {"used_percent": 90.0, "window_minutes": 10080, "resets_at": 1}}
    lines = [{"type": "event_msg", "payload": {"type": "token_count", "rate_limits": limits}},
             {"type": "event_msg", "payload": {"type": "token_count", "rate_limits": {"limit_id": "premium", "primary": None}}}]
    (day / "rollout-x.jsonl").write_text("\n".join(json.dumps(line) for line in lines))
    windows = CodexAgentCli(home=tmp_path).quota()
    assert [(w.name, w.used_percent) for w in windows] == [("5h", 40.0), ("weekly", 0.0)]
    assert ClaudeAgentCli(home=tmp_path).quota() is None


def test_agy_answers_through_its_print_flag_and_its_conversation_goes(tmp_path, monkeypatch):
    agy = AntigravityAgentCli(home=tmp_path)
    brain = tmp_path / ".gemini/antigravity-cli/brain/0f0e7a6c-1111-4222-8333-444455556666"
    brain.mkdir(parents=True)
    out = json.dumps({"conversation_id": brain.name, "status": "SUCCESS", "response": '{"ok": true}\n'})
    assert agy.ask_answer(out) == '{"ok": true}' and not brain.exists()
    assert agy.matches("agy", "agy --model m --output-format json -p=hi") is False
    assert agy.matches("agy", "agy --dangerously-skip-permissions") is True


def test_launch_starts_with_the_prompt_and_records_it(fake_agents, monkeypatch):
    from tests.test_service_live import run_service

    monkeypatch.setattr(starting, "quotas", lambda installed: {})
    with run_service() as live:
        cwd = str(Path.home())
        r = live.http.post("/start/launch", json={"prompt": "hello there", "tool": "fakeagent", "cwd": cwd, "name": "Say Hello", "title": "Say hello"})
        assert r.status_code == 202, r.text
        created = r.json()
        assert created["display_name"] == "say-hello" and created["description"] == "Say hello"
        live.wait_for(created["id"], "running")
        recent = live.http.get("/start/recent").json()
        assert recent[0]["session"] == created["id"] and recent[0]["title"] == "Say hello"
        r = live.http.post("/start/launch", json={"prompt": "", "tool": "fakeagent", "cwd": cwd, "name": "Waits", "changed": ["prompt"]})
        assert r.status_code == 202, r.text
        live.wait_for(r.json()["id"], "running")
        tools = {t["name"]: t for t in live.http.get("/start/models").json()}
        assert tools["fakeagent"]["models"] == [] and "can_probe" in tools["fakeagent"]
