"""The Progress Viewer's read of a track: tasks, their order and rounds, notes, generations and damaged records."""

import os
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agentop import projects
from agentop.plugins.projects import progress, pursuit, track
from agentop.service.app import create_app
from agentop.service.config import Settings
from agentop.service.identity import Identity
from agentop.service.plugins import PluginHost
from tests import test_track
from tests.conftest import signed_in
from tests.test_plugins import QuietStore
from tests.test_track import ag, folder, report, setup_plan, start

alpha, sessions, no_service = test_track.alpha, test_track.sessions, test_track.no_service

PLUGINS = Path(progress.__file__).resolve().parents[1]
AUTH = {"Authorization": "Bearer test-token"}


def rows(result):
    return [t["id"] for t in result["active"] + result["completed"]]


def by_id(result):
    return {t["id"]: t for t in result["active"] + result["completed"]}


def without_snapshot():
    (folder() / ".project/tasks.json").unlink()


def test_closures_sort_newest_first_within_a_minute_and_with_no_tasks_json(alpha, capsys, sessions, monkeypatch):
    setup_plan(capsys)
    start(capsys, "dev", "M2A1")
    start(capsys, "api", "M2A1")
    report(monkeypatch, capsys, "dev")
    report(monkeypatch, capsys, "api")
    ag(capsys, "coordinator", "task", "accept", "api", "--checked", "read it", "--project", "alpha")
    ag(capsys, "coordinator", "task", "accept", "dev", "--checked", "read it too", "--project", "alpha")
    start(capsys, "web", "M2A2")

    result = progress.read(alpha)
    assert [t["id"] for t in result["completed"]] == ["P1_M2A1-1", "P1_M2A1-2"]
    assert [t["id"] for t in result["active"]] == ["P1_M2A2-1"]
    assert all(not t["closed_approx"] for t in result["completed"])
    assert result["completed"][0]["closed_at"] > result["completed"][1]["closed_at"]
    assert by_id(result)["P1_M2A2-1"]["state"] == "awaiting report"

    without_snapshot()
    again = progress.read(alpha)
    assert [t["id"] for t in again["completed"]] == ["P1_M2A1-1", "P1_M2A1-2"]
    dev = by_id(again)["P1_M2A1-1"]
    assert dev["sources"] == ["markdown", "journal"] and dev["checked"] == "read it too"
    assert dev["input"] == {"goal": "dev's goal", "accept": ["tests pass"], "artifact": "results/dev.md",
                            "brief": "Read the README first. / Then the spec."}


def test_rounds_blocked_reject_and_fail_without_a_report(alpha, capsys, sessions, monkeypatch):
    setup_plan(capsys)
    start(capsys, "dev", "M2A1")
    start(capsys, "api", "M2A2")
    start(capsys, "ops", "M2A2")
    report(monkeypatch, capsys, "dev", "first try")
    ag(capsys, "coordinator", "task", "reject", "dev", "--feedback", "criterion 1 fails\nsee line 3", "--project", "alpha")
    report(monkeypatch, capsys, "dev", "second try\nwith two lines")
    pursuit.report(alpha, "ops", "no access to the repo", blocked=True)
    ag(capsys, "coordinator", "task", "fail", "api", "--note", "never started", "--project", "alpha")

    kept = (folder() / ".project/tasks.json").read_text()
    for strip in (False, True):
        if strip:
            without_snapshot()
        result = by_id(progress.read(alpha))
        dev = result["P1_M2A1-1"]
        assert (dev["state"], dev["blocked"], len(dev["rounds"])) == ("awaiting review", False, 2)
        assert dev["rounds"][0]["summary"] == "first try" and dev["rounds"][0]["feedback"] == "criterion 1 fails\nsee line 3"
        assert dev["rounds"][1]["summary"] == "second try\nwith two lines"
        ops = result["P1_M2A2-2"]
        assert (ops["state"], ops["blocked"]) == ("awaiting review", True)
        api = result["P1_M2A2-1"]
        assert (api["state"], api["note"], api["rounds"][0].get("summary")) == ("failed", "never started", None)
        assert list(result) == ["P1_M2A2-2", "P1_M2A1-1", "P1_M2A2-1"]

    (folder() / ".project/tasks.json").write_text(kept)
    ag(capsys, "coordinator", "task", "accept", "dev", "--checked", "ran it", "--project", "alpha")
    result = progress.read(alpha)
    assert [t["id"] for t in result["completed"]] == ["P1_M2A1-1", "P1_M2A2-1"]
    assert result["completed"][0]["checked"] == "ran it"


