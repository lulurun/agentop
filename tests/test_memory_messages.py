"""The messages table: prompts and replies read from the archive, nothing else."""

import json
import sqlite3
from datetime import datetime, timezone

import pytest
import zstandard

from agentop.plugins.memory.messages import Messages


def lines(*records):
    return "".join(json.dumps(r) + "\n" for r in records)


def claude(kind, uuid, content, **extra):
    return {
        "type": kind,
        "uuid": uuid,
        "sessionId": "s1",
        "cwd": "/work/proj",
        "timestamp": "2026-11-01T10:00:00Z",
        "message": {"role": kind, "content": content},
        **extra,
    }


CLAUDE = lines(
    {"type": "mode", "mode": "normal"},
    claude("user", "u0", "<local-command-caveat>Caveat: ...</local-command-caveat>", isMeta=True),
    claude("user", "u1", "Who sent me the atama link?<system-reminder>injected</system-reminder>"),
    claude("assistant", "a1", [{"type": "thinking", "thinking": "hmm"}]),
    claude("assistant", "a2", [{"type": "tool_use", "id": "t1", "name": "Bash", "input": {}}]),
    claude("user", "u2", [{"type": "tool_result", "tool_use_id": "t1", "content": "tool output"}]),
    claude("assistant", "a3", [{"type": "text", "text": "Yamada sent https://example.com/atama"}]),
    claude("user", "u3", "<command-name>/handoff</command-name>\n<command-message>handoff</command-message>\n<command-args>deploy eiger</command-args>"),
    claude("user", "u4", "a side question", isSidechain=True),
)

CODEX = lines(
    {"type": "session_meta", "timestamp": "2026-11-02T00:00:00Z", "payload": {"id": "c1", "cwd": "/work/hotaka"}},
    {"type": "turn_context", "payload": {"turn_id": "t1", "cwd": "/work/hotaka"}},
    {"type": "response_item", "timestamp": "2026-11-02T00:00:01Z", "payload": {
        "type": "message", "id": "m0", "role": "user",
        "content": [{"type": "input_text", "text": "# AGENTS.md instructions for /work\n\n<INSTRUCTIONS>..."}]}},
    {"type": "response_item", "payload": {
        "type": "message", "id": "m1", "role": "user",
        "content": [
            {"type": "input_text", "text": "<environment_context>\n<cwd>/work</cwd>\n</environment_context>"},
            {"type": "input_text", "text": "please restart influxdb"},
        ]}},
    {"type": "response_item", "payload": {"type": "message", "id": "m2", "role": "developer", "content": [{"type": "input_text", "text": "rules"}]}},
    {"type": "response_item", "payload": {"type": "reasoning", "id": "r1"}},
    {"type": "response_item", "payload": {"type": "message", "id": "m3", "role": "assistant", "content": [{"type": "output_text", "text": "Restarted."}]}},
)

ANTIGRAVITY = lines(
    {"step_index": 0, "source": "USER_EXPLICIT", "type": "USER_INPUT", "created_at": "2026-06-06T13:05:02Z",
     "content": "<USER_REQUEST>\nhow do I add an ssh key?\n</USER_REQUEST>\n<ADDITIONAL_METADATA>time</ADDITIONAL_METADATA>"},
    {"step_index": 1, "source": "MODEL", "type": "PLANNER_RESPONSE", "content": "Run ssh-keygen.", "tool_calls": []},
    {"step_index": 2, "source": "MODEL", "type": "RUN_COMMAND", "content": "command output"},
)


@pytest.fixture
def archive(tmp_path):
    root = tmp_path / "archive"
    (root / "claude" / "-work-proj" / "s1" / "subagents").mkdir(parents=True)
    (root / "claude" / "-work-proj" / "s1.jsonl").write_text(CLAUDE)
    (root / "claude" / "-work-proj" / "s1" / "subagents" / "agent-a.jsonl").write_text(lines(claude("user", "x1", "subagent prompt")))
    (root / "codex" / "sessions" / "2026" / "11").mkdir(parents=True)
    (root / "codex" / "sessions" / "2026" / "11" / "rollout-1.jsonl").write_text(CODEX)
    brain = root / "antigravity" / "g1" / ".system_generated" / "logs"
    brain.mkdir(parents=True)
    (brain / "transcript.jsonl").write_text(ANTIGRAVITY)
    return root


