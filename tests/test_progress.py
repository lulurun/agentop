"""The Progress Viewer's read of a track: tasks, their order and rounds, notes, generations and damaged records."""

import json
import os
import re
import sys
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
    assert any("2 lines unreadable" in i for i in result["issues"])

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
    # Only task and note headings start entries; any other '## ' line is text of the entry before it.
    assert "## Stray heading" in garbled["raw"]
    assert result["issues"] == ["track/1/EXECUTION_M2.md: P1_M2A1-4's entry can't be read unambiguously; its text is shown as written"]


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


def closed_pair(capsys, sessions, monkeypatch):
    """Two parallel tasks of one action, closed in reverse open order within a minute: P1_M2A1-1 is the newest closure."""
    setup_plan(capsys)
    start(capsys, "dev", "M2A1")
    start(capsys, "api", "M2A1")
    report(monkeypatch, capsys, "dev", "dev's summary")
    report(monkeypatch, capsys, "api", "api's summary")
    ag(capsys, "coordinator", "task", "accept", "api", "--checked", "x", "--project", "alpha")
    ag(capsys, "coordinator", "task", "accept", "dev", "--checked", "y", "--project", "alpha")


def test_an_undecodable_direction_is_still_shown_with_the_rest(alpha, capsys, sessions, monkeypatch):
    closed_pair(capsys, sessions, monkeypatch)
    (folder() / "track/1/DIRECTION.md").write_bytes(b"# Direction 1\n\n## Direction\n\xff\n")
    result = progress.read(alpha)
    assert "\ufffd" in result["direction"] and not result["direction_placeholder"]
    assert [t["id"] for t in result["completed"]] == ["P1_M2A1-1", "P1_M2A1-2"] and result["plan"]
    (folder() / "track/1/DIRECTION.md").write_bytes(b"## Direction\n\n" + track.DIRECTION_PLACEHOLDER.encode() + b"\n\xff")
    assert progress.read(alpha)["direction_placeholder"] is False
    (folder() / "track/1/DIRECTION.md").write_text("## Direction\n\n" + track.DIRECTION_PLACEHOLDER + "\n")
    assert progress.read(alpha)["direction_placeholder"] is True


def test_malformed_journal_values_are_ignored_and_valid_events_kept(alpha, capsys, sessions, monkeypatch):
    closed_pair(capsys, sessions, monkeypatch)
    without_snapshot()
    bad = [
        {"id": "P1_M2A1-1", "direction": 1, "round": "bad", "event": "report", "status": "open", "at": 1.0},
        {"id": "P1_M2A1-2", "direction": 1, "round": 10**12, "event": "open", "status": "open", "at": "soon"},
        {"id": "P1_M2A1-2", "direction": 1, "round": 1, "event": "fail", "status": "weird", "at": -5},
        {"id": "P1_M2A1-1", "direction": True, "event": "fail", "status": "failed", "at": 9e99},
        {"id": "P1_M2A1-1", "direction": "1", "event": "fail", "status": "failed", "at": 9e99},
        {"id": "P2_M2A1-1", "direction": 1, "event": "fail", "status": "failed", "at": 9e99},
        {"id": 7, "direction": 1}, {"id": "P1_M2A1-1", "direction": 1, "action": {"x": 1}, "session": ["s"]},
    ]
    with open(folder() / "track/tasks.jsonl", "a") as f:
        f.writelines(json.dumps(e) + "\n" for e in bad)
        f.write('{"id": "P1_M2A1-1", "direction": 1, "event": "accept", "status": "accepted", "at": NaN, "round": Infinity}\n')
        f.write('{"id": "P1_M2A1-1", "direction": 1, "at": 1e400}\n')
    result = progress.read(alpha)
    assert [t["id"] for t in result["completed"]] == ["P1_M2A1-1", "P1_M2A1-2"]
    dev = by_id(result)["P1_M2A1-1"]
    assert dev["state"] == "accepted" and dev["closed_at"] and not dev["closed_approx"] and len(dev["rounds"]) == 1
    assert dev["action"] == "M2A1" and dev["session"] == "dev" and dev["rounds"][0]["summary"] == "dev's summary"
    assert any("invalid fields" in i for i in result["issues"]) and any("unreadable" in i for i in result["issues"])
    json.dumps(result, allow_nan=False)


