"""Transcript listing and the File viewer plugin: only the session's own files can be read."""

import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agentop import agentclis
from agentop.agentcli import AgentCli
from agentop.service import files, transcripts
from agentop.service.app import create_app
from agentop.service.config import Settings
from agentop.service.identity import Identity
from agentop.service.plugins import DEFAULT_ROOT, PluginHost
from tests.test_plugins import AUTH, TOKEN, QuietStore


class FakeAgent(AgentCli):
    name = "fake"

    def __init__(self, base: Path):
        self.base = base

    def transcript_files(self, session_id):
        log = self.base / f"{session_id}.jsonl"
        if not log.exists():
            return None, []
        extra = sorted((self.base / session_id).rglob("*")) if (self.base / session_id).is_dir() else []
        # A file outside the base is never listed, whatever the tool says.
        return self.base, [log, *[p for p in extra if p.is_file()], self.base.parent / "secret.txt"]


@pytest.fixture
def agent(tmp_path, monkeypatch):
    base = tmp_path / "logs"
    (base / "s1" / "subagents").mkdir(parents=True)
    (base / "s1.jsonl").write_text('{"type": "user"}\n')
    (base / "s1" / "subagents" / "a.jsonl").write_text("{}\n")
    (tmp_path / "secret.txt").write_text("no")
    fake = FakeAgent(base)
    monkeypatch.setattr(agentclis, "AGENTS", [*agentclis.AGENTS, fake])
    return fake


def test_lists_the_log_and_the_files_beside_it(agent):
    listing = transcripts.list_transcripts("fake", "s1")
    assert listing["root"] == str(agent.base)
    assert [f["path"] for f in listing["files"]] == ["s1.jsonl", "s1/subagents/a.jsonl"]


@pytest.mark.parametrize("tool, session_id", [(None, "s1"), ("fake", None), ("nope", "s1"), ("fake", "../s1"), ("fake", "*")])
def test_nothing_without_a_known_tool_and_a_plain_session_id(agent, tool, session_id):
    assert transcripts.list_transcripts(tool, session_id) == {"root": None, "files": [], "truncated": False}


def test_only_listed_files_resolve(agent):
    assert transcripts.resolve_transcript("fake", "s1", "s1/subagents/a.jsonl").name == "a.jsonl"
    for path in ("../secret.txt", "s1", "other.jsonl"):
        with pytest.raises(FileNotFoundError):
            transcripts.resolve_transcript("fake", "s1", path)


def test_changes_report_where_the_directory_sits_in_the_repository(tmp_path):
    import subprocess

    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "app").mkdir()
    assert files.git_changes(str(tmp_path))["prefix"] == ""
    assert files.git_changes(str(tmp_path / "app"))["prefix"] == "app/"


@pytest.fixture
def client(tmp_path, agent):
    root = tmp_path / "plugins"
    shutil.copytree(DEFAULT_ROOT / "viewer", root / "viewer")
    work = tmp_path / "work"
    (work / "src").mkdir(parents=True)
    (work / "src" / "a.py").write_text("print(1)\n")
    (work / "blob.bin").write_bytes(b"\0\1\2")
    (tmp_path / "outside.txt").write_text("no")
    store = QuietStore()
    host = PluginHost(store, root=root, data_dir=tmp_path / "home")
    app = create_app(Settings(), Identity(TOKEN, "iid", "test"), store, host)
    with TestClient(app) as test_client:
        store.set_rows({"one": {"id": "one", "tool": "fake", "session_id": "s1", "cwd": str(work), "status": "running"}})
        yield test_client


def read(client, **params):
    return client.get("/api/v2/plugins/viewer/file", params={"session": "one", **params}, headers=AUTH)


def test_core_lists_a_sessions_transcripts(client):
    response = client.get("/api/v2/sessions/one/transcripts", headers=AUTH)
    assert response.status_code == 200
    assert [f["path"] for f in response.json()["files"]] == ["s1.jsonl", "s1/subagents/a.jsonl"]


def test_reads_a_file_in_the_working_directory(client):
    body = read(client, path="src/a.py").json()
    assert body["text"] == "print(1)\n"
    assert body["binary"] is False and body["truncated"] is False


def test_reads_a_transcript(client):
    assert read(client, path="s1.jsonl", source="transcript").json()["text"] == '{"type": "user"}\n'


def test_binary_files_have_no_text(client):
    body = read(client, path="blob.bin").json()
    assert body["binary"] is True and body["text"] == ""


@pytest.mark.parametrize(
    "params, status",
    [
        ({"path": "../outside.txt"}, 403),
        ({"path": "missing.txt"}, 404),
        ({"path": "src"}, 404),
        ({"path": "../secret.txt", "source": "transcript"}, 404),
        ({"path": "x", "source": "elsewhere"}, 422),
        ({"path": "src/a.py", "session": "nobody"}, 404),
    ],
)
def test_refuses_what_is_not_the_sessions(client, params, status):
    assert read(client, **params).status_code == status


def test_long_files_end_on_a_whole_line(tmp_path):
    from agentop.plugins.viewer import read_text

    path = tmp_path / "long.txt"
    path.write_text("".join(f"line {i}\n" for i in range(1000)))
    result = read_text(path, max_bytes=100)
    assert result["truncated"] is True
    assert result["text"].endswith("\n") and len(result["text"]) <= 100
