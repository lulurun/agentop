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


@pytest.fixture(autouse=True)
def no_service(monkeypatch):
    """The CLI tells the Coordinator here, not through a service on this machine."""
    monkeypatch.setattr("agentop.cli._service_api", lambda *a, **k: None)


@pytest.fixture
def sessions(monkeypatch):
    """ops faked: task start records the sessions it would start, stop and send do nothing."""
    started, prompts, sent = [], [], []
    monkeypatch.setattr(ops, "get_sessions", lambda with_meta=False: [])
    monkeypatch.setattr(ops, "start", lambda tool, cwd, name, params=None, project=None: started.append(name) or SimpleNamespace(name=f"{name}-1"))
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


def snapshot(path):
    return {str(f.relative_to(path)): f.read_bytes() for f in sorted(path.rglob("*")) if f.is_file()}


def folder(project="alpha"):
    return projects.folder(project)


def report(monkeypatch, capsys, session, text="done", project="alpha"):
    monkeypatch.setattr(ops, "current_session", lambda: (f"{session}-1", project, session))
    task = pursuit.task_for(project, session)
    artifact = folder(project) / (task["artifact"] if task else f"results/{session}.md")
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text("result")
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
        ("direction", "change"), ("direction", "new"), ("direction", "add"), ("direction", "show"), ("plan", "show"), ("plan", "milestone", "add"),
        ("plan", "action", "add"), ("plan", "action", "set"), ("plan", "ask"), ("plan", "answer"),
        ("milestone", "close"), ("note",), ("task", "start"), ("task", "report"), ("task", "accept"), ("task", "reject"), ("task", "fail"),
        ("task", "list"), ("task", "show"), ("yield",), ("review",), ("status",),
    ]
    for command in commands:
        out, _ = ag(capsys, "coordinator", *command, "--help")
        assert f"agentop coordinator {' '.join(command)}" in out