def test_a_malformed_or_incomplete_snapshot_row_does_not_erase_the_markdown(alpha, capsys, sessions, monkeypatch):
    closed_pair(capsys, sessions, monkeypatch)
    path = folder() / ".project/tasks.json"
    tasks = json.loads(path.read_text())["tasks"]
    tasks[0] = {"id": "P1_M2A1-1", "direction": 1, "rounds": "x", "goal": 5, "accept": "no", "status": "weird",
                "closed_at": float("inf"), "opened_at": True, "checked": ["y"]}
    tasks[1] = {"id": "P1_M2A1-2", "direction": 1, "rounds": [None, {"summary": 3}] * 10**4}
    path.write_text(json.dumps({"tasks": tasks}))
    result = by_id(progress.read(alpha))
    dev, api = result["P1_M2A1-1"], result["P1_M2A1-2"]
    assert dev["input"]["goal"] == "dev's goal" and dev["input"]["accept"] == ["tests pass"]
    assert dev["rounds"][0]["summary"] == "dev's summary" and dev["checked"] == "y" and dev["state"] == "accepted"
    assert api["input"]["artifact"] == "results/api.md" and api["rounds"][0]["summary"] == "api's summary"
    assert len(api["rounds"]) == progress.MAX_ROUNDS
    assert [t["id"] for t in progress.read(alpha)["completed"]] == ["P1_M2A1-1", "P1_M2A1-2"]


def test_records_past_the_first_megabyte_are_read_and_oversized_ones_skipped(alpha, capsys, sessions, monkeypatch):
    closed_pair(capsys, sessions, monkeypatch)
    without_snapshot()
    journal = folder() / "track/tasks.jsonl"
    padding = json.dumps({"id": "P9_M1A1-1", "direction": 9, "event": "open", "status": "open", "at": 1.0, "pad": "x" * 200})
    huge = json.dumps({"id": "P1_M2A1-1", "direction": 1, "event": "accept", "pad": "x" * (progress.MAX_RECORD_BYTES + 10)})
    journal.write_text((padding + "\n") * 6000 + huge + "\n" + journal.read_text())
    assert journal.stat().st_size > 2_000_000

    execution = folder() / "track/1/EXECUTION_M2.md"
    head, _, entries = execution.read_text().partition("\n## ")
    notes = "".join(f"## P1_M2A1 note {k}  by: coordinator\n2026-11-25 10:00\n\n{'n' * 1000}\n\n" for k in range(1, 1500))
    oversized = "## P1_M2A2-9  session `big`  claude\n" + ("y" * 1000 + "\n") * 300 + "\n"
    execution.write_text(head + "\n\n" + notes + oversized + "## " + entries)
    assert execution.stat().st_size > 1_500_000

    result = progress.read(alpha)
    assert [t["id"] for t in result["completed"]] == ["P1_M2A1-1", "P1_M2A1-2", "P1_M2A2-9"]
    dev = by_id(result)["P1_M2A1-1"]
    assert not dev["closed_approx"] and dev["input"]["goal"] == "dev's goal" and dev["rounds"][0]["summary"] == "dev's summary"
    assert by_id(result)["P1_M2A2-9"]["raw"] is None and len(result["notes"]) == 1499
    assert any("over" in i and "tasks.jsonl" in i for i in result["issues"])
    assert any("P1_M2A2-9" in i and "isn't shown" in i for i in result["issues"])


def test_markdown_records_of_another_direction_are_never_joined(alpha, capsys, sessions, monkeypatch):
    closed_pair(capsys, sessions, monkeypatch)
    with open(folder() / "track/1/EXECUTION_M2.md", "a") as f:
        f.write("\n## P2_M2A1-1  garbled heading\nx\n\n## P2_M2A1 note 1  by: coordinator\n2026-11-25 10:00\n\nforeign\n"
                "\n## P2_M2A1-2  session `z`  claude\nOpened 2026-11-25 10:00 · rounds 1 · open\n")
    result = progress.read(alpha)
    assert rows(result) == ["P1_M2A1-1", "P1_M2A1-2"] and result["notes"] == []
    assert sum("belongs to another direction" in i for i in result["issues"]) == 3