def test_plan_status_and_notes_come_from_the_markdown(alpha, capsys, sessions, monkeypatch):
    setup_plan(capsys)
    ag(capsys, "coordinator", "note", "M2A2", "Wrote the commands by hand", "--project", "alpha")
    ag(capsys, "coordinator", "note", "M2A2", "The owner checked them", "--by", "owner", "--project", "alpha")
    ag(capsys, "coordinator", "plan", "action", "set", "M2A2", "--status", "done", "--project", "alpha")
    result = progress.read(alpha)
    assert "- M2A2 Commands  [done]" in result["plan"]
    notes = [(n["id"], n["by"], n["action"], n["body"]) for n in result["notes"]]
    assert notes == [("P1_M2A2 note 2", "owner", "M2A2", "The owner checked them"),
                     ("P1_M2A2 note 1", "coordinator", "M2A2", "Wrote the commands by hand")]
    assert result["notes"][0]["recorded_at"] is not None and rows(result) == []


def test_generations_2_and_10_are_kept_apart_with_unlinked_tasks_and_addenda(alpha, capsys, sessions, monkeypatch):
    setup_plan(capsys)
    start(capsys, "dev", "M2A1")
    ag(capsys, "coordinator", "task", "fail", "dev", "--note", "x", "--project", "alpha")
    ag(capsys, "coordinator", "direction", "new", "--project", "alpha", "--text", "Two", "--copy-plan")
    start(capsys, "dev", "M2A1")
    ag(capsys, "coordinator", "task", "fail", "dev", "--note", "x", "--project", "alpha")
    ag(capsys, "coordinator", "direction", "new", "--project", "alpha", "--text", "Ten", "--copy-plan")
    (folder() / "track/3").rename(folder() / "track/10")
    ag(capsys, "coordinator", "direction", "add", "--project", "alpha", "--text", "Also this")
    start(capsys, "dev", "M2A1")
    pursuit.open_task(alpha, "loose", "claude", "/x", "loose goal", ["a"], "results/loose.md")

    current = progress.read(alpha)
    assert (current["generations"], current["current"], current["generation"]) == ([10, 2, 1], 10, 10)
    assert "### Addendum 1" in current["direction"] and "Also this" in current["direction"]
    active = by_id(current)
    assert set(active) == {"P10_M2A1-1", "t4"} and active["t4"]["unlinked"] and not active["P10_M2A1-1"]["unlinked"]
    two = progress.read(alpha, 2)
    assert rows(two) == ["P2_M2A1-1"] and two["generation"] == 2 and not two["fallback"]
    assert "Two" in two["direction"] and "## M2 Track files  [stopped]" in two["plan"]
    assert rows(progress.read(alpha, 1)) == ["P1_M2A1-1"]
    missing = progress.read(alpha, 7)
    assert missing["generation"] == 10 and missing["fallback"]


def test_a_pending_direction_change_is_in_its_own_plan(alpha, capsys, sessions, monkeypatch):
    setup_plan(capsys)
    start(capsys, "dev", "M2A1")
    ag(capsys, "coordinator", "direction", "change", "--project", "alpha", "--text", "Pivot")
    result = progress.read(alpha)
    assert track.CHANGE_TITLE + "Pivot" in result["plan"] and result["generations"] == [1]


def test_missing_track_placeholder_and_missing_execution(tmp_path, monkeypatch, alpha):
    result = progress.read(alpha)
    assert result["direction_placeholder"] and result["missing"]["executions"] and rows(result) == []
    assert not result["missing"]["plan"] and not result["missing"]["direction"]

    projects.create("bare", "projects")
    bare = progress.read("bare")
    assert bare["missing"]["track"] and bare["generations"] == [] and not bare["legacy"]
    (projects.folder("bare") / "STATE.md").write_text("# State\n")
    assert progress.read("bare")["legacy"]

    (folder() / "track/1/PLAN.md").unlink()
    assert progress.read(alpha)["missing"]["plan"] and progress.read(alpha)["plan"] is None