@pytest.fixture
def table(archive, tmp_path):
    return Messages(tmp_path / "messages.sqlite", archive)


def rows(table, *columns):
    with sqlite3.connect(table.path) as db:
        return db.execute(f"SELECT {', '.join(columns)} FROM messages ORDER BY tool, id").fetchall()


def test_only_prompts_and_replies_are_kept(table):
    # Not: meta records, injected reminders and instructions, thinking, tool calls
    # and output, developer messages, subagents, or tool steps.
    assert table.index()["added"] == 7
    assert rows(table, "tool", "role", "text") == [
        ("antigravity", "user", "how do I add an ssh key?"),
        ("antigravity", "assistant", "Run ssh-keygen."),
        ("claude", "user", "Who sent me the atama link?"),
        ("claude", "assistant", "Yamada sent https://example.com/atama"),
        ("claude", "user", "/handoff deploy eiger"),
        ("codex", "user", "please restart influxdb"),
        ("codex", "assistant", "Restarted."),
    ]


def test_each_message_knows_its_session_project_turn_and_time(table):
    table.index()
    by_text = {r[0]: r[1:] for r in rows(table, "text", "session", "project", "turn", "at", "file")}
    assert by_text["Yamada sent https://example.com/atama"] == (
        "s1", "/work/proj", "u1", datetime(2026, 11, 1, 10, tzinfo=timezone.utc).timestamp(), "claude/-work-proj/s1.jsonl",
    )
    assert by_text["Restarted."][:3] == ("c1", "/work/hotaka", "t1")
    assert by_text["Run ssh-keygen."][:3] == ("g1", None, "0")


def test_a_grown_file_is_read_from_where_it_stopped(table, archive):
    table.index()
    log = archive / "claude" / "-work-proj" / "s1.jsonl"
    with log.open("a") as f:
        f.write(lines(claude("user", "u5", "one more thing")))
    result = table.index()
    assert (result["files"], result["added"]) == (1, 1)
    claude_texts = [t for (tool, t) in rows(table, "tool", "text") if tool == "claude"]
    assert claude_texts[-1] == "one more thing"


def test_a_line_still_being_written_waits_for_the_next_run(table, archive):
    log = archive / "codex" / "sessions" / "2026" / "11" / "rollout-1.jsonl"
    whole = lines({"type": "response_item", "payload": {"type": "message", "id": "m4", "role": "user", "content": [{"type": "input_text", "text": "half written"}]}})
    log.write_text(CODEX + whole[:20])
    table.index()
    assert ("half written",) not in rows(table, "text")
    log.write_text(CODEX + whole)
    assert table.index()["added"] == 1
    assert ("half written",) in rows(table, "text")


def test_a_record_seen_twice_is_stored_once(table, archive):
    # A rewritten transcript keeps its old copy beside it, and a resumed session repeats records.
    folder = archive / "claude" / "-work-proj"
    (folder / "s1.jsonl.1800000000").write_text(CLAUDE)
    (folder / "s2.jsonl").write_text(CLAUDE)
    assert table.index()["added"] == 7


def test_compressed_codex_transcripts_are_read(table, archive):
    folder = archive / "codex" / "sessions" / "2026" / "11"
    (folder / "rollout-1.jsonl").unlink()
    (folder / "rollout-1.jsonl.zst").write_bytes(zstandard.ZstdCompressor().compress(CODEX.encode()))
    table.index()
    assert ("please restart influxdb",) in rows(table, "text")
    assert table.index()["files"] == 0


def test_the_table_can_be_rebuilt_from_the_archive(table):
    table.index()
    before = rows(table, "tool", "record", "text")
    table.path.unlink()
    for extra in ("-wal", "-shm"):
        table.path.with_name(table.path.name + extra).unlink(missing_ok=True)
    table.index()
    assert rows(table, "tool", "record", "text") == before


def test_status_counts_messages_and_sessions(table):
    assert table.status() == {"messages": 0, "sessions": 0, "tools": {}}
    table.index()
    assert table.status()["tools"]["claude"] == {"messages": 3, "sessions": 1}
    assert table.status()["messages"] == 7
