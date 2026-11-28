"""Result files: the P{N}_M{m}A{a}_ prefix a task's artifact gets, and each action's results in the Progress Viewer."""

import json
import os
import shutil

import pytest
from fastapi.testclient import TestClient

from agentop import projects
from agentop.plugins.projects import progress, pursuit, results
from agentop.service.app import create_app
from agentop.service.config import Settings
from agentop.service.identity import Identity
from agentop.service.plugins import DEFAULT_ROOT, PluginHost
from tests import test_track
from tests.conftest import signed_in
from tests.test_plugins import AUTH, TOKEN, QuietStore
from tests.test_track import ag, as_coordinator, folder, report, setup_plan

alpha, sessions, no_service = test_track.alpha, test_track.sessions, test_track.no_service


def start(capsys, name, action, artifact, code=0):
    return ag(capsys, "coordinator", "task", "start", "--project", "alpha", "--action", action, name, "--tool", "claude",
              "--cwd", "/x", "--goal", f"{name}'s goal", "--accept", "tests pass", "--artifact", artifact, code=code)


def stored(task_id):
    return next(t for t in json.loads((folder() / ".project/tasks.json").read_text())["tasks"] if t["id"] == task_id)


def test_start_names_the_artifact_and_brief_report_and_record_all_use_that_path(alpha, capsys, sessions, monkeypatch):
    setup_plan(capsys)
    out, _ = start(capsys, "dev", "M2A1", "results/dev.md")
    assert "named results/P1_M2A1_dev.md (asked: results/dev.md)" in out
    task = stored("P1_M2A1-1")
    assert task["artifact"] == "results/P1_M2A1_dev.md" and task["artifact_requested"] == "results/dev.md"
    brief = sessions.prompts[-1]
    assert f"write your result to {folder().resolve() / 'results/P1_M2A1_dev.md'}." in brief
    assert "with the same prefix, P1_M2A1_ (for example results/P1_M2A1_tests.txt)" in brief
    assert "Artifact: results/P1_M2A1_dev.md" in (folder() / "track/1/EXECUTION_M2.md").read_text()

    # The name the Coordinator asked for is not the artifact: a report needs the prefixed file.
    monkeypatch.setattr("agentop.ops.current_session", lambda: ("dev-1", "alpha", "dev"))
    (folder() / "results").mkdir()
    (folder() / "results/dev.md").write_text("wrong file")
    _, err = ag(capsys, "coordinator", "task", "report", "done", code=1)
    assert "The artifact results/P1_M2A1_dev.md does not exist" in err
    (folder() / "results/P1_M2A1_dev.md").write_text("result")
    ag(capsys, "coordinator", "task", "report", "done")
    target, notice = sessions.sent[-1]
    assert target == "Coordinator" and "Artifact: results/P1_M2A1_dev.md (present)" in notice
    as_coordinator(monkeypatch)
    ag(capsys, "coordinator", "task", "reject", "dev", "--feedback", "more", "--project", "alpha")
    assert "update results/P1_M2A1_dev.md" in sessions.sent[-1][1]


def test_a_prefixed_name_is_kept_and_another_actions_prefix_is_refused_without_using_a_number(alpha, capsys, sessions):
    setup_plan(capsys)
    _, err = start(capsys, "api", "M2A1", "results/P1_M2A10_api.md", code=1)
    assert "starts with P1_M2A10_, which is not this task's prefix P1_M2A1_" in err
    _, err = start(capsys, "api", "M2A1", "results/P0_M2A1-3_api.md", code=1)
    assert "Name it results/P1_M2A1_api.md" not in err and "Name it P1_M2A1_api.md" in err
    start(capsys, "api", "M2A1", "results/P1_M2A1_api.md")
    assert stored("P1_M2A1-1")["artifact"] == "results/P1_M2A1_api.md"
    assert "artifact_requested" not in stored("P1_M2A1-1")
    start(capsys, "sub", "M2A2", "notes/deep/sub.md")
    assert stored("P1_M2A2-1")["artifact"] == "notes/deep/P1_M2A2_sub.md"
    _, err = start(capsys, "esc", "M2A2", "../outside.md", code=1)
    assert "must be inside the project folder" in err