def test_headings_inside_reports_stay_in_their_entry_without_tasks_json(alpha, capsys, sessions, monkeypatch):
    setup_plan(capsys)
    start(capsys, "dev", "M2A1")
    start(capsys, "api", "M2A2")
    start(capsys, "ops", "M2A2")
    first = "Verification complete.\n\n## Results\n\n148 tests passed."
    feedback = "Two things:\n\n## Missing\n\n- the docs\n- a test"
    second = "Fixed both.\n\n## Results\n\n150 tests passed.\n\n### Notes\nNothing else."
    checked = "Ran the suite.\n\n## Checked\n\nAll criteria."
    report(monkeypatch, capsys, "dev", first)
    ag(capsys, "coordinator", "task", "reject", "dev", "--feedback", feedback, "--project", "alpha")
    report(monkeypatch, capsys, "dev", second)
    ag(capsys, "coordinator", "task", "accept", "dev", "--checked", checked, "--project", "alpha")
    report(monkeypatch, capsys, "api", "Plain summary")
    ag(capsys, "coordinator", "task", "accept", "api", "--checked", "Verified too", "--project", "alpha")
    report(monkeypatch, capsys, "ops", "Half done.\n\n## Blocked on\n\nAccess.")
    ag(capsys, "coordinator", "note", "M2A1", "Note after the tasks", "--project", "alpha")
    execution = (folder() / "track/1/EXECUTION_M2.md").read_text()
    assert first in execution and second in execution and execution.count("## Results") == 2
    without_snapshot()

    result = progress.read(alpha)
    assert result["issues"] == []
    assert [t["id"] for t in result["completed"]] == ["P1_M2A2-1", "P1_M2A1-1"]
    dev, api, ops = (by_id(result)[i] for i in ("P1_M2A1-1", "P1_M2A2-1", "P1_M2A2-2"))
    assert dev["input"] == {"goal": "dev's goal", "accept": ["tests pass"], "artifact": "results/dev.md",
                            "brief": "Read the README first. / Then the spec."}
    assert [(r["summary"], r["feedback"]) for r in dev["rounds"]] == [(first, feedback), (second, None)]
    assert (dev["state"], dev["checked"], dev["raw"]) == ("accepted", checked, None)
    assert (api["rounds"][0]["summary"], api["checked"], api["input"]["goal"]) == ("Plain summary", "Verified too", "api's goal")
    assert (ops["state"], ops["rounds"][0]["summary"]) == ("awaiting review", "Half done.\n\n## Blocked on\n\nAccess.")
    assert [(n["action"], n["body"]) for n in result["notes"]] == [("M2A1", "Note after the tasks")]


def test_a_heading_only_in_feedback_or_checked_text_is_kept_once(alpha, capsys, sessions, monkeypatch):
    setup_plan(capsys)
    start(capsys, "dev", "M2A1")
    start(capsys, "api", "M2A2")
    feedback = "Two things:\n\n## Missing\n\n- the docs"
    checked = "Read it.\n\n## Checked\n\nEverything."
    report(monkeypatch, capsys, "dev", "First try")
    ag(capsys, "coordinator", "task", "reject", "dev", "--feedback", feedback, "--project", "alpha")
    report(monkeypatch, capsys, "dev", "Second try")
    ag(capsys, "coordinator", "task", "accept", "dev", "--checked", checked, "--project", "alpha")
    report(monkeypatch, capsys, "api", "Same\n\n## Missing\n\n- the docs")
    without_snapshot()
    result = progress.read(alpha)
    dev, api = by_id(result)["P1_M2A1-1"], by_id(result)["P1_M2A2-1"]
    assert [(r["summary"], r["feedback"]) for r in dev["rounds"]] == [("First try", feedback), ("Second try", None)]
    assert dev["checked"] == checked and dev["state"] == "accepted" and result["issues"] == []
    assert api["rounds"][0]["summary"] == "Same\n\n## Missing\n\n- the docs" and api["state"] == "awaiting review"