def test_keep_open_and_task_carry_are_gone(alpha, capsys):
    _, err = ag(capsys, "coordinator", "task", "carry", "P1_M2A1-1", "--to", "M2A1", "--project", "alpha", code=2)
    assert "invalid choice: 'carry'" in err
    _, err = ag(capsys, "coordinator", "direction", "new", "--text", "x", "--keep-open", "--project", "alpha", code=2)
    assert "unrecognized arguments: --keep-open" in err
    assert not hasattr(pursuit, "carry") and not hasattr(track, "carry")


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
    for part in ("rounds 2 · accepted", "Input: dev's goal", "- tests pass", "Artifact: results/P1_M2A1_dev.md",
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
    assert "Tasks are open: P1_M2A1-1 (dev)" in err and "direction change --text -" in err
    report(monkeypatch, capsys, "dev")
    ag(capsys, "coordinator", "task", "accept", "dev", "--checked", "x", "--project", "alpha")
    ag(capsys, "coordinator", "direction", "new", "--project", "alpha", "--text", "Something else", "--copy-plan")
    old = snapshot(folder() / "track/1")
    assert track.generation(alpha) == 2 and "## M2 Track files" in (folder() / "track/2/PLAN.md").read_text()
    assert (folder() / "track/2/PLAN.md").read_text().startswith("# Plan 2 (from Direction 2)")
    # With nothing pending, an immediate switch still writes the old plan's final state.
    plan1 = (folder() / "track/1/PLAN.md").read_text()
    assert "- M2A2 Commands  [not done (direction changed to 2)]" in plan1 and "## M2 Track files  [stopped]" in plan1
    assert "- M2A1 Layout  [done]" in plan1

    _, err = ag(capsys, "coordinator", "plan", "action", "set", "P1_M2A2", "--status", "dropped", "--reason", "x",
                "--project", "alpha", code=1)
    assert "never written again" in err
    start(capsys, "dev", "M2A2")
    report(monkeypatch, capsys, "dev")
    ag(capsys, "coordinator", "task", "accept", "dev", "--checked", "x", "--project", "alpha")
    assert snapshot(folder() / "track/1") == old
    assert "## P2_M2A2-1" in (folder() / "track/2/EXECUTION_M2.md").read_text()
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
    assert "dev reported and wait for you" in refused("waiting", "--next", "Wait for dev, then check its result.")
    ag(capsys, "coordinator", "task", "accept", "dev", "--checked", "x", "--project", "alpha")
    assert "Tasks were closed since the last review" in refused("done", "--evidence", "e", "--next", "Wait for dev, then check its result.")


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


def test_a_reworded_owner_yield_reuses_the_open_ask(alpha, capsys):
    setup_plan(capsys)
    first = ("M5 (direction change) is accepted and installed (cebe5d8). May I merge coordinator-track (M1-M5, local) "
             "into main and push to github lulurun/agentop (M4A2)?")
    again = "May I merge coordinator-track (M1-M5, local) into main and push to github lulurun/agentop (M4A2)?"
    out, _ = ag(capsys, "coordinator", "yield", "owner", "--question", first, "--recommend", "Yes", "--default", "Hold",
                "--project", "alpha")
    ref = out.split("Recorded as ")[1].split(",")[0]
    out, _ = ag(capsys, "coordinator", "yield", "owner", "--question", again, "--recommend", "Yes", "--default", "Hold",
                "--project", "alpha")
    assert f"Recorded as {ref}, waiting on owner, in the plan, asked 2×" in out
    text = (folder() / "track/1/PLAN.md").read_text()
    assert text.count("[waiting on owner]") == 1 and f"Question: {again}" in text
    out, _ = ag(capsys, "coordinator", "yield", "owner", "--question", "May I merge?", "--recommend", "Yes",
                "--default", "Hold", "--project", "alpha")
    assert ref not in out and (folder() / "track/1/PLAN.md").read_text().count("[waiting on owner]") == 2


def test_only_the_coordinator_closes_once_no_task_is_open_and_create_loads_the_folder_again(alpha, capsys, sessions,
                                                                                         monkeypatch):
    setup_plan(capsys)
    start(capsys, "dev", "M2A1")
    report(monkeypatch, capsys, "dev", "layout written")
    ag(capsys, "coordinator", "task", "accept", "dev", "--checked", "read track/", "--project", "alpha")
    start(capsys, "api", "M2A2")
    (folder() / "code/repo").mkdir(parents=True)
    (folder() / "code/repo/main.py").write_text("print('hi')\n")

    stopped = []
    live = [{"name": "coord-1", "display_name": "Coordinator", "project": "alpha", "managed": True, "tool": "claude"},
            {"name": "api-1", "display_name": "api", "project": "alpha", "managed": True, "tool": "claude"}]
    monkeypatch.setattr(ops, "get_sessions", lambda with_meta=False: live)
    monkeypatch.setattr(ops, "stop", stopped.append)
    monkeypatch.setattr(ops, "stop_detached", lambda name, sessions: stopped.append((name, sessions)))
    monkeypatch.setattr(sys.modules["agentop.cli"], "_projects_plugin_on", lambda: True)
    ag(capsys, "project", "remove", "alpha", code=2)
    ag(capsys, "project", "close", "alpha", "--delete-folder", code=2)

    # Not from another session, nor from outside one.
    monkeypatch.setattr(ops, "current_session", lambda: ("api-1", "alpha", "api"))
    _, err = ag(capsys, "project", "close", "alpha", code=1)
    assert "only alpha's Coordinator closes it" in err
    monkeypatch.setattr(ops, "current_session", lambda: None)
    ag(capsys, "project", "close", "alpha", code=1)
    as_coordinator(monkeypatch)

    # It waits for the running execution, which is closed as usual.
    _, err = ag(capsys, "project", "close", "alpha", code=1)
    assert "tasks are open: P1_M2A2-1 (api)" in err
    assert pursuit.close_request(alpha)
    _, err = start(capsys, "new", "M2A1", code=1)
    assert "project close is pending" in err
    report(monkeypatch, capsys, "api", "commands written")
    _, err = ag(capsys, "project", "close", "alpha", code=1)
    assert "tasks are open" in err
    ag(capsys, "coordinator", "task", "accept", "api", "--checked", "ran them", "--project", "alpha")
    assert track.find_action(track.load_plan(alpha), 2, 2)["status"] == "done"
    assert "commands written" in track.execution_path(alpha, 1, 2).read_text()
    assert track.problems(alpha, [])  # An unclosed milestone must not block closing.
    # Only accept stopped a session, as it always does.
    assert projects.get(alpha) is not None and stopped == ["api-1"]
    stopped.clear()

    before = snapshot(folder())
    out, _ = ag(capsys, "project", "close", "alpha", "--note", "shipped the track")
    # Nothing else is stopped; the Coordinator's own session ends last, from a process of its own.
    assert stopped == [("coord-1", live)]
    assert "Closed alpha: marked in track/1/CLOSED.md, and gone from agentop project list." in out
    assert f"Its folder, files and other sessions are kept as they are: {folder()}" in out
    assert out.rstrip().endswith("Closing this session now.")
    assert projects.get(alpha) is None and alpha not in [p["name"] for p in projects.list_projects()]
    after = snapshot(folder())
    mark = after.pop("track/1/CLOSED.md").decode()
    assert {k: v for k, v in after.items() if k != ".project/events.jsonl"} == {
        k: v for k, v in before.items() if k not in (".project/project.json", ".project/events.jsonl")}
    assert pursuit.close_request(alpha) is None
    assert mark.splitlines()[-1].endswith("closed by `Coordinator` of alpha: shipped the track")

    # A closed project's status still reads, and shows the mark; reading writes nothing.
    out, _ = ag(capsys, "coordinator", "status", "--project", "alpha")
    assert out.startswith("# Closed (track/1/CLOSED.md): closed now") and "shipped the track" in out
    assert "# Direction 1" in out and snapshot(folder()) == {**after, "track/1/CLOSED.md": mark.encode()}
    ag(capsys, "coordinator", "plan", "show", "--project", "alpha", code=1)

    monkeypatch.setattr(ops, "get_sessions", lambda with_meta=False: [])
    monkeypatch.setattr("agentop.agentclis.installed", lambda agents: [SimpleNamespace(name="claude")])
    out, _ = ag(capsys, "project", "create", "alpha")
    assert f"Project alpha, loaded from its kept folder and marked reopened: {folder()}" in out
    assert sessions.started[-1] == projects.COORDINATOR
    assert sessions.prompts[-1] == coordinator.resume_prompt(projects.get(alpha))
    reloaded = snapshot(folder())
    assert reloaded.pop("track/1/CLOSED.md").decode().splitlines()[-1].endswith(
        "reopened by the owner, with agentop project create")
    assert {k: v for k, v in reloaded.items() if k not in (".project/project.json", ".project/events.jsonl")} == {
        k: v for k, v in before.items() if k not in (".project/project.json", ".project/events.jsonl")}
    out, _ = ag(capsys, "coordinator", "status", "--project", "alpha")
    assert out.startswith("# Closed (track/1/CLOSED.md): reopened now")
    assert "## M2 Track files" in out
    assert "# Priority close request" not in out


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


def test_direction_add_appends_numbered_addenda_and_status_shows_them(alpha, capsys, monkeypatch):
    _, err = ag(capsys, "coordinator", "direction", "add", "--project", "alpha", "--text", "Early", code=1)
    assert "has no direction text yet" in err
    setup_plan(capsys)
    monkeypatch.setattr(track.time, "time", lambda: 1795000000.0)
    out, _ = ag(capsys, "coordinator", "direction", "add", "--project", "alpha", "--text", "-",
                stdin="Also show the addenda in status.\n- one line each")
    assert "Addendum 1 is in track/1/DIRECTION.md" in out and "plan milestone add" in out
    ag(capsys, "coordinator", "plan", "action", "add", "M2", "Addenda in status", "--project", "alpha")
    ag(capsys, "coordinator", "direction", "add", "--project", "alpha", "--text", "Keep it short.", "--no-plan-change")
    when = track._when(1795000000.0)
    text = (folder() / "track/1/DIRECTION.md").read_text()
    assert text.count("## Addenda") == 1
    assert f"## Addenda\n\n### Addendum 1 ({when})\n\nAlso show the addenda in status.\n- one line each\n" in text
    assert f"### Addendum 2 ({when})\n\nKeep it short.\n\nPlan: no change needed" in text
    assert track.direction_text(alpha) == "Ship the track."

    # k never goes back, even when an addendum is deleted by hand.
    path = folder() / "track/1/DIRECTION.md"
    path.write_text(text.split("### Addendum 2")[0])
    ag(capsys, "coordinator", "direction", "add", "--project", "alpha", "--text", "Third.", "--no-plan-change")
    assert "### Addendum 3 (" in path.read_text() and "### Addendum 2" not in path.read_text()

    out, _ = ag(capsys, "coordinator", "direction", "show", "--project", "alpha")
    assert "### Addendum 1 (" in out and "### Addendum 3 (" in out
    out, _ = ag(capsys, "coordinator", "status", "--project", "alpha")
    assert "## Addenda" in out and "Also show the addenda in status." in out and "Third." in out

    _, err = ag(capsys, "coordinator", "direction", "add", "--project", "alpha", "--text", "## Heading", code=1)
    assert "headings" in err
    _, err = ag(capsys, "coordinator", "direction", "add", "--project", "alpha", code=1)
    assert "--text -" in err


def test_direction_add_refuses_older_directions_and_a_new_direction_starts_with_none(alpha, capsys):
    setup_plan(capsys)
    ag(capsys, "coordinator", "direction", "add", "--project", "alpha", "--text", "More.")
    ag(capsys, "coordinator", "direction", "new", "--project", "alpha", "--text", "Something else")
    old = (folder() / "track/1/DIRECTION.md").read_text()
    _, err = ag(capsys, "coordinator", "direction", "add", "--project", "alpha", "--direction", "1", "--text", "x", code=1)
    assert "track/1 belongs to an older direction (the current one is 2); it is never written again" in err
    assert (folder() / "track/1/DIRECTION.md").read_text() == old
    assert "Addendum" not in (folder() / "track/2/DIRECTION.md").read_text()
    # The pending plan change belonged to direction 1; direction 2 doesn't inherit it.
    assert not any("Addendum" in p for p in pursuit.problems(alpha))
    ag(capsys, "coordinator", "direction", "add", "--project", "alpha", "--direction", "2", "--text", "y", "--no-plan-change")
    assert "### Addendum 1 (" in (folder() / "track/2/DIRECTION.md").read_text()


@pytest.mark.parametrize("fix", [
    ("plan", "milestone", "add", "Addenda"),
    ("plan", "action", "add", "M2", "Addenda"),
    ("plan", "action", "set", "M2A2", "--status", "blocked"),
    ("direction", "add", "--no-plan-change"),
])
def test_yield_refuses_after_an_addendum_until_the_plan_changed(alpha, capsys, fix):
    setup_plan(capsys)
    blocked = ("coordinator", "yield", "blocked", "--need", "a", "--tried", "b", "--project", "alpha")
    ag(capsys, "coordinator", "plan", "action", "add", "M2", "Before", "--project", "alpha")
    ag(capsys, "coordinator", "direction", "add", "--project", "alpha", "--text", "Also export CSV.")
    _, err = ag(capsys, *blocked, code=1)
    assert "Addendum 1 of direction 1 hasn't changed the plan yet" in err
    assert "plan milestone add, action add or action set" in err and "direction add --no-plan-change" in err
    out, _ = ag(capsys, "coordinator", "status", "--project", "alpha")
    assert "Addendum 1 of direction 1 hasn't changed the plan yet" in out
    # Showing the plan, closing or asking isn't a change made for the addendum.
    ag(capsys, "coordinator", "plan", "show", "--project", "alpha")
    ag(capsys, "coordinator", "note", "M2A1", "x", "--project", "alpha")
    ag(capsys, *blocked, code=1)
    ag(capsys, "coordinator", *fix, "--project", "alpha")
    ag(capsys, *blocked)
    if fix[0] == "direction":
        assert "Plan: no change needed" in (folder() / "track/1/DIRECTION.md").read_text().split("### Addendum 1")[1]
    events = [e for e in pursuit.events(alpha) if e["kind"] == "direction.addendum"]
    assert events[0]["addendum"] == 1 and events[0]["plan_change"] is True


def test_no_plan_change_alone_needs_a_pending_addendum(alpha, capsys):
    setup_plan(capsys)
    _, err = ag(capsys, "coordinator", "direction", "add", "--no-plan-change", "--project", "alpha", code=1)
    assert "No addendum waits for a plan change" in err


CHANGE = "Pivot to the CSV export.\n\nDone means: every report downloads as CSV."


def test_direction_change_is_the_plans_first_action_and_a_second_one_replaces_it(alpha, capsys, sessions, monkeypatch):
    setup_plan(capsys)
    start(capsys, "dev", "M2A1")
    start(capsys, "api", "M2A2")
    out, _ = ag(capsys, "coordinator", "direction", "change", "--text", "-", "--project", "alpha", stdin=CHANGE)
    assert "Added the direction change as M3A1, first in the plan" in out
    assert "It waits for the open tasks: P1_M2A1-1 (dev), P1_M2A2-1 (api)" in out
    plan = track.load_plan(alpha)
    first = plan["milestones"][0]
    assert (first["m"], first["title"], first["status"]) == (3, "Direction change", "in progress")
    action = first["actions"][0]
    assert action["title"] == "Change direction: Pivot to the CSV export." and action["status"] == "pending (waits for 2 open tasks)"
    assert track.change_text(action) == CHANGE
    text = (folder() / "track/1/PLAN.md").read_text()
    assert text.index("## M3 Direction change") < text.index("## M1 ")
    assert "  > Pivot to the CSV export.\n  >\n  > Done means: every report downloads as CSV." in text

    out, _ = ag(capsys, "coordinator", "direction", "change", "--text", "Pivot to PDF instead.", "--project", "alpha")
    assert "Replaced the pending direction change as M3A1" in out
    plan = track.load_plan(alpha)
    changes = [a for m in plan["milestones"] for a in m["actions"] if a["title"].startswith("Change direction:")]
    assert len(changes) == 1 and track.change_text(changes[0]) == "Pivot to PDF instead."
    assert [m["title"] for m in plan["milestones"]].count("Direction change") == 1
    events = [e for e in pursuit.events(alpha) if e["kind"] == "direction.change"]
    assert [e["replaced"] for e in events] == [False, True] and events[0]["waits"] == ["P1_M2A1-1", "P1_M2A2-1"]


def test_a_direction_change_needs_a_direction_and_text(alpha, capsys):
    _, err = ag(capsys, "coordinator", "direction", "change", "--text", "x", "--project", "alpha", code=1)
    assert "no direction to change yet" in err
    setup_plan(capsys)
    _, err = ag(capsys, "coordinator", "direction", "change", "--text", "-", "--project", "alpha", stdin=" \n", code=1)
    assert "needs --text" in err
    _, err = ag(capsys, "coordinator", "direction", "new", "--project", "alpha", code=1)
    assert "A direction needs --text" in err


def test_a_pending_change_waits_for_open_tasks_then_is_all_that_is_left(alpha, capsys, sessions, monkeypatch):
    from agentop.plugins.projects import wake

    def refused(*argv):
        return ag(capsys, "coordinator", "yield", *argv, "--project", "alpha", code=1)[1]

    setup_plan(capsys)
    start(capsys, "dev", "M2A1")
    start(capsys, "api", "M2A2")
    ag(capsys, "coordinator", "direction", "change", "--text", "-", "--project", "alpha", stdin=CHANGE)

    _, err = start(capsys, "docs", "M2A1", code=1)
    assert "No new task starts: a direction change is pending (M3A1)" in err
    assert "P1_M2A1-1 (dev), P1_M2A2-1 (api)" in err and sessions.started == ["dev", "api"]

    out, _ = ag(capsys, "coordinator", "status", "--project", "alpha")
    assert out.startswith("# Direction change pending: M3A1, before anything else")
    head = out.split("# Next")[0]
    assert "Done means: every report downloads as CSV." in head
    assert "- P1_M2A1-1 `dev`" in head and "- P1_M2A2-1 `api`" in head

    # Running tasks finish as usual, and waiting for them is a fine yield.
    report(monkeypatch, capsys, "dev")
    ag(capsys, "coordinator", "task", "reject", "dev", "--feedback", "criterion 1 fails", "--project", "alpha")
    report(monkeypatch, capsys, "dev", "fixed")
    out, _ = ag(capsys, "coordinator", "task", "accept", "dev", "--checked", "x", "--project", "alpha")
    assert "direction new" not in out and "M2A1 is done" in out
    assert "[pending (waits for 1 open task)]" in (folder() / "track/1/PLAN.md").read_text()
    assert pursuit.change_notice(alpha) == ""
    ag(capsys, "coordinator", "yield", "waiting", "--next", "Wait for api, then make the direction change.", "--project", "alpha")

    report(monkeypatch, capsys, "api")
    out, _ = ag(capsys, "coordinator", "task", "fail", "api", "--note", "not needed now", "--project", "alpha")
    assert "Next: the direction change; nothing else comes first." in out
    assert "no task is open: run agentop coordinator direction new now" in out
    assert "[pending (no task open: run direction new)]" in (folder() / "track/1/PLAN.md").read_text()

    for argv in (("waiting", "--next", "Wait for nothing at all, really now."), ("blocked", "--need", "a", "--tried", "b"),
                 ("done", "--evidence", "e", "--next", "Make the direction change, then plan it.")):
        assert "A direction change is pending (M3A1) and no task is open: run agentop coordinator direction new" in refused(*argv)
    ag(capsys, "coordinator", "yield", "owner", "--question", "Keep the PDF work?", "--recommend", "No", "--default", "Drop it",
       "--project", "alpha")
    assert "Keep the PDF work?" not in "".join(
        track.action_line(a) for a in track.load_plan(alpha)["milestones"][0]["actions"])

    assert "direction new now; it takes the pending text" in pursuit.wake_note(alpha)
    prompt = wake.compose(alpha, wake.CLEARED, "[owner] hello")
    assert prompt.index("direction new now") < prompt.index("# Next") < prompt.index("[owner] hello")
    _, err = start(capsys, "docs", "M2A1", code=1)
    assert "no task is open: run agentop coordinator direction new now" in err


def test_direction_new_finishes_the_old_plan_with_the_pending_text(alpha, capsys, sessions, monkeypatch):
    setup_plan(capsys)
    ag(capsys, "coordinator", "plan", "action", "add", "M2", "Docs", "--project", "alpha")
    ag(capsys, "coordinator", "plan", "action", "set", "M2A3", "--status", "dropped", "--reason", "later", "--project", "alpha")
    ag(capsys, "coordinator", "plan", "milestone", "add", "Release", "--project", "alpha")
    ag(capsys, "coordinator", "plan", "milestone", "add", "Done one", "--project", "alpha")
    ag(capsys, "coordinator", "plan", "action", "add", "M4", "Small", "--project", "alpha")
    ag(capsys, "coordinator", "plan", "action", "set", "M4A1", "--status", "done", "--note", "did it", "--project", "alpha")
    ag(capsys, "coordinator", "milestone", "close", "M4", "--summary", "Small done.", "--project", "alpha")
    start(capsys, "dev", "M2A1")
    ag(capsys, "coordinator", "direction", "change", "--text", "-", "--project", "alpha", stdin=CHANGE)
    _, err = ag(capsys, "coordinator", "direction", "new", "--project", "alpha", code=1)
    assert "Tasks are open: P1_M2A1-1 (dev)" in err and track.generation(alpha) == 1
    report(monkeypatch, capsys, "dev")
    ag(capsys, "coordinator", "task", "accept", "dev", "--checked", "x", "--project", "alpha")

    out, _ = ag(capsys, "coordinator", "direction", "new", "--project", "alpha")
    assert "Plan 1's final state is in track/1/PLAN.md" in out and "Direction 2:" in out
    assert track.generation(alpha) == 2 and track.direction_text(alpha) == CHANGE
    plan1 = (folder() / "track/1/PLAN.md").read_text()
    for line in ("## M5 Direction change  [done]", "Summary: Direction changed to 2.",
                 "- M5A1 Change direction: Pivot to the CSV export.  [done]",
                 "## M1 Agree the direction with the owner  [stopped]", "## M2 Track files  [stopped]",
                 "- M2A1 Layout  [done] → P1_M2A1-1", "- M2A2 Commands  [not done (direction changed to 2)]",
                 "- M2A3 Docs  [dropped (later)]", "## M3 Release  [stopped]", "## M4 Done one  [done]", "- M4A1 Small  [done]"):
        assert line in plan1, line
    assert "Direction changed to 2: track/2/DIRECTION.md." in (folder() / "track/1/EXECUTION_M5.md").read_text()
    assert (folder() / "track/2/PLAN.md").read_text().strip().endswith("Date: " + track._today())
    assert any(e["kind"] == "direction.new" and e["direction"] == 2 for e in pursuit.events(alpha))

    # Direction 2 starts clean, and nothing done in it touches direction 1.
    old = snapshot(folder() / "track/1")
    out, _ = ag(capsys, "coordinator", "status", "--project", "alpha")
    assert not out.startswith("# Direction change pending") and "# Direction 2" in out
    ag(capsys, "coordinator", "plan", "milestone", "add", "CSV", "--project", "alpha")
    ag(capsys, "coordinator", "plan", "action", "add", "M1", "Export", "--project", "alpha")
    start(capsys, "csv", "M1A1")
    report(monkeypatch, capsys, "csv")
    ag(capsys, "coordinator", "task", "accept", "csv", "--checked", "x", "--project", "alpha")
    ag(capsys, "coordinator", "note", "M1A1", "More", "--project", "alpha")
    ag(capsys, "coordinator", "direction", "add", "--text", "Also XLSX.", "--no-plan-change", "--project", "alpha")
    ag(capsys, "coordinator", "direction", "change", "--text", "Back to PDF.", "--project", "alpha")
    assert snapshot(folder() / "track/1") == old


def test_copy_plan_leaves_the_change_behind_and_dropping_it_cancels_the_change(alpha, capsys, sessions):
    setup_plan(capsys)
    ag(capsys, "coordinator", "direction", "change", "--text", "Pivot.", "--project", "alpha")
    ag(capsys, "coordinator", "direction", "new", "--copy-plan", "--project", "alpha")
    plan2 = (folder() / "track/2/PLAN.md").read_text()
    assert "## M2 Track files  [planned]" in plan2 and "Direction change" not in plan2 and "not done" not in plan2

    ag(capsys, "coordinator", "direction", "change", "--text", "Pivot again.", "--project", "alpha")
    ag(capsys, "coordinator", "plan", "action", "set", "M3A1", "--status", "dropped", "--reason", "owner withdrew it",
       "--project", "alpha")
    assert track.current_change(alpha) is None
    start(capsys, "dev", "M2A1")


def test_the_current_milestone_is_the_work_not_the_pending_change(alpha, capsys, sessions):
    setup_plan(capsys)
    start(capsys, "dev", "M2A1")
    ag(capsys, "coordinator", "direction", "change", "--text", "Pivot.", "--project", "alpha")
    assert track.current_milestone(track.load_plan(alpha))["m"] == 2
    out, _ = ag(capsys, "coordinator", "status", "--project", "alpha")
    assert "# Execution of M2, the current milestone" in out and "## P1_M2A1-1" in out


def test_priority_close_survives_status_and_clear_and_drains_normal_decisions(alpha, capsys, sessions, monkeypatch):
    from agentop.plugins.projects import wake

    setup_plan(capsys)
    start(capsys, "dev", "M2A1")
    start(capsys, "api", "M2A2")
    ag(capsys, "coordinator", "direction", "change", "--text", "Unrelated pending change")
    ag(capsys, "coordinator", "close", "--note", "Owner is finished here")
    request = pursuit.close_request(alpha)
    ag(capsys, "coordinator", "close")
    assert pursuit.close_request(alpha) == request
    out, _ = ag(capsys, "coordinator", "status")
    assert out.startswith("# Priority close request") and "Owner is finished here" in out
    assert "# Direction change pending" not in out
    prompt = wake.compose(alpha, "Context cleared")
    assert "Priority: close" in prompt
    assert "direction new now" not in prompt
    pursuit.log(alpha, "clear.verified")
    assert "Priority: close" in pursuit.wake_note(alpha)
    ag(capsys, "coordinator", "yield", "waiting", "--next", "Drain existing reports, then close the project.")
    _, err = start(capsys, "other", "M2A1", code=1)
    assert "project close is pending" in err
    _, err = ag(capsys, "start", "other", "--tool", "claude", "--cwd", "/tmp", "--untracked", code=1)
    assert "project close is pending" in err
    report(monkeypatch, capsys, "dev")
    ag(capsys, "coordinator", "task", "accept", "dev", "--checked", "read result")
    ag(capsys, "coordinator", "task", "fail", "api", "--note", "Not needed for close")
    plan = track.load_plan(alpha)
    assert track.find_action(plan, 2, 1)["status"] == "done"
    assert track.find_action(plan, 2, 2)["status"] == "planned"
    assert "Not needed for close" in track.execution_path(alpha, 1, 2).read_text()
    assert "No task is open: run agentop project close alpha now" in cli.status_text(alpha)
    stopped = []
    monkeypatch.setattr(ops, "stop_detached", lambda *args: stopped.append(projects.get(alpha)))
    ag(capsys, "project", "close", "alpha")
    assert stopped == [None]
    assert (folder() / "track/1/PLAN.md").is_file()


def test_a_stale_task_start_cannot_open_an_execution_after_final_close(alpha):
    projects.close(alpha)
    with pytest.raises(pursuit.PursuitError, match="No project called alpha"):
        pursuit.open_task(alpha, "late", "codex", "/tmp", "Late work", ["done"], "results/late.md")
    assert pursuit.open_tasks(alpha) == []
