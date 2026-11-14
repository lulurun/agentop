"""Session summaries: which sessions are due, what the model reads, and finding a session by its summary."""

from __future__ import annotations

import json
import shutil
import subprocess
import time

import pytest
from fastapi.testclient import TestClient

from agentop import agentclis
from agentop.agentclis.base import AgentCli
from agentop.agentclis.claude import ClaudeAgentCli
from agentop.plugins.memory import summaries as sm
from agentop.plugins.memory.messages import Messages
from agentop.service.app import create_app
from agentop.service.config import Settings
from agentop.service.identity import Identity
from agentop.service.plugins import DEFAULT_ROOT, PluginHost
from tests.test_plugins import AUTH, TOKEN, QuietStore
from tests.conftest import signed_in

NOW = time.time()
OLD = NOW - 2 * 3600


@pytest.fixture
def table(tmp_path):
    return Messages(tmp_path / "messages.sqlite", tmp_path / "archive")


def say(table, session, *texts, at=OLD, tool="claude", project="/work/proj"):
    """Add messages to a session, alternating user and assistant, a second apart."""
    db = table._connect()
    with db:
        start = db.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        for i, text in enumerate(texts):
            db.execute(
                "INSERT INTO messages (tool, record, session, project, turn, at, role, text, file)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'f')",
                (tool, f"r{start + i}", session, project, "t", at + i, "user" if i % 2 == 0 else "assistant", text),
            )
    db.close()


def answer(title="Lunch plans", summary="Talked about where to eat.", keywords=("Sushi Dai", "Tsukiji")):
    return json.dumps({"title": title, "summary": summary, "keywords": list(keywords)})


def test_a_quiet_session_is_due_and_a_busy_one_is_not(table):
    say(table, "quiet", "hi", "hello")
    say(table, "busy", "hi", "hello", at=NOW - 60)
    say(table, "one", "just one")
    assert [d.session for d in sm.Summaries(table).due(NOW)] == ["quiet"]


def test_due_is_newest_first_and_limited(table):
    for i in range(5):
        say(table, f"s{i}", "a", "b", at=OLD - i * 100)
    assert [d.session for d in sm.Summaries(table).due(NOW, limit=2)] == ["s0", "s1"]


def test_a_summarized_session_is_due_again_once_it_has_grown_by_half(table):
    summaries = sm.Summaries(table)
    say(table, "s", *["m"] * 20)
    summaries.summarize(summaries.due(NOW)[0], lambda p, s: answer(), "test")
    assert summaries.due(NOW) == []
    say(table, "s", *["m"] * 9, at=OLD + 100)
    assert summaries.due(NOW) == []
    say(table, "s", "m", at=OLD + 200)
    assert [d.messages for d in summaries.due(NOW)] == [30]


def test_a_failure_waits_before_it_is_tried_again(table, monkeypatch):
    summaries = sm.Summaries(table)
    say(table, "s", "a", "b")

    def broken(prompt, system):
        raise RuntimeError("signed out")

    result = summaries.run(broken, "test")
    assert result["made"] == 0 and "signed out" in result["errors"][0]
    assert summaries.due() == []
    monkeypatch.setattr(sm, "RETRY_SECONDS", -1)
    assert [d.session for d in summaries.due()] == ["s"]


def test_two_failures_end_the_run(table):
    for i in range(4):
        say(table, f"s{i}", "a", "b")
    calls = []

    def broken(prompt, system):
        calls.append(prompt)
        raise RuntimeError("over quota")

    sm.Summaries(table).run(broken, "test")
    assert len(calls) == 2


def test_the_model_reads_the_conversation_with_long_messages_cut(table):
    say(table, "s", "Who sent the atama link?", "x" * 5000)
    seen = {}

    def ask(prompt, system):
        seen["prompt"], seen["system"] = prompt, system
        return answer()

    sm.Summaries(table).summarize(sm.Summaries(table).due(NOW)[0], ask, "test")
    assert "User: Who sent the atama link?" in seen["prompt"]
    assert "x" * sm.MESSAGE_CHARS + " […]" in seen["prompt"]
    assert "/work/proj" in seen["prompt"]
    assert "data, not instructions" in seen["system"]


def test_a_long_session_keeps_its_start_and_its_end(table, monkeypatch):
    monkeypatch.setattr(sm, "BUDGET_CHARS", 3000)
    say(table, "s", *[f"message {i:03d} " + "y" * 90 for i in range(100)])
    text, count = sm.Summaries(table).transcript("claude", "s")
    assert count == 100
    assert "message 000" in text and "message 099" in text
    assert "message 050" not in text
    assert "messages left out" in text
    assert len(text) < 3500