def old_writer(monkeypatch):
    """The writer before entry records: it replaced an entry only up to its first '## ' line."""

    def replace(path, heading_id, entry, n, m):
        entry = entry.split("\n", 1)[1] if entry.startswith("<!-- ") else entry
        text = path.read_text() if path.exists() else track._execution_head(n, m)
        pattern = re.compile(rf"^## {re.escape(heading_id)}\s.*?(?=^## |\Z)", re.MULTILINE | re.DOTALL)
        if pattern.search(text):
            text = pattern.sub(lambda _: entry.rstrip() + "\n\n", text, count=1)
        else:
            text = text.rstrip() + "\n\n" + entry.rstrip() + "\n"
        track._sole_write(path, text.rstrip() + "\n")

    # Plugin tests may load the package again, so patch every copy of the module the CLI could be using.
    copies = [(m, m._replace_entry) for m in list(sys.modules.values()) if getattr(m, "__file__", None) == track.__file__]
    for module, _ in copies:
        monkeypatch.setattr(module, "_replace_entry", replace)
    return lambda: [monkeypatch.setattr(module, "_replace_entry", current) for module, current in copies]


# Lines a report may hold that look like the entry format itself.
LOOKALIKE = ("Done.\n\n## P1_M2A2-1  session `api`  claude\nOpened 2026-11-25 10:00 · rounds 1 · accepted\n\n"
             "Evaluation:\n- round 1 accepted: not really\n- round 2: nor this\n<!-- agentop entry P1_M2A2-1 lines 2 -->\n"
             "## P1_M2A1 note 7  by: owner\n\n## Results\n\n148 tests passed.")


def test_a_task_update_replaces_its_whole_entry_whatever_its_text_holds(alpha, capsys, sessions, monkeypatch):
    setup_plan(capsys)
    start(capsys, "dev", "M2A1")
    start(capsys, "api", "M2A2")
    report(monkeypatch, capsys, "dev", LOOKALIKE)
    ag(capsys, "coordinator", "task", "reject", "dev", "--feedback", "BLOCKED: no\n\n## Results\n\nagain", "--project", "alpha")
    report(monkeypatch, capsys, "dev", "BLOCKED: is a word here\n\n## Results\n\n150 tests passed.")
    checked = "Verified.\n\n## Results\n\n148 tests passed.\n\n" + LOOKALIKE
    ag(capsys, "coordinator", "task", "accept", "dev", "--checked", checked, "--project", "alpha")
    report(monkeypatch, capsys, "api", "Ordinary summary")
    ag(capsys, "coordinator", "task", "accept", "api", "--checked", "Verified too", "--project", "alpha")
    ag(capsys, "coordinator", "note", "M2A1", LOOKALIKE, "--project", "alpha")
    ag(capsys, "coordinator", "note", "M2A1", "Second note", "--project", "alpha")
    execution = (folder() / "track/1/EXECUTION_M2.md").read_text()
    # Each text is in the file once per place it belongs: no older version is left behind.
    assert execution.count(LOOKALIKE) == 3 and execution.count("again") == 1 and execution.count("Ordinary summary") == 1
    assert track.has_entry(alpha, "M2A1") and "P1_M2A1 note 2" in execution and "note 8" not in execution
    without_snapshot()

    result = progress.read(alpha)
    assert result["issues"] == []
    assert [t["id"] for t in result["completed"]] == ["P1_M2A2-1", "P1_M2A1-1"]
    dev, api = by_id(result)["P1_M2A1-1"], by_id(result)["P1_M2A2-1"]
    assert [(r["summary"], r["blocked"], r["feedback"]) for r in dev["rounds"]] == [
        (LOOKALIKE, False, "BLOCKED: no\n\n## Results\n\nagain"),
        ("BLOCKED: is a word here\n\n## Results\n\n150 tests passed.", False, None)]
    assert (dev["checked"], dev["state"], dev["raw"], dev["input"]["goal"]) == (checked, "accepted", None, "dev's goal")
    assert (api["rounds"][0]["summary"], api["checked"], api["input"]["artifact"]) == ("Ordinary summary", "Verified too", "results/api.md")
    assert [n["body"] for n in result["notes"]] == ["Second note", LOOKALIKE]


