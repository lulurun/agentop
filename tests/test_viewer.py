"""Transcript listing and the File viewer plugin: only the session's own files can be read."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agentop import agentclis
from agentop.agentclis.base import AgentCli
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

    def transcript_files(self, session_id, root=None):
        base = root or self.base
        log = base / f"{session_id}.jsonl"
        if not log.exists():
            return None, []
        extra = (
            sorted((base / session_id).rglob("*"))
            if (base / session_id).is_dir()
            else []
        )
        # A file outside the base is never listed, whatever the tool says.
        return base, [
            log,
            *[p for p in extra if p.is_file()],
            base.parent / "secret.txt",
        ]


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


@pytest.mark.parametrize(
    "tool, session_id",
    [(None, "s1"), ("fake", None), ("nope", "s1"), ("fake", "../s1"), ("fake", "*")],
)
def test_nothing_without_a_known_tool_and_a_plain_session_id(agent, tool, session_id):
    assert transcripts.list_transcripts(tool, session_id) == {
        "root": None,
        "files": [],
        "truncated": False,
        "archived": False,
    }


def test_only_listed_files_resolve(agent):
    assert (
        transcripts.resolve_transcript("fake", "s1", "s1/subagents/a.jsonl").name
        == "a.jsonl"
    )
    for path in ("../secret.txt", "s1", "other.jsonl"):
        with pytest.raises(FileNotFoundError):
            transcripts.resolve_transcript("fake", "s1", path)


def test_a_transcript_the_tool_deleted_comes_from_the_archive(
    agent, tmp_path, monkeypatch
):
    archive = tmp_path / "archive"
    archive.mkdir()
    (archive / "gone.jsonl").write_text("{}\n")
    (archive / "s1.jsonl").write_text("the archive's copy\n")
    monkeypatch.setattr(transcripts, "archive_root", lambda tool: archive)
    listing = transcripts.list_transcripts("fake", "gone")
    assert (listing["root"], listing["archived"]) == (str(archive), True)
    assert [f["path"] for f in listing["files"]] == ["gone.jsonl"]
    assert (
        transcripts.resolve_transcript("fake", "gone", "gone.jsonl")
        == archive / "gone.jsonl"
    )
    # While the tool still has its own, that is the one listed.
    assert transcripts.list_transcripts("fake", "s1")["archived"] is False


def test_a_codex_rollout_is_found_by_name_once_compressed_or_moved(
    tmp_path, monkeypatch
):
    from agentop.agentclis.codex import CodexAgentCli

    codex = CodexAgentCli()
    monkeypatch.setattr(
        codex,
        "_thread_by_id",
        lambda _id: {"rollout_path": str(Path.home() / ".codex/sessions/r-t1.jsonl")},
    )
    folder = tmp_path / "archived_sessions" / "2026"
    folder.mkdir(parents=True)
    (folder / "rollout-2026-t1.jsonl.zst").write_bytes(b"z")
    base, found = codex.transcript_files("t1", root=tmp_path)
    assert (base, found) == (folder, [folder / "rollout-2026-t1.jsonl.zst"])
    assert codex.transcript_files("t2", root=tmp_path) == (None, [])


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
        store.set_rows(
            {
                "one": {
                    "id": "one",
                    "tool": "fake",
                    "session_id": "s1",
                    "cwd": str(work),
                    "status": "running",
                }
            }
        )
        yield test_client


def read(client, **params):
    return client.get(
        "/api/v2/plugins/viewer/file", params={"session": "one", **params}, headers=AUTH
    )


def test_core_lists_a_sessions_transcripts(client):
    response = client.get("/api/v2/sessions/one/transcripts", headers=AUTH)
    assert response.status_code == 200
    assert [f["path"] for f in response.json()["files"]] == [
        "s1.jsonl",
        "s1/subagents/a.jsonl",
    ]


def test_reads_a_file_in_the_working_directory(client):
    body = read(client, path="src/a.py").json()
    assert body["text"] == "print(1)\n"
    assert body["binary"] is False and body["truncated"] is False


def test_reads_a_transcript(client):
    assert (
        read(client, path="s1.jsonl", source="transcript").json()["text"]
        == '{"type": "user"}\n'
    )


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


def test_a_changed_file_reads_as_a_diff(client):
    work = Path(read(client, path="src/a.py").json()["absolute"]).parent.parent
    for args in (["init"], ["add", "."], ["-c", "user.name=t", "-c", "user.email=t@x", "commit", "-m", "x"]):
        subprocess.run(["git", "-C", str(work), *args], check=True, capture_output=True)
    (work / "src" / "a.py").write_text("print(2)\n")
    diff = lambda path: client.get("/api/v2/plugins/viewer/diff", params={"session": "one", "path": path}, headers=AUTH)
    body = diff("src/a.py").json()
    assert "-print(1)\n+print(2)\n" in body["text"]
    assert body["absolute"] == str(work / "src" / "a.py")
    assert diff("blob.bin").status_code == 404
    assert diff("../outside.txt").status_code == 403


PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 16


def image(client, **params):
    return client.get("/api/v2/plugins/viewer/image", params={"session": "one", **params}, headers=AUTH)


def test_an_image_comes_as_data_for_a_markdown_file(client):
    work = Path(read(client, path="src/a.py").json()["absolute"]).parent.parent
    (work / "docs").mkdir()
    (work / "docs" / "shot.PNG").write_bytes(PNG)
    body = image(client, path="docs/shot.PNG").json()
    assert body["type"] == "image/png" and body["size"] == len(PNG)
    import base64

    assert base64.b64decode(body["data"]) == PNG


@pytest.mark.parametrize(
    "params, status",
    [
        ({"path": "src/a.py"}, 415),
        ({"path": "../outside.png"}, 403),
        ({"path": "missing.png"}, 404),
        ({"path": "x.png", "session": "nobody"}, 404),
    ],
)
def test_only_the_sessions_images_are_served(client, params, status):
    assert image(client, **params).status_code == status


def test_a_large_image_is_refused(tmp_path):
    from agentop.plugins.viewer import read_image

    path = tmp_path / "big.png"
    path.write_bytes(PNG * 10)
    with pytest.raises(ValueError, match="larger than"):
        read_image(path, max_bytes=100)


def test_long_files_end_on_a_whole_line(tmp_path):
    from agentop.plugins.viewer import read_text

    path = tmp_path / "long.txt"
    path.write_text("".join(f"line {i}\n" for i in range(1000)))
    result = read_text(path, max_bytes=100)
    assert result["truncated"] is True
    assert result["text"].endswith("\n") and len(result["text"]) <= 100


@pytest.fixture
def claude_client(tmp_path, monkeypatch):
    from agentop.agentclis import claude as claude_mod

    projects = tmp_path / "projects"
    (projects / "-work").mkdir(parents=True)
    log = projects / "-work" / "c1.jsonl"
    record = {"sessionId": "c1", "cwd": "/work", "timestamp": "2026-11-01T10:00:00Z"}
    log.write_text(
        "\n".join(
            json.dumps(r)
            for r in [
                {
                    **record,
                    "type": "user",
                    "uuid": "u1",
                    "message": {"role": "user", "content": "who sent the link?"},
                },
                {
                    **record,
                    "type": "assistant",
                    "uuid": "a1",
                    "message": {
                        "content": [
                            {"type": "tool_use", "id": "t", "name": "Bash", "input": {}}
                        ]
                    },
                },
                {
                    **record,
                    "type": "assistant",
                    "uuid": "a2",
                    "message": {
                        "content": [
                            {"type": "text", "text": "Yamada, https://example.com"}
                        ]
                    },
                },
            ]
        )
        + "\n"
    )
    (projects / "-work" / "notes.txt").write_text("not a transcript")
    monkeypatch.setattr(claude_mod, "_PROJECTS_DIR", projects)
    root = tmp_path / "plugins"
    shutil.copytree(DEFAULT_ROOT / "viewer", root / "viewer")
    store = QuietStore()
    host = PluginHost(store, root=root, data_dir=tmp_path / "home")
    app = create_app(Settings(), Identity(TOKEN, "iid", "test"), store, host)
    with TestClient(app) as test_client:
        store.set_rows(
            {
                "one": {
                    "id": "one",
                    "tool": "claude",
                    "session_id": "c1",
                    "cwd": str(tmp_path),
                    "status": "running",
                }
            }
        )
        yield test_client


def test_a_transcript_reads_as_a_conversation(claude_client):
    file = claude_client.get(
        "/api/v2/plugins/viewer/file",
        params={"session": "one", "path": "c1.jsonl", "source": "transcript"},
        headers=AUTH,
    )
    assert file.json()["conversation"] is True
    response = claude_client.get(
        "/api/v2/plugins/viewer/conversation",
        params={"session": "one", "path": "c1.jsonl"},
        headers=AUTH,
    )
    body = response.json()
    assert body["total"] == 2
    assert [(m["role"], m["text"]) for m in body["messages"]] == [
        ("user", "who sent the link?"),
        ("assistant", "Yamada, https://example.com"),
    ]


def test_only_a_listed_transcript_reads_as_a_conversation(claude_client):
    for path in ("notes.txt", "../c1.jsonl", "other.jsonl"):
        response = claude_client.get(
            "/api/v2/plugins/viewer/conversation",
            params={"session": "one", "path": path},
            headers=AUTH,
        )
        assert response.status_code == 404