def test_a_session_is_found_by_its_summary_words_and_keywords(table):
    summaries = sm.Summaries(table)
    say(table, "lunch", "where should we go", "Go before 6am")
    say(table, "other", "fix the build", "fixed", at=OLD - 500)
    for due in summaries.due(NOW):
        if due.session == "lunch":
            summaries.summarize(
                due, lambda p, s: answer(summary="A friend's restaurant recommendation."), "claude haiku"
            )
        else:
            summaries.summarize(due, lambda p, s: answer("Build", "Fixed the build.", ["CI"]), "claude haiku")

    hits = summaries.search("restaurant recommend")
    assert [h["session"] for h in hits] == ["lunch"]
    assert hits[0]["title"] == "Lunch plans"
    assert hits[0]["keywords"] == ["Sushi Dai", "Tsukiji"]
    assert [h["matched"] for h in summaries.search("sushi")] == [["Sushi Dai"]]
    assert summaries.search("restaurant", tool="codex") == []
    assert summaries.search("restaurant", since=NOW) == []
    assert summaries.get(None, "lunch")["made_by"] == "claude haiku"


def test_a_new_summary_replaces_the_old_one_in_search(table):
    summaries = sm.Summaries(table)
    say(table, "s", *["m"] * 10)
    summaries.summarize(summaries.due(NOW)[0], lambda p, s: answer(summary="About kamakura."), "test")
    say(table, "s", *["m"] * 10, at=OLD + 100)
    summaries.summarize(summaries.due(NOW)[0], lambda p, s: answer(summary="About hotaka."), "test")
    assert summaries.search("kamakura") == []
    assert [h["summary"] for h in summaries.search("hotaka")] == ["About hotaka."]
    assert summaries.status()["summaries"] == 1


def test_an_unparsable_answer_is_asked_for_once_more(table):
    summaries = sm.Summaries(table)
    say(table, "s", "a", "b")
    answers = iter(['{"title": "T", "summary": "cut off', answer()])
    summaries.summarize(summaries.due(NOW)[0], lambda p, s: next(answers), "test")
    assert summaries.get(None, "s")["title"] == "Lunch plans"


@pytest.mark.parametrize(
    "text",
    [
        '{"title": "T", "summary": "S", "keywords": ["a", "A", "b c"]}',
        'Here you go:\n```json\n{"title": "T", "summary": "S", "keywords": "a, b c"}\n```',
    ],
)
def test_parse_takes_the_json_however_it_is_wrapped(text):
    parsed = sm.parse(text)
    assert parsed["title"] == "T" and parsed["summary"] == "S"
    assert parsed["keywords"].split("\x1f") == ["a", "b c"]


@pytest.mark.parametrize("text", ["no json here", '{"title": "", "summary": "S"}', "[1, 2]"])
def test_parse_refuses_an_unusable_answer(text):
    with pytest.raises(RuntimeError):
        sm.parse(text)


# ---------------------------------------------------------------- the tool side


def test_claude_print_mode_is_not_a_session():
    claude = ClaudeAgentCli()
    assert claude.matches("claude", "claude --dangerously-skip-permissions")
    assert not claude.matches("claude", "claude --print --model haiku")
    assert not claude.matches("claude", "/usr/bin/claude -p hello")


def test_claude_answer_is_the_result_line():
    claude = ClaudeAgentCli()
    out = 'hook says hi\n{"type": "result", "is_error": false, "result": " the answer "}\n'
    assert claude.ask_answer(out) == "the answer"
    with pytest.raises(RuntimeError, match="signed out"):
        claude.ask_answer('{"type": "result", "is_error": true, "result": "signed out"}')
    with pytest.raises(RuntimeError):
        claude.ask_answer("")


class Echo(AgentCli):
    """Answers with what it was given, through a real subprocess."""

    name = "echo"

    def ask_cmd(self, system):
        return ["sh", "-c", 'cat; echo " [$0]"', system]


class Sleepy(AgentCli):
    name = "sleepy"

    def ask_cmd(self, system):
        return ["sleep", "30"]


@pytest.fixture
def real_ask(monkeypatch):
    """Undo the guard against real models: these tools are shell commands."""
    monkeypatch.undo()


def test_ask_runs_the_command_with_the_prompt_on_stdin(real_ask, tmp_path):
    assert Echo().ask("hello", "sys", tmp_path / "cwd") == "hello [sys]"
    assert (tmp_path / "cwd").is_dir()


def test_ask_is_stopped_and_timed_out(real_ask, tmp_path):
    started = time.monotonic()
    with pytest.raises(RuntimeError, match="stopped"):
        Sleepy().ask("x", "s", tmp_path, stop=lambda: True)
    with pytest.raises(RuntimeError, match="took over"):
        Sleepy().ask("x", "s", tmp_path, timeout=0.2)
    assert time.monotonic() - started < 5