def test_a_retry_never_overwrites_an_earlier_tasks_artifact(alpha, capsys, sessions, monkeypatch):
    setup_plan(capsys)
    start(capsys, "dev", "M2A1", "results/dev.md")
    ag(capsys, "coordinator", "task", "fail", "dev", "--note", "wrong approach", "--project", "alpha")
    # The first try left no file, but its record still names the path.
    start(capsys, "dev2", "M2A1", "results/dev.md")
    assert stored("P1_M2A1-2")["artifact"] == "results/P1_M2A1_dev-2.md"
    report(monkeypatch, capsys, "dev2")
    ag(capsys, "coordinator", "task", "fail", "dev2", "--note", "still wrong", "--project", "alpha")
    assert (folder() / "results/P1_M2A1_dev-2.md").read_text() == "result"
    # A file already on disk, from no task at all, is not overwritten either.
    (folder() / "results/P1_M2A1_dev-3.md").write_text("by hand")
    start(capsys, "dev3", "M2A1", "results/P1_M2A1_dev.md")
    assert stored("P1_M2A1-3")["artifact"] == "results/P1_M2A1_dev-3-2.md"
    assert (folder() / "results/P1_M2A1_dev-3.md").read_text() == "by hand"
    assert (folder() / "results/P1_M2A1_dev-2.md").read_text() == "result"


def test_legacy_records_and_tasks_on_no_action_keep_their_paths(alpha, capsys, sessions, monkeypatch):
    setup_plan(capsys)
    start(capsys, "dev", "M2A1", "results/dev.md")
    # An older task's record, before prefixes: its artifact anywhere, absolute or relative.
    path = folder() / ".project/tasks.json"
    data = json.loads(path.read_text())
    legacy = next(t for t in data["tasks"] if t["id"] == "P1_M2A1-1")
    legacy["artifact"] = str(folder() / "results/old-dev.md")
    path.write_text(json.dumps(data))
    (folder() / "results").mkdir()
    (folder() / "results/old-dev.md").write_text("old result")
    report(monkeypatch, capsys, "dev")
    ag(capsys, "coordinator", "task", "accept", "dev", "--checked", "read it", "--project", "alpha")
    assert stored("P1_M2A1-1")["status"] == "accepted"
    m2a1 = next(a for a in progress.read(alpha)["actions"] if a["ref"] == "M2A1")
    assert [(r["name"], r["path"], r["task"], r["kind"], r["problem"]) for r in m2a1["results"]] == [
        ("old-dev.md", "results/old-dev.md", "P1_M2A1-1", "artifact", None)]
    # A task on no action has no prefix to take.
    assert pursuit.open_task(alpha, "loose", "claude", "/x", "g", ["a"], "results/loose.md")["artifact"] == "results/loose.md"


def test_results_group_under_their_exact_action_and_direction(alpha, capsys, sessions, monkeypatch):
    setup_plan(capsys)
    start(capsys, "dev", "M2A1", "results/dev.md")
    report(monkeypatch, capsys, "dev")
    start(capsys, "api", "M2A2", "results/api.md")
    r = folder() / "results"
    for name in ("P1_M2A1_tests.txt", "P1_M2A10_other.txt", "P2_M2A1_next.txt", "P01_M2A1_padded.txt",
                 "P1_M2A1.txt", "P1_M2A1-1_attempt.txt", "notes.txt", "P1_M2A1_"):
        (r / name).write_text("x")
    (r / "P1_M2A1_empty.log").write_text("")
    (r / "P1_M2A1_shots").mkdir()
    (folder().parent / "secret.txt").write_text("no")
    os.symlink(folder().parent / "secret.txt", r / "P1_M2A1_link.txt")

    data = progress.read(alpha)
    acts = {a["ref"]: a for a in data["actions"]}
    assert list(acts) == ["M2A1", "M2A2", "M2A10"]
    assert acts["M2A1"]["title"] == "Layout" and acts["M2A10"]["title"] is None
    rows = [(e["name"], e["task"], e["kind"], e["problem"]) for e in acts["M2A1"]["results"]]
    assert rows == [
        ("P1_M2A1_dev.md", "P1_M2A1-1", "artifact", None),
        ("P1_M2A1_empty.log", None, "supporting", "empty"),
        ("P1_M2A1_link.txt", None, "supporting", "outside"),
        ("P1_M2A1_shots", None, "supporting", "folder"),
        ("P1_M2A1_tests.txt", None, "supporting", None),
    ]
    assert [(e["name"], e["problem"]) for e in acts["M2A2"]["results"]] == [("P1_M2A2_api.md", "missing")]
    assert [e["name"] for e in acts["M2A10"]["results"]] == ["P1_M2A10_other.txt"]

    ag(capsys, "coordinator", "task", "fail", "api", "--note", "n", "--project", "alpha")
    ag(capsys, "coordinator", "task", "accept", "dev", "--checked", "ok", "--project", "alpha")
    ag(capsys, "coordinator", "direction", "new", "--project", "alpha", "--text", "-", stdin="Next direction.")
    second = {a["ref"]: a for a in progress.read(alpha)["actions"]}
    assert [e["name"] for e in second["M2A1"]["results"]] == ["P2_M2A1_next.txt"]
    first = {a["ref"]: a for a in progress.read(alpha, 1)["actions"]}
    assert "P2_M2A1_next.txt" not in [e["name"] for e in first["M2A1"]["results"]]