def test_damaged_records_leave_the_rest_readable(alpha, capsys, sessions, monkeypatch):
    setup_plan(capsys)
    start(capsys, "dev", "M2A1")
    report(monkeypatch, capsys, "dev")
    ag(capsys, "coordinator", "task", "accept", "dev", "--checked", "x", "--project", "alpha")
    start(capsys, "api", "M2A2")
    (folder() / ".project/tasks.json").write_text("{not json")
    with open(folder() / "track/tasks.jsonl", "a") as f:
        f.write("half a line\n[1, 2]\n")
    result = progress.read(alpha)
    assert rows(result) == ["P1_M2A2-1", "P1_M2A1-1"]
    assert any("tasks.json is unreadable" in i for i in result["issues"])
    assert any("2 unreadable lines" in i for i in result["issues"])

    # Journal only: the Markdown entry is gone, so its input and output are unavailable.
    (folder() / "track/1/EXECUTION_M2.md").unlink()
    only = by_id(progress.read(alpha))
    assert only["P1_M2A1-1"]["sources"] == ["journal"] and only["P1_M2A1-1"]["input"] is None
    assert (only["P1_M2A1-1"]["state"], only["P1_M2A2-1"]["state"]) == ("accepted", "awaiting report")
    assert progress.read(alpha)["missing"]["executions"]


def test_markdown_only_closures_are_approximate_and_undated_ones_last(alpha, capsys, sessions, monkeypatch):
    setup_plan(capsys)
    (folder() / "track/1/EXECUTION_M2.md").write_text(
        "# Execution of M2 (Plan 1)\n\n"
        "## P1_M2A1-1  session `a`  claude\nOpened 2026-11-25 10:00 · closed 2026-11-25 10:05 · rounds 1 · accepted\n\n"
        "Input: g\n- c\nArtifact: results/a.md\n\nOutput:\n- round 1: did it\n\nEvaluation:\n- round 1 accepted: ok\n\n"
        "## P1_M2A1-2  session `b`  claude\nOpened 2026-11-25 10:00 · rounds 1 · failed\n\n"
        "Input: g\n- c\nArtifact: results/b.md\n\nOutput:\n- (no report yet)\n\nEvaluation:\n- round 1 failed: gone\n\n"
        "## P1_M2A1-3  session `c`  claude\nOpened 2026-11-25 10:00 · closed 2026-11-25 10:07 · rounds 1 · accepted\n\n"
        "Input: g\n- c\nArtifact: results/c.md\n\nOutput:\n- round 1: did it\n\nEvaluation:\n- round 1 accepted: ok\n\n"
        "## P1_M2A1-4  garbled heading\nsomething\n\n"
        "## Stray heading\n",
    )
    result = progress.read(alpha)
    order = [(t["id"], t["closed_approx"]) for t in result["completed"]]
    assert order == [("P1_M2A1-3", True), ("P1_M2A1-1", True), ("P1_M2A1-2", False), ("P1_M2A1-4", False)]
    assert result["completed"][1]["closed_at"] == time.mktime(time.strptime("2026-11-25 10:05", "%Y-%m-%d %H:%M"))
    garbled = by_id(result)["P1_M2A1-4"]
    assert garbled["state"] == "unknown" and "garbled heading" in garbled["raw"]
    assert any("Stray heading" in i for i in result["issues"])


def test_files_outside_the_project_are_not_read(alpha, capsys, tmp_path):
    secret = tmp_path / "secret.md"
    secret.write_text("## P1_M1A1-1  session `x`  claude\n")
    os.symlink(secret, folder() / "track/1/EXECUTION_M9.md")
    os.symlink(secret, folder() / "track/1/PLAN.md.tmp")
    (folder() / "track/1/DIRECTION.md").unlink()
    os.symlink(secret, folder() / "track/1/DIRECTION.md")
    result = progress.read(alpha)
    assert result["direction"] is None and rows(result) == []
    assert sum("outside the project" in i for i in result["issues"]) == 2


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTOP_HOME", str(tmp_path))
    store = QuietStore()
    host = PluginHost(store, roots=[PLUGINS], data_dir=tmp_path / "plugin-data")
    app = create_app(Settings(), Identity("service-token", "iid", "test"), store, host, clients=signed_in("test-token"))
    with TestClient(app) as test_client:
        yield test_client


def test_the_route_is_authenticated_and_scoped_to_the_plugins_projects(client):
    found = projects.create("alpha", "projects")
    track.write_first(found["name"])
    projects.create("theirs", "someplugin")
    base = "/api/v2/plugins/projects/projects"
    assert client.get(f"{base}/alpha/progress").status_code == 401
    ok = client.get(f"{base}/alpha/progress", headers=AUTH)
    assert ok.status_code == 200, ok.text
    assert ok.json()["generations"] == [1] and ok.json()["direction_placeholder"]
    assert client.get(f"{base}/alpha/progress?generation=1", headers=AUTH).json()["generation"] == 1
    assert client.get(f"{base}/alpha/progress?generation=0", headers=AUTH).status_code == 422
    assert client.get(f"{base}/theirs/progress", headers=AUTH).status_code == 404
    assert client.get(f"{base}/nope/progress", headers=AUTH).status_code == 404
