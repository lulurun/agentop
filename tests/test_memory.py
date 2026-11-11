"""The Memory plugin's archive: a copy of every transcript, kept up to date and never deleted."""

import json
import os
import shutil
import time

import pytest
from fastapi.testclient import TestClient

from agentop.plugins.memory import archive as archive_mod
from agentop.plugins.memory.archive import Archive, Source, all_files, claude_files, codex_files
from agentop.service.app import create_app
from agentop.service.config import Settings
from agentop.service.identity import Identity
from agentop.service.plugins import DEFAULT_ROOT, PluginHost
from tests.test_plugins import AUTH, TOKEN, QuietStore


@pytest.fixture
def home(tmp_path):
    # The tools' default places, under a home of the test's own.
    claude = tmp_path / ".claude" / "projects"
    (claude / "-proj" / "s1" / "subagents").mkdir(parents=True)
    (claude / "-proj" / "memory").mkdir()
    (claude / "-proj" / "s1.jsonl").write_text('{"n": 1}\n')
    (claude / "-proj" / "s1" / "subagents" / "a.jsonl").write_text("{}\n")
    (claude / "-proj" / "memory" / "note.md").write_text("Claude's own notes")
    codex = tmp_path / ".codex"
    (codex / "sessions" / "2026").mkdir(parents=True)
    (codex / "sessions" / "2026" / "rollout-1.jsonl").write_text("{}\n")
    (codex / "auth.json").write_text("secret")
    return tmp_path


@pytest.fixture
def archive(home, tmp_path):
    return Archive(
        tmp_path / "memory",
        [Source("claude", home / ".claude" / "projects", claude_files), Source("codex", home / ".codex", codex_files)],
    )


def archived(archive):
    return sorted(str(p.relative_to(archive.files_dir)) for p in archive.files_dir.rglob("*") if p.is_file())


def touch_later(path):
    """Move the modification time on, as a later write would."""
    later = path.stat().st_mtime + 5
    os.utime(path, (later, later))


def test_first_sweep_copies_every_transcript_and_nothing_else(archive):
    result = archive.sweep()
    assert result["copied"] == 3
    assert archived(archive) == [
        "claude/-proj/s1.jsonl",
        "claude/-proj/s1/subagents/a.jsonl",
        "codex/sessions/2026/rollout-1.jsonl",
    ]


def test_an_unchanged_file_is_left_alone(archive):
    archive.sweep()
    assert archive.sweep()["unchanged"] == 3


def test_a_grown_file_gets_only_its_new_bytes(archive, home, monkeypatch):
    archive.sweep()
    log = home / ".claude" / "projects" / "-proj" / "s1.jsonl"
    with log.open("a") as f:
        f.write('{"n": 2}\n')
    touch_later(log)
    copied = []
    monkeypatch.setattr(archive_mod, "_copy", lambda *a: copied.append(a) or 0)
    assert archive.sweep()["appended"] == 1
    assert copied == []
    assert (archive.files_dir / "claude/-proj/s1.jsonl").read_text() == '{"n": 1}\n{"n": 2}\n'


def test_a_rewritten_file_keeps_the_old_copy(archive, home, monkeypatch):
    archive.sweep()
    log = home / ".claude" / "projects" / "-proj" / "s1.jsonl"
    log.write_text('{"x": 0}\n')
    touch_later(log)
    monkeypatch.setattr(time, "time", lambda: 1_800_000_000)
    assert archive.sweep()["replaced"] == 1
    folder = archive.files_dir / "claude" / "-proj"
    assert (folder / "s1.jsonl").read_text() == '{"x": 0}\n'
    assert (folder / "s1.jsonl.1800000000").read_text() == '{"n": 1}\n'


def test_a_file_the_tool_deletes_stays_in_the_archive(archive, home):
    archive.sweep()
    shutil.rmtree(home / ".claude" / "projects")
    archive.sweep()
    assert "claude/-proj/s1.jsonl" in archived(archive)
    assert archive.status()["tools"]["claude"]["files"] == 2


def test_state_survives_a_restart(archive, tmp_path):
    archive.sweep()
    again = Archive(archive.folder, archive.sources)
    assert again.sweep()["unchanged"] == 3


def test_a_missing_source_is_skipped(tmp_path):
    archive = Archive(tmp_path / "memory", [Source("antigravity", tmp_path / "nowhere", all_files)])
    assert archive.sweep()["copied"] == 0


def test_a_sweep_can_be_stopped(archive):
    result = archive.sweep(stop=lambda: True)
    assert result["copied"] == 0
    assert archive.sweep()["copied"] == 3


def test_status_counts_files_and_bytes(archive):
    archive.sweep()
    status = archive.status()
    assert status["files"] == 3
    assert status["tools"]["codex"] == {"files": 1, "bytes": 3}
    assert status["last_sweep"]["copied"] == 3


@pytest.fixture
def client(tmp_path, home, monkeypatch):
    root = tmp_path / "plugins"
    shutil.copytree(DEFAULT_ROOT / "memory", root / "memory")
    monkeypatch.setenv("AGENTOP_HOME", str(tmp_path / "state"))
    # The plugin loads its own copy of the module, so the sources are moved through HOME.
    monkeypatch.setenv("HOME", str(home))
    store = QuietStore()
    host = PluginHost(store, root=root, data_dir=tmp_path / "flags")
    app = create_app(Settings(), Identity(TOKEN, "iid", "test"), store, host)
    with TestClient(app) as test_client:
        yield test_client


def test_the_plugin_sweeps_at_start_and_on_request(client, tmp_path, home):
    deadline = time.time() + 5
    while time.time() < deadline:
        status = client.get("/api/v2/plugins/memory/status", headers=AUTH).json()
        if status["last_sweep"]:
            break
        time.sleep(0.05)
    assert status["files"] == 3
    assert status["folder"] == str(tmp_path / "flags" / "plugins" / "memory" / "archive")

    (home / ".codex" / "sessions" / "2026" / "rollout-2.jsonl").write_text("{}\n")
    status = client.post("/api/v2/plugins/memory/sweep", headers=AUTH).json()
    assert status["files"] == 4
    assert status["last_sweep"]["copied"] == 1


def test_the_plugin_searches_what_it_has_recorded(client, home):
    log = home / ".claude" / "projects" / "-proj" / "s1.jsonl"
    record = {"type": "user", "uuid": "u9", "sessionId": "s1", "message": {"role": "user", "content": "the atama link"}}
    log.write_text(json.dumps(record) + "\n")
    client.post("/api/v2/plugins/memory/sweep", headers=AUTH)
    hits = client.get("/api/v2/plugins/memory/search", params={"q": "atama"}, headers=AUTH).json()
    assert [h["snippet"] for h in hits] == ["the atama link"]
    messages = client.get("/api/v2/plugins/memory/conversation", params={"session": "s1"}, headers=AUTH).json()
    assert [m["text"] for m in messages] == ["the atama link"]
