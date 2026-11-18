"""Claude's live session meta: from its sessions/<pid>.json and one read of the transcript."""

import json

from agentop.agentclis.claude import ClaudeAgentCli


def write_session(home, pid, **meta):
    folder = home / ".claude" / "sessions"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{pid}.json").write_text(json.dumps(meta))


def write_transcript(home, cwd, session_id, records):
    folder = home / ".claude" / "projects" / cwd.replace("/", "-")
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{session_id}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in records))


def usage(msg_id, tokens):
    return {"type": "assistant", "message": {"id": msg_id, "usage": {"input_tokens": tokens, "output_tokens": 1}}}


def test_meta_has_the_id_the_latest_title_and_usage_counted_once(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    write_session(tmp_path, 42, sessionId="s1", bridgeSessionId="session_abcdefgh", status="idle")
    write_transcript(
        tmp_path,
        "/w/proj",
        "s1",
        [
            {"type": "ai-title", "aiTitle": "First"},
            usage("m1", 10),
            usage("m1", 10),  # the same message in a second record
            usage("m2", 5),
            {"type": "ai-title", "aiTitle": "Second"},
        ],
    )
    meta = ClaudeAgentCli().get_session_meta(42, "/w/proj")
    assert meta["session_id"] == "s1"
    assert meta["ai_title"] == "Second"
    assert meta["token_usage"]["input_tokens"] == 15
    assert meta["token_usage"]["output_tokens"] == 2
    assert meta["bridge_url"] == "https://claude.ai/code/session_abcdefgh"
    assert meta["claude_status"] == "idle"


def test_no_session_file_means_no_meta(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert ClaudeAgentCli().get_session_meta(42, "/w/proj") == {}


def test_a_missing_transcript_still_gives_the_session_id(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    write_session(tmp_path, 42, sessionId="s1")
    assert ClaudeAgentCli().get_session_meta(42, "/w/proj") == {"session_id": "s1"}
