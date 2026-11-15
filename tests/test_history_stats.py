"""History stats: the prompts, replies, tokens and size of a past session's transcript, for each tool."""

from __future__ import annotations

import json

import pytest
import zstandard

from agentop.service import transcripts


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("AGENTOP_HOME", str(tmp_path / ".agentop"))
    transcripts._file_stats.cache_clear()
    return tmp_path


def jsonl(*records) -> str:
    return "".join(json.dumps(r) + "\n" for r in records)


def claude_turn(uuid, kind, text, msg_id=None, usage=None, **extra):
    message = {"role": kind, "content": [{"type": "text", "text": text}]}
    if msg_id:
        message.update(id=msg_id, usage=usage or {})
    return {"type": kind, "uuid": uuid, "message": message, **extra}


def test_claude_counts_the_main_conversation_and_every_files_tokens(home):
    sid = "11111111-2222-3333-4444-555555555555"
    project = home / ".claude/projects/-w"
    project.mkdir(parents=True)
    usage = {"input_tokens": 10, "output_tokens": 100, "cache_read_input_tokens": 1000, "cache_creation_input_tokens": 5}
    (project / f"{sid}.jsonl").write_text(
        jsonl(
            claude_turn("u1", "user", "fix the bug"),
            claude_turn("a1", "assistant", "Looking.", "msg_1", usage),
            # The same message, a second content block: its usage counts once.
            claude_turn("a2", "assistant", "Done.", "msg_1", usage),
            claude_turn("u2", "user", "thanks"),
            {"type": "summary"},
        )
        + "not json\n"
    )
    sub = project / sid / "subagents"
    sub.mkdir(parents=True)
    (sub / "agent-1.jsonl").write_text(
        jsonl(
            claude_turn("s1", "user", "search the code", isSidechain=True),
            claude_turn("s2", "assistant", "Found it.", "msg_2", {"input_tokens": 1, "output_tokens": 2}, isSidechain=True),
        )
    )
    (project / sid / "tool-results").mkdir()
    (project / sid / "tool-results" / "out.txt").write_text("x" * 50)

    stats = transcripts.transcript_stats("claude", sid)
    assert (stats["prompts"], stats["replies"]) == (2, 2)
    assert stats["token_usage"] == {
        "input_tokens": 11,
        "output_tokens": 102,
        "cache_read_input_tokens": 1000,
        "cache_creation_input_tokens": 5,
    }
    assert stats["files"] == 3
    assert stats["bytes"] == sum(f.stat().st_size for f in project.rglob("*") if f.is_file())
    assert not stats["archived"] and not stats["truncated"]


def test_codex_reads_a_compressed_rollout_and_takes_the_cache_out_of_input(home):
    thread = "01a18bcf-f7bc-7af3-91b5-74bcf3f51fca"
    folder = home / ".codex/sessions/2026/11/15"
    folder.mkdir(parents=True)

    def total(i, o, cached):
        info = {"total_token_usage": {"input_tokens": i, "cached_input_tokens": cached, "output_tokens": o}}
        return {"type": "event_msg", "payload": {"type": "token_count", "info": info}}

    data = jsonl(
        {"type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "hi"}]}},
        total(100, 10, 60),
        total(500, 40, 300),
    ).encode()
    path = folder / f"rollout-2026-11-15T10-00-00-{thread}.jsonl.zst"
    path.write_bytes(zstandard.ZstdCompressor().compress(data))

    stats = transcripts.transcript_stats("codex", thread)
    assert stats["token_usage"] == {
        "input_tokens": 200,
        "output_tokens": 40,
        "cache_read_input_tokens": 300,
        "cache_creation_input_tokens": 0,
    }
    assert stats["files"] == 1 and stats["bytes"] == path.stat().st_size


def test_antigravity_sums_each_steps_tokens_and_skips_its_artifacts(home):
    conv = "d27f8d20-bfdb-4cdc-b297-c4e21978d562"
    logs = home / ".gemini/antigravity-cli/brain" / conv / ".system_generated/logs"
    logs.mkdir(parents=True)
    (logs / "transcript.jsonl").write_text(
        jsonl(
            {"step_index": 0, "type": "USER_INPUT", "content": "<USER_REQUEST>\nmake it installable\n</USER_REQUEST>"},
            {"step_index": 1, "type": "PLANNER_RESPONSE", "content": "On it.", "input_tokens": 100, "cache_read_tokens": 0, "output_tokens": 30},
            {"step_index": 2, "type": "PLANNER_RESPONSE", "content": "Done.", "input_tokens": 20, "cache_read_tokens": 400, "output_tokens": 5},
        )
    )
    (logs.parents[1] / "plan.md").write_text("# Plan\n")

    stats = transcripts.transcript_stats("antigravity", conv)
    assert (stats["prompts"], stats["replies"]) == (1, 2)
    assert stats["token_usage"] == {
        "input_tokens": 120,
        "output_tokens": 35,
        "cache_read_input_tokens": 400,
        "cache_creation_input_tokens": 0,
    }
    assert stats["files"] == 2


def test_the_memory_archive_stands_in_for_deleted_transcripts(home):
    sid = "99999999-2222-3333-4444-555555555555"
    project = home / ".agentop/plugins/memory/archive/claude/-w"
    project.mkdir(parents=True)
    (project / f"{sid}.jsonl").write_text(jsonl(claude_turn("u1", "user", "hello")))
    stats = transcripts.transcript_stats("claude", sid)
    assert stats["archived"] and stats["prompts"] == 1 and stats["token_usage"] is None


def test_nothing_for_an_unknown_session_or_a_bad_id():
    for tool, sid in (("claude", "missing"), ("claude", "../etc"), ("nope", "x"), (None, None)):
        stats = transcripts.transcript_stats(tool, sid)
        assert (stats["files"], stats["bytes"], stats["prompts"], stats["token_usage"]) == (0, 0, 0, None)