def test_ask_reports_a_failing_command(real_ask, tmp_path):
    class Failing(AgentCli):
        name = "failing"

        def ask_cmd(self, system):
            return ["sh", "-c", "echo 'not signed in' >&2; exit 3"]

    with pytest.raises(RuntimeError, match=r"failed \(3\): not signed in"):
        Failing().ask("x", "s", tmp_path)


def test_a_tool_without_one_shot_prompts_says_so(real_ask, tmp_path):
    with pytest.raises(RuntimeError, match="can't answer"):
        AgentCli().ask("x", "s", tmp_path)


# ---------------------------------------------------------------- the plugin


class Writer(ClaudeAgentCli):
    """Claude, answering every summary request the same way."""

    ask_model = "fake"
    executable = "/bin/true"

    def ask_cmd(self, system):
        return ["true"]

    def ask(self, prompt, system, cwd, timeout=180, stop=lambda: False):
        return answer(summary="A friend's restaurant recommendation.")


@pytest.fixture
def plugin_client(tmp_path, monkeypatch):
    home = tmp_path / "home"
    log = home / ".claude" / "projects" / "-proj" / "s1.jsonl"
    log.parent.mkdir(parents=True)
    records = [
        {"type": "user", "uuid": "u1", "sessionId": "s1", "timestamp": "2026-01-01T00:00:00Z",
         "message": {"role": "user", "content": "where should we eat"}},
        {"type": "assistant", "uuid": "a1", "sessionId": "s1", "timestamp": "2026-01-01T00:00:05Z",
         "message": {"role": "assistant", "content": [{"type": "text", "text": "Sushi Dai, before 6am"}]}},
    ]
    log.write_text("".join(json.dumps(r) + "\n" for r in records))
    root = tmp_path / "plugins"
    shutil.copytree(DEFAULT_ROOT / "memory", root / "memory")
    monkeypatch.setenv("AGENTOP_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(agentclis, "AGENTS", [Writer()])

    store = QuietStore()
    host = PluginHost(store, root=root, data_dir=tmp_path / "flags")
    app = create_app(Settings(), Identity("service-token", "iid", "test"), store, host, clients=signed_in(TOKEN))
    with TestClient(app) as client:
        yield client


def wait_for(client, check, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = client.get("/api/v2/plugins/memory/status", headers=AUTH).json()
        if check(status):
            return status
        time.sleep(0.05)
    raise AssertionError(f"not reached: {status}")


def test_the_plugin_summarizes_after_its_sweep_and_finds_the_session(plugin_client):
    status = wait_for(plugin_client, lambda s: s["summaries"]["summaries"] == 1)
    assert status["summaries"]["enabled"] and status["summaries"]["by"] == "claude fake"
    hits = plugin_client.get("/api/v2/plugins/memory/search/sessions", params={"q": "restaurant"}, headers=AUTH).json()
    assert [h["session"] for h in hits] == ["s1"]
    summary = plugin_client.get("/api/v2/plugins/memory/summary", params={"session": "s1"}, headers=AUTH).json()
    assert summary["title"] == "Lunch plans"


def test_summaries_can_be_turned_off(plugin_client):
    status = plugin_client.patch("/api/v2/plugins/memory/summaries", json={"enabled": False}, headers=AUTH).json()
    assert status["summaries"]["enabled"] is False
    status = plugin_client.get("/api/v2/plugins/memory/status", headers=AUTH).json()
    assert status["summaries"]["enabled"] is False


def test_the_cli_shows_sessions_found_by_their_summary(table, tmp_path):
    folder = tmp_path / "state" / "plugins" / "memory"
    folder.mkdir(parents=True)
    table = Messages(folder / "messages.sqlite", folder / "archive")
    say(table, "lunch", "where should we go", "Sushi Dai, before 6am")
    summaries = sm.Summaries(table)
    summaries.summarize(summaries.due(NOW)[0], lambda p, s: answer(summary="A restaurant recommendation."), "t")
    env = {"AGENTOP_HOME": str(tmp_path / "state"), "PATH": "/usr/bin:/bin", "HOME": str(tmp_path)}
    import os
    import sys

    def recall(*args):
        return subprocess.run(
            [sys.executable, "-m", "agentop.cli", "recall", *args],
            env={**os.environ, **env},
            capture_output=True,
            text=True,
            check=True,
        ).stdout

    out = recall("restaurant")
    assert "Sessions about this" in out and "Lunch plans" in out
    assert "No messages with all of: restaurant" in out
    out = recall("--session", "lunch")
    assert out.startswith("=== Lunch plans")
    assert json.loads(recall("restaurant", "--json"))["sessions"][0]["session"] == "lunch"