def test_a_large_results_folder_is_scanned_up_to_a_bound(alpha, capsys, sessions, monkeypatch):
    setup_plan(capsys)
    monkeypatch.setattr(results, "MAX_SCAN", 50)
    monkeypatch.setattr(results, "MAX_PER_ACTION", 10)
    r = folder() / "results"
    r.mkdir(exist_ok=True)
    for i in range(30):
        (r / f"P1_M2A1_{i:03}.txt").write_text("x")
    for i in range(40):
        (r / f"other-{i}.txt").write_text("x")
    data = progress.read(alpha)
    m2a1 = next(a for a in data["actions"] if a["ref"] == "M2A1")
    assert data["results_truncated"] is True
    assert len(m2a1["results"]) <= 10 and m2a1.get("more", 0) >= 0


@pytest.mark.parametrize("name, key", [
    ("P7_M1A4_x.md", (7, "M1A4")), ("P7_M1A10_x", (7, "M1A10")), ("P7_M1A1_", None), ("P07_M1A1_x", None),
    ("P7_M1A1-2_x", None), ("p7_m1a1_x", None), ("P7_M1A1x", None),
])
def test_prefix_key_is_exact(name, key):
    assert results.prefix_key(name) == key


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTOP_HOME", str(tmp_path))
    root = tmp_path / "plugins"
    shutil.copytree(DEFAULT_ROOT / "viewer", root / "viewer")
    store = QuietStore()
    host = PluginHost(store, roots=[root], data_dir=tmp_path / "plugin-data")
    app = create_app(Settings(), Identity("service-token", "iid", "test"), store, host, clients=signed_in(TOKEN))
    found = projects.create("alpha", "projects")
    base = projects.folder(found["name"])
    (base / "results").mkdir()
    (base / "results/P1_M1A1_report.md").write_text("# Done\n")
    (base / "results/P1_M1A1_blob.bin").write_bytes(b"\0\1\2")
    (tmp_path / "outside.txt").write_text("no")
    os.symlink(tmp_path / "outside.txt", base / "results/P1_M1A1_escape.txt")
    os.symlink(tmp_path, base / "results/P1_M1A1_dir")
    with TestClient(app) as test_client:
        yield test_client


def read(client, **params):
    return client.get("/api/v2/plugins/viewer/file", params={"source": "project", "project": "alpha", **params}, headers=AUTH)


def test_a_project_result_reads_as_text_through_the_file_viewer(client):
    body = read(client, path="results/P1_M1A1_report.md").json()
    assert body["text"] == "# Done\n" and body["binary"] is False and body["conversation"] is False
    assert body["absolute"].endswith("projects/alpha/results/P1_M1A1_report.md")
    assert read(client, path="results/P1_M1A1_blob.bin").json()["binary"] is True


@pytest.mark.parametrize("params, status", [
    ({"path": "../outside.txt"}, 403),
    ({"path": "results/../../outside.txt"}, 403),
    ({"path": "results/P1_M1A1_escape.txt"}, 403),
    ({"path": "results/P1_M1A1_dir/outside.txt"}, 403),
    ({"path": ".project/project.json"}, 403),
    ({"path": "results/missing.md"}, 404),
    ({"path": "results"}, 404),
    ({"path": "x", "project": "nope"}, 404),
    ({"path": "x", "project": "../alpha"}, 404),
    ({"path": "x", "project": ""}, 422),
])
def test_project_reads_stay_inside_the_project_folder(client, params, status):
    assert read(client, **params).status_code == status


def test_a_session_source_still_needs_a_session(client):
    response = client.get("/api/v2/plugins/viewer/file", params={"path": "x"}, headers=AUTH)
    assert response.status_code == 422
    assert client.get("/api/v2/plugins/viewer/file", params={"path": "x"}).status_code == 401
