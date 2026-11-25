"""The Coordinator's track: directions, plans, execution entries, the agentop coordinator commands, checks and resume."""

import json
import sys
from types import SimpleNamespace

import pytest

from agentop import coordinator, ops, projects
from agentop.cli import main
from agentop.plugins.projects import cli, pursuit, track


@pytest.fixture
def alpha(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTOP_HOME", str(tmp_path))
    found = projects.create("alpha", "projects")
    coordinator.write_instructions(found)
    return "alpha"


@pytest.fixture
def sessions(monkeypatch):
    """ops faked: task start records the sessions it would start, stop and send do nothing."""
    started, prompts, sent = [], [], []
    monkeypatch.setattr(ops, "get_sessions", lambda with_meta=False: [])
    monkeypatch.setattr(ops, "start", lambda tool, cwd, name, params, project: started.append(name) or SimpleNamespace(name=f"{name}-1"))
    monkeypatch.setattr(ops, "first_prompt", lambda name, prompt, *a: prompts.append(prompt))
    monkeypatch.setattr(ops, "find_session", lambda name, project=None: {"name": f"{name}-1"})
    monkeypatch.setattr(ops, "stop", lambda name: None)
    monkeypatch.setattr("agentop.cli.deliver", lambda target, text, project: sent.append((target, text)))
    as_coordinator(monkeypatch)
    return SimpleNamespace(started=started, prompts=prompts, sent=sent)


def as_coordinator(monkeypatch):
    monkeypatch.setattr(ops, "current_session", lambda: ("coord-1", "alpha", projects.COORDINATOR))


def ag(capsys, *argv, stdin=None, code=0):
    """Run `agentop …` in-process; returns (stdout, stderr) and checks the exit code."""
    if stdin is not None:
        import io

        sys.stdin = io.StringIO(stdin)
    old = sys.argv
    sys.argv = ["agentop", *argv]
    try:
        main()
        got = 0
    except SystemExit as exc:
        got = exc.code or 0
    finally:
        sys.argv = old
        sys.stdin = sys.__stdin__
    out, err = capsys.readouterr()
    assert got == code, (out, err)
    return out, err


def folder(project="alpha"):
    return projects.folder(project)


def report(monkeypatch, capsys, session, text="done", project="alpha"):
    monkeypatch.setattr(ops, "current_session", lambda: (f"{session}-1", project, session))
    (folder(project) / "results").mkdir(exist_ok=True)
    (folder(project) / f"results/{session}.md").write_text("result")
    out, _ = ag(capsys, "coordinator", "task", "report", text)
    as_coordinator(monkeypatch)
    return out


def start(capsys, name, action, project="alpha", code=0):
    return ag(capsys, "coordinator", "task", "start", "--project", project, "--action", action, name, "--tool", "claude",
              "--cwd", "/x", "--model", "Sonnet 5.5", "--goal", f"{name}'s goal", "--accept", "tests pass",
              "--artifact", f"results/{name}.md", "--prompt", "-", stdin="Read the README first.\nThen the spec.", code=code)


def setup_plan(capsys):
    ag(capsys, "coordinator", "direction", "new", "--project", "alpha", "--text", "-", stdin="Ship the track.")
    ag(capsys, "coordinator", "plan", "milestone", "add", "Track files", "--project", "alpha")
    ag(capsys, "coordinator", "plan", "action", "add", "M2", "Layout", "--project", "alpha")
    ag(capsys, "coordinator", "plan", "action", "add", "M2", "Commands", "--project", "alpha")


def test_every_coordinator_command_exists(alpha, capsys):
    commands = [
        ("direction", "new"), ("direction", "show"), ("plan", "show"), ("plan", "milestone", "add"),
        ("plan", "action", "add"), ("plan", "action", "set"), ("plan", "ask"), ("plan", "answer"),
        ("milestone", "close"), ("note",), ("task", "start"), ("task", "report"), ("task", "accept"), ("task", "reject"), ("task", "fail"),
        ("task", "list"), ("task", "show"), ("task", "carry"), ("yield",), ("review",), ("status",),
    ]
    for command in commands:
        out, _ = ag(capsys, "coordinator", *command, "--help")
        assert f"agentop coordinator {' '.join(command)}" in out


def test_the_old_top_level_names_are_gone_and_the_report_is_under_coordinator_task(alpha, capsys, sessions, monkeypatch):
    for old in (("task", "list"), ("task", "report", "done"), ("yield", "waiting"), ("review", "--no-write")):
        _, err = ag(capsys, *old, "--project", "alpha", code=2)
        assert "invalid choice" in err
    setup_plan(capsys)
    start(capsys, "dev", "M2A1")
    prompt = sessions.prompts[0]
    assert prompt.count("task report") == prompt.count("agentop coordinator task report") == 2
    report(monkeypatch, capsys, "dev")
    assert any("dev reported round 1 of 3" in text and "agentop coordinator task accept dev" in text for _, text in sessions.sent)


def test_task_ids_come_from_the_counter_and_each_event_is_one_line(alpha, capsys, sessions, monkeypatch):
    setup_plan(capsys)
    out, _ = start(capsys, "dev", "M2A1")
    assert "Task P1_M2A1-1" in out
    with pytest.raises(pursuit.PursuitError, match="no M2A9"):
        pursuit.open_task(alpha, "x", "claude", "/x", "g", ["a"], "results/x.md", action="M2A9")
    start(capsys, "api", "M2A1")
    report(monkeypatch, capsys, "dev")
    ag(capsys, "coordinator", "task", "reject", "dev", "--feedback", "criterion 1 fails", "--project", "alpha")
    report(monkeypatch, capsys, "dev", "fixed")
    ag(capsys, "coordinator", "task", "fail", "P1_M2A1-1", "--note", "wrong split", "--project", "alpha")
    out, _ = start(capsys, "dev", "M2A1")
    assert "Task P1_M2A1-3" in out
    counter = json.loads((folder() / "track/counter.json").read_text())
    assert counter["direction"] == 1 and counter["tasks"] == {"P1_M2A1": 3} and counter["total"] == 3
    lines = [json.loads(line) for line in (folder() / "track/tasks.jsonl").read_text().splitlines()]
    assert [(e["id"], e["event"], e["round"]) for e in lines] == [
        ("P1_M2A1-1", "open", 1), ("P1_M2A1-2", "open", 1), ("P1_M2A1-1", "report", 1), ("P1_M2A1-1", "reject", 2),
        ("P1_M2A1-1", "report", 2), ("P1_M2A1-1", "fail", 2), ("P1_M2A1-3", "open", 1),
    ]
    assert all({"id", "action", "at", "status", "round"} <= e.keys() for e in lines)
    assert lines[-2]["status"] == "failed" and lines[0]["action"] == "M2A1"
    assert sessions.started == ["dev", "api", "dev"]
    assert "[task P1_M2A1-1]" in sessions.prompts[0]


def test_accept_reject_and_fail_write_the_execution_entry_and_the_action_status(alpha, capsys, sessions, monkeypatch):
    setup_plan(capsys)
    start(capsys, "dev", "M2A1")
    plan = (folder() / "track/1/PLAN.md").read_text()
    assert "## M2 Track files  [in progress]" in plan and "- M2A1 Layout  [in progress] → P1_M2A1-1" in plan
    report(monkeypatch, capsys, "dev", "12 tests pass")
    ag(capsys, "coordinator", "task", "reject", "dev", "--feedback", "docs missing", "--project", "alpha")
    execution = (folder() / "track/1/EXECUTION_M2.md").read_text()
    assert "## P1_M2A1-1  session `dev`  claude (Sonnet 5.5)" in execution and "round 1 rejected: docs missing" in execution
    report(monkeypatch, capsys, "dev", "docs added")
    out, _ = ag(capsys, "coordinator", "task", "accept", "dev", "--checked", "ran pytest; read the docs", "--project", "alpha")
    assert "Recorded in track/1/EXECUTION_M2.md; M2A1 is done" in out
    execution = (folder() / "track/1/EXECUTION_M2.md").read_text()
    assert execution.count("## P1_M2A1-1") == 1
    for part in ("rounds 2 · accepted", "Input: dev's goal", "- tests pass", "Artifact: results/dev.md",
                 "Brief: Read the README first. / Then the spec.", "- round 1: 12 tests pass", "- round 2: docs added",
                 "- round 2 accepted: ran pytest; read the docs"):
        assert part in execution, part
    assert "- M2A1 Layout  [done] → P1_M2A1-1" in (folder() / "track/1/PLAN.md").read_text()

    start(capsys, "api", "M2A2")
    ag(capsys, "coordinator", "task", "fail", "api", "--note", "the API was wrong", "--project", "alpha")
    assert "- M2A2 Commands  [planned] → P1_M2A2-1 (failed)" in (folder() / "track/1/PLAN.md").read_text()
    assert "- round 1 failed: the API was wrong" in (folder() / "track/1/EXECUTION_M2.md").read_text()


def test_files_of_older_directions_are_never_written(alpha, capsys, sessions, monkeypatch):
    setup_plan(capsys)
    start(capsys, "dev", "M2A1")
    _, err = ag(capsys, "coordinator", "direction", "new", "--project", "alpha", "--text", "Something else", code=1)
    assert "Tasks of direction 1 are open: P1_M2A1-1" in err and "--keep-open" in err
    ag(capsys, "coordinator", "direction", "new", "--project", "alpha", "--text", "Something else", "--copy-plan", "--keep-open")
    old = {p.name: p.read_text() for p in (folder() / "track/1").iterdir()}
    assert track.generation(alpha) == 2 and "## M2 Track files" in (folder() / "track/2/PLAN.md").read_text()
    assert (folder() / "track/2/PLAN.md").read_text().startswith("# Plan 2 (from Direction 2)")

    _, err = ag(capsys, "coordinator", "plan", "action", "set", "P1_M2A2", "--status", "dropped", "--reason", "x",
                "--project", "alpha", code=1)
    assert "never written again" in err
    report(monkeypatch, capsys, "dev")
    _, err = ag(capsys, "coordinator", "task", "accept", "dev", "--checked", "x", "--project", "alpha", code=1)
    assert "Carry it first: agentop coordinator task carry P1_M2A1-1" in err
    _, err = ag(capsys, "coordinator", "yield", "waiting", "--project", "alpha", code=1)
    assert "belongs to direction 1, not carried: agentop coordinator task carry P1_M2A1-1" in err
    out, _ = ag(capsys, "coordinator", "task", "carry", "P1_M2A1-1", "--to", "M2A1", "--project", "alpha")
    assert "Carried P1_M2A1-1 to P2_M2A1-1" in out
    ag(capsys, "coordinator", "task", "accept", "dev", "--checked", "x", "--project", "alpha")
    assert {p.name: p.read_text() for p in (folder() / "track/1").iterdir()} == old
    execution = (folder() / "track/2/EXECUTION_M2.md").read_text()
    assert "## P2_M2A1-1" in execution and "Carried from P1_M2A1-1." in execution
    assert track.task_states(alpha)["P1_M2A1-1"]["status"] == "carried"
    with pytest.raises(track.TrackError, match="never written again"):
        track._writable(alpha, 1)


def test_yield_refuses_each_case_and_names_the_fix(alpha, capsys, sessions, monkeypatch):
    def refused(*argv):
        return ag(capsys, "coordinator", "yield", *argv, "--project", "alpha", code=1)[1]

    import shutil

    shutil.rmtree(track.root(alpha))
    assert "no track/ yet: run agentop coordinator direction new" in refused("blocked", "--need", "a", "--tried", "b")
    coordinator.write_instructions(projects.get(alpha))
    assert "has no direction text: write it under '## Direction' in track/1/DIRECTION.md" in refused(
        "blocked", "--need", "a", "--tried", "b")
    # Asking the owner for the direction is the fix, so `owner` passes, and becomes an ask in the plan.
    ag(capsys, "coordinator", "yield", "owner", "--question", "Goal?", "--recommend", "R", "--default", "D", "--project", "alpha")
    setup_plan(capsys)

    pursuit.open_task(alpha, "loose", "claude", "/x", "g", ["a"], "results/loose.md")
    assert "has no action: agentop coordinator task carry t1 --to" in refused("waiting")
    pursuit.carry(alpha, "t1", "M2A2")
    with pytest.raises(pursuit.PursuitError, match="already in the current plan"):
        pursuit.carry(alpha, "loose", "M2A1")
    pursuit.fail(alpha, "loose", "not needed")

    plan_path = folder() / "track/1/PLAN.md"
    plan_path.write_text(plan_path.read_text().replace("- M2A1 Layout  [planned]", "- M2A1 Layout  [done]"))
    assert "M2A1 is done with no execution entry: agentop coordinator note M2A1" in refused("blocked", "--need", "a", "--tried", "b")
    ag(capsys, "coordinator", "note", "M2A1", "Did it myself", "--project", "alpha")
    ag(capsys, "coordinator", "plan", "action", "set", "M2A2", "--status", "dropped", "--reason", "folded in", "--project", "alpha")
    ag(capsys, "coordinator", "plan", "answer", "M1A1", "Ship the track", "--project", "alpha")
    err = refused("blocked", "--need", "a", "--tried", "b")
    assert "Every action of M1 is done or dropped: agentop coordinator milestone close M1 --summary -" in err
    assert "Every action of M2 is done or dropped" in err
    _, err = ag(capsys, "coordinator", "milestone", "close", "M2", "--summary", "-", "--project", "alpha", stdin="", code=1)
    assert "needs --summary" in err
    ag(capsys, "coordinator", "milestone", "close", "M1", "--summary", "Direction agreed.", "--project", "alpha")
    ag(capsys, "coordinator", "milestone", "close", "M2", "--summary", "-", "--project", "alpha", stdin="Layout in.\nSee track/.")
    assert "Summary: Layout in.\nSee track/." in plan_path.read_text()

    ag(capsys, "coordinator", "plan", "milestone", "add", "More", "--project", "alpha")
    ag(capsys, "coordinator", "plan", "action", "add", "M3", "More", "--project", "alpha")
    ag(capsys, "coordinator", "plan", "action", "add", "M3", "Later", "--project", "alpha")
    start(capsys, "dev", "M3A1")
    report(monkeypatch, capsys, "dev")
    assert "dev reported and wait for you" in refused("waiting")
    ag(capsys, "coordinator", "task", "accept", "dev", "--checked", "x", "--project", "alpha")
    assert "Tasks were closed since the last review" in refused("done", "--evidence", "e")


def test_plan_commands_place_number_and_ask(alpha, capsys):
    setup_plan(capsys)
    ag(capsys, "coordinator", "plan", "milestone", "add", "First", "--before", "M2", "--project", "alpha")
    ag(capsys, "coordinator", "plan", "action", "add", "M2", "Early", "--before", "M2A1", "--project", "alpha")
    _, err = ag(capsys, "coordinator", "plan", "action", "set", "M2A1", "--status", "done", "--project", "alpha", code=1)
    assert "no execution entry yet" in err
    ag(capsys, "coordinator", "plan", "action", "set", "M2A1", "--status", "done", "--note", "did it", "--project", "alpha")
    text = (folder() / "track/1/PLAN.md").read_text()
    assert text.index("## M3 First") < text.index("## M2 Track files")
    assert text.index("- M2A3 Early") < text.index("- M2A1 Layout  [done]")
    for _ in range(2):
        ag(capsys, "coordinator", "yield", "owner", "--question", "Merge hook-state?", "--recommend", "No", "--default",
           "Stays local", "--project", "alpha")
    out, _ = ag(capsys, "coordinator", "plan", "ask", "M2", "--question", "merge hook-state", "--recommend", "No",
                "--default", "Stays local", "--project", "alpha")
    assert "(asked 3×)" in out
    text = (folder() / "track/1/PLAN.md").read_text()
    assert text.count("Merge hook-state?  [waiting on owner]") == 1 and "Asked: " in text and "(3×)" in text
    out, _ = ag(capsys, "coordinator", "plan", "show", "--project", "alpha")
    assert "waiting on owner" in out


def snapshot(path):
    return {str(f.relative_to(path)): f.read_bytes() for f in sorted(path.rglob("*")) if f.is_file()}


def test_remove_only_unregisters_and_the_project_recreated_on_its_folder_resumes(alpha, capsys, sessions, monkeypatch):
    setup_plan(capsys)
    start(capsys, "dev", "M2A1")
    report(monkeypatch, capsys, "dev", "layout written")
    ag(capsys, "coordinator", "task", "accept", "dev", "--checked", "read track/", "--project", "alpha")
    start(capsys, "api", "M2A2")
    before = snapshot(folder())
    assert ".project/project.json" in before and ".project/tasks.json" in before and "track/tasks.jsonl" in before

    stopped = []
    monkeypatch.setattr(ops, "get_sessions", lambda with_meta=False: [
        {"name": "api-1", "display_name": "api", "project": "alpha"}, {"name": "x-1", "display_name": "x", "project": "beta"}])
    monkeypatch.setattr(ops, "stop", stopped.append)
    monkeypatch.setattr(sys.modules["agentop.cli"], "_projects_plugin_on", lambda: True)
    out, _ = ag(capsys, "project", "remove", "alpha")
    assert stopped == ["api-1"]
    assert "Removed alpha from the list. Nothing else in its folder was changed" in out
    assert "Its running sessions were stopped, as they would be orphaned: api." in out and "interrupted" not in out
    assert projects.get(alpha) is None and alpha not in [p["name"] for p in projects.list_projects()]
    after = snapshot(folder())
    assert after == {k: v for k, v in before.items() if k != ".project/project.json"}

    monkeypatch.setattr(ops, "get_sessions", lambda with_meta=False: [])
    found = projects.create(alpha, "projects")
    coordinator.write_instructions(found)
    prompt = coordinator.first_prompt(found)
    assert prompt == coordinator.resume_prompt(found) and "agentop coordinator status" in prompt
    assert prompt != coordinator.welcome_prompt(found)

    out, _ = ag(capsys, "coordinator", "status", "--project", "alpha")
    assert "# Direction 1" in out and "Ship the track." in out
    assert "## M2 Track files  [in progress]" in out and "- M2A1 Layout  [done] → P1_M2A1-1" in out
    assert "- M2A2 Commands  [in progress] → P1_M2A2-1" in out
    assert "## P1_M2A1-1  session `dev`" in out and "round 1 accepted: read track/" in out
    assert "- P1_M2A2-1 `api` (M2A2), round 1/3, working" in out
    ag(capsys, "coordinator", "task", "fail", "api", "--note", "its session went with the removal", "--project", "alpha")
    out, _ = start(capsys, "api", "M2A2")
    assert "Task P1_M2A2-2" in out


def test_a_new_project_welcomes_and_a_state_md_project_keeps_its_checks(alpha, capsys):
    found = projects.get(alpha)
    assert not track.started(alpha) and coordinator.first_prompt(found) == coordinator.welcome_prompt(found)
    import shutil

    shutil.rmtree(track.root(alpha))
    (folder() / "STATE.md").write_text("# State\n\n## Objective\n\nX\n")
    coordinator.write_instructions(found)
    assert not track.exists(alpha)
    _, err = ag(capsys, "coordinator", "yield", "blocked", "--need", "a", "--tried", "b", "--project", "alpha", code=1)
    assert "STATE.md has no section" in err
    out, _ = ag(capsys, "coordinator", "status", "--project", "alpha")
    assert "still keeps STATE.md" in out
    ag(capsys, "coordinator", "direction", "new", "--text", "Now on the track.", "--project", "alpha")
    assert track.generation(alpha) == 1 and track.direction_text(alpha) == "Now on the track."
    assert (folder() / "STATE.md").exists()
    out, _ = ag(capsys, "coordinator", "direction", "show", "--project", "alpha")
    assert out.startswith("# Direction 1")