def test_a_legacy_entry_without_its_older_versions_and_rewritten_whole(alpha, capsys, sessions, monkeypatch):
    restore = old_writer(monkeypatch)
    setup_plan(capsys)
    start(capsys, "dev", "M2A1")
    start(capsys, "api", "M2A2")
    start(capsys, "ops", "M2A2")
    first = "Verification complete.\n\n## Results\n\n148 tests passed."
    feedback = "Two things:\n\n## Missing\n\n- the docs"
    second = "Fixed.\n\n## Results\n\n150 tests passed."
    checked = "Verified independently.\n\n## Results\n\n148 tests passed.\n\nTypeScript and the build also passed."
    report(monkeypatch, capsys, "dev", first)
    ag(capsys, "coordinator", "task", "reject", "dev", "--feedback", feedback, "--project", "alpha")
    report(monkeypatch, capsys, "dev", second)
    ag(capsys, "coordinator", "task", "accept", "dev", "--checked", checked, "--project", "alpha")
    report(monkeypatch, capsys, "api", "Plain\n\n## Results\n\nok")
    ag(capsys, "coordinator", "task", "accept", "api", "--checked", "Verified too", "--project", "alpha")
    report(monkeypatch, capsys, "ops", "Half done.\n\n## Blocked on\n\nAccess.")
    path = folder() / "track/1/EXECUTION_M2.md"
    legacy = path.read_text()
    assert "<!--" not in legacy and legacy.count("## Results") > 4
    snapshot = (folder() / ".project/tasks.json").read_text()
    without_snapshot()

    def check(result):
        assert result["issues"] == []
        assert [t["id"] for t in result["completed"]] == ["P1_M2A2-1", "P1_M2A1-1"]
        dev, api, ops = (by_id(result)[i] for i in ("P1_M2A1-1", "P1_M2A2-1", "P1_M2A2-2"))
        assert [(r["summary"], r["feedback"]) for r in dev["rounds"]] == [(first, feedback), (second, None)]
        assert (dev["checked"], dev["raw"], dev["input"]["artifact"]) == (checked, None, "results/dev.md")
        assert (api["rounds"][0]["summary"], api["checked"]) == ("Plain\n\n## Results\n\nok", "Verified too")
        assert (ops["state"], ops["rounds"][0]["summary"]) == ("awaiting review", "Half done.\n\n## Blocked on\n\nAccess.")

    check(progress.read(alpha))

    # The current writer replaces a legacy entry with every leftover after it, and leaves the other entries as they were.
    restore()
    (folder() / ".project/tasks.json").write_text(snapshot)
    ag(capsys, "coordinator", "task", "reject", "ops", "--feedback", "More", "--project", "alpha")
    updated = path.read_text()
    assert updated.count("Half done.") == 1 and updated.count("<!-- agentop entry") == 1
    assert updated.startswith(legacy[:legacy.index("## P1_M2A2-2")])
    without_snapshot()
    result = progress.read(alpha)
    check({**result, "active": [{**t, "state": "awaiting review"} for t in result["active"]]})
    assert by_id(result)["P1_M2A2-2"]["rounds"][0]["feedback"] == "More"


def test_ambiguous_legacy_and_edited_entries_are_shown_as_written(alpha, capsys, sessions, monkeypatch):
    setup_plan(capsys)
    start(capsys, "dev", "M2A1")
    start(capsys, "api", "M2A2")
    report(monkeypatch, capsys, "dev", "Done")
    ag(capsys, "coordinator", "task", "accept", "dev", "--checked", "Checked", "--project", "alpha")
    report(monkeypatch, capsys, "api", "Also done")
    path = folder() / "track/1/EXECUTION_M2.md"
    # A hand edit adds a line to the first entry: it no longer fits its record.
    path.write_text(path.read_text().replace("- round 1: Done\n", "- round 1: Done\nedited by hand\n"))
    # A legacy entry whose text after its evaluation repeats its first heading, but isn't an older version of it.
    path.write_text(path.read_text() + (
        "\n## P1_M2A1-2  session `old`  claude\nOpened 2026-11-25 10:00 · closed 2026-11-25 10:05 · rounds 1 · accepted\n\n"
        "Input: g\n- c\nArtifact: results/old.md\n\nOutput:\n- round 1: A\n\n## Results\n\nB\n\nEvaluation:\n"
        "- round 1 accepted: ok\n\n## Results\n\nsomething else\n"))
    without_snapshot()
    result = progress.read(alpha)
    dev, old, api = (by_id(result)[i] for i in ("P1_M2A1-1", "P1_M2A1-2", "P1_M2A2-1"))
    assert "edited by hand" in dev["raw"] and dev["rounds"][0]["summary"] == "Done\nedited by hand"
    assert "something else" in old["raw"] and old["input"]["artifact"] == "results/old.md"
    assert (api["raw"], api["rounds"][0]["summary"], api["state"]) == (None, "Also done", "awaiting review")
    assert sorted(result["issues"]) == [f"track/1/EXECUTION_M2.md: {i}'s entry can't be read unambiguously; its text is shown as written"
                                        for i in ("P1_M2A1-1", "P1_M2A1-2")]


def test_a_legacy_report_quoting_an_evaluation_block_is_read_whole(alpha, capsys, sessions, monkeypatch):
    old_writer(monkeypatch)
    setup_plan(capsys)
    start(capsys, "dev", "M2A1")
    start(capsys, "api", "M2A2")
    summary = "The status output included:\n\nEvaluation:\n- (not decided yet)\n\nThe example is part of the report."
    report(monkeypatch, capsys, "dev", summary)
    ag(capsys, "coordinator", "task", "accept", "dev", "--checked", "Checked", "--project", "alpha")
    report(monkeypatch, capsys, "api", "Next row")
    ag(capsys, "coordinator", "task", "accept", "api", "--checked", "Also checked", "--project", "alpha")
    without_snapshot()
    result = progress.read(alpha)
    dev, api = by_id(result)["P1_M2A1-1"], by_id(result)["P1_M2A2-1"]
    assert (dev["rounds"][0]["summary"], dev["checked"], dev["raw"]) == (summary, "Checked", None)
    assert (api["rounds"][0]["summary"], api["checked"]) == ("Next row", "Also checked") and result["issues"] == []


def test_legacy_checked_text_quoting_the_previous_leftover_is_read_whole(alpha, capsys, sessions, monkeypatch):
    old_writer(monkeypatch)
    setup_plan(capsys)
    start(capsys, "dev", "M2A1")
    start(capsys, "api", "M2A2")
    summary = "Verification complete.\n\n## Results\n\n148 tests passed."
    report(monkeypatch, capsys, "dev", summary)
    path = folder() / "track/1/EXECUTION_M2.md"
    leftover = path.read_text().split("\n## Results", 1)[1].split("\n\n## P1_M2A2-1", 1)[0].rstrip()
    checked = f"Verified against this excerpt:\n\n## Results{leftover}"
    ag(capsys, "coordinator", "task", "accept", "dev", "--checked", checked, "--project", "alpha")
    report(monkeypatch, capsys, "api", "Next row")
    assert checked in path.read_text() and checked.endswith("Evaluation:\n- (not decided yet)")
    without_snapshot()
    result = progress.read(alpha)
    dev, api = by_id(result)["P1_M2A1-1"], by_id(result)["P1_M2A2-1"]
    assert (dev["rounds"][0]["summary"], dev["checked"], dev["raw"]) == (summary, checked, None)
    assert api["rounds"][0]["summary"] == "Next row" and result["issues"] == []


def test_a_legacy_entry_with_two_exact_readings_is_shown_as_written(alpha, capsys, sessions, monkeypatch):
    old_writer(monkeypatch)
    setup_plan(capsys)
    start(capsys, "dev", "M2A1")
    start(capsys, "api", "M2A2")
    report(monkeypatch, capsys, "dev", "Done")
    # Read either way, the entry renders back the same: the summary ends with a decision, or the checked text holds one.
    checked = "First part\n\nEvaluation:\n- round 1 accepted: second part"
    ag(capsys, "coordinator", "task", "accept", "dev", "--checked", checked, "--project", "alpha")
    report(monkeypatch, capsys, "api", "Next row")
    without_snapshot()
    result = progress.read(alpha)
    dev, api = by_id(result)["P1_M2A1-1"], by_id(result)["P1_M2A2-1"]
    assert checked in dev["raw"] and dev["state"] == "accepted"
    assert result["issues"] == ["track/1/EXECUTION_M2.md: P1_M2A1-1's entry can't be read unambiguously; its text is shown as written"]
    assert api["rounds"][0]["summary"] == "Next row" and api["raw"] is None
