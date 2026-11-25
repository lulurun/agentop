"""A Coordinator's tasks, stop reasons, idle checks and review (docs/0_13_projects.md, section 4.3)."""

import os
import time

import pytest

from agentop import coordinator, projects
from agentop.plugins.attention import Watcher
from agentop.plugins.projects import cli, pursuit, review, track
from agentop.plugins.projects.checker import Checker


@pytest.fixture
def alpha(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTOP_HOME", str(tmp_path))
    found = projects.create("alpha", "projects")
    coordinator.write_instructions(found)
    return "alpha"


def write(project, rel, text="result"):
    path = projects.folder(project) / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


STATE_TEMPLATE = """# State of alpha

## Objective

(What the project is for, and what "done" means.)

## Where things are

## Decisions

## Open questions for the owner

## Next
"""


def legacy(project):
    """A project from before the track: STATE.md, and no track/."""
    import shutil

    shutil.rmtree(track.root(project))
    (projects.folder(project) / pursuit.STATE_FILE).write_text(STATE_TEMPLATE)


def touch_state(project):
    path = projects.folder(project) / pursuit.STATE_FILE
    path.write_text(path.read_text().replace(pursuit.OBJECTIVE_PLACEHOLDER, "Ship the thing."))
    later = time.time() + 1
    os.utime(path, (later, later))


def direct(project):
    """Direction 1 written, and M1 with one action, M1A1."""
    pursuit.new_direction(project, "Ship the thing.")
    with pursuit._locked(project):
        track.add_action(project, "M1", "Add login")


def test_a_task_needs_a_goal_criteria_and_an_artifact_in_the_project(alpha):
    with pytest.raises(pursuit.PursuitError, match="--accept"):
        pursuit.open_task(alpha, "dev", "claude", "/x", "Add login", [" "], "results/dev.md")
    with pytest.raises(pursuit.PursuitError, match="inside the project folder"):
        pursuit.open_task(alpha, "dev", "claude", "/x", "Add login", ["tests pass"], "/etc/passwd")
    with pytest.raises(pursuit.PursuitError, match="not .project"):
        pursuit.open_task(alpha, "dev", "claude", "/x", "Add login", ["tests pass"], ".project/tasks.json")
    task = pursuit.open_task(alpha, "dev", "claude", "/x", "Add login", ["tests pass"], "results/dev.md")
    assert task["id"] == "t1" and pursuit.awaiting_report(task)
    with pytest.raises(pursuit.PursuitError, match="already has an open task"):
        pursuit.open_task(alpha, "DEV", "claude", "/x", "Again", ["x"], "results/x.md")


def test_three_rounds_then_only_fail(alpha):
    pursuit.open_task(alpha, "dev", "claude", "/x", "Add login", ["tests pass"], "results/dev.md")
    with pytest.raises(pursuit.PursuitError, match="does not exist"):
        pursuit.report(alpha, "dev", "done")
    with pytest.raises(pursuit.PursuitError, match="not reported"):
        pursuit.reject(alpha, "dev", "more")
    pursuit.report(alpha, "dev", "stuck on a password prompt", blocked=True)
    pursuit.reject(alpha, "dev", "use the test account")
    write(alpha, "results/dev.md")
    pursuit.report(alpha, "dev", "done, 12 tests pass")
    with pytest.raises(pursuit.PursuitError, match="--checked"):
        pursuit.accept(alpha, "dev", "")
    pursuit.reject(alpha, "dev", "criterion 1 fails")
    pursuit.report(alpha, "dev", "fixed")
    with pytest.raises(pursuit.PursuitError, match="all 3 rounds"):
        pursuit.reject(alpha, "dev", "still wrong")
    task = pursuit.fail(alpha, "dev", "the split was wrong; replanning")
    assert task["status"] == "failed" and len(task["rounds"]) == 3
    assert [e["kind"] for e in pursuit.events(alpha)][-1] == "task.fail"


def test_yield_checks_each_reason_with_state_md_before_the_track(alpha):
    legacy(alpha)
    with pytest.raises(pursuit.PursuitError, match="--recommend and --default"):
        pursuit.yield_turn(alpha, "owner", {"question": "Which?"})
    with pytest.raises(pursuit.PursuitError, match="Nothing is pending"):
        pursuit.yield_turn(alpha, "waiting", {})
    pursuit.yield_turn(alpha, "owner", {"question": "Goal?", "recommend": "Tell me", "default": "Nothing to pursue yet"})

    pursuit.open_task(alpha, "dev", "claude", "/x", "Add login", ["tests pass"], "results/dev.md")
    with pytest.raises(pursuit.PursuitError, match="no objective"):
        pursuit.yield_turn(alpha, "waiting", {})
    touch_state(alpha)
    assert pursuit.yield_turn(alpha, "waiting", {})["sessions"] == "dev"
    with pytest.raises(pursuit.PursuitError, match="still open"):
        pursuit.yield_turn(alpha, "done", {"evidence": "it works"})

    write(alpha, "results/dev.md")
    pursuit.report(alpha, "dev", "done")
    with pytest.raises(pursuit.PursuitError, match="reported and wait for you"):
        pursuit.yield_turn(alpha, "waiting", {})
    pursuit.accept(alpha, "dev", "ran the tests")
    state = projects.folder(alpha) / pursuit.STATE_FILE
    os.utime(state, (time.time() - 100, time.time() - 100))
    with pytest.raises(pursuit.PursuitError, match="before the latest task was closed"):
        pursuit.yield_turn(alpha, "done", {"evidence": "it works"})
    touch_state(alpha)
    with pytest.raises(pursuit.PursuitError, match="since the last review"):
        pursuit.yield_turn(alpha, "done", {"evidence": "it works"})
    _, path, _ = review.run(alpha)
    with pytest.raises(pursuit.PursuitError, match="reflection"):
        pursuit.yield_turn(alpha, "done", {"evidence": "it works"})
    path.write_text(path.read_text().replace(pursuit.REFLECTION_PLACEHOLDER, "I should have split it earlier."))
    assert pursuit.yield_turn(alpha, "done", {"evidence": "it works"})["type"] == "done"


def test_review_counts_stops_and_shows_the_trend(alpha):
    for _ in range(3):
        pursuit.yield_turn(alpha, "owner", {"question": "Q?", "recommend": "R", "default": "wait"})
    text, path, m = review.run(alpha)
    assert m["stops"] == 3 and m["owner_rate"] == 1.0
    assert "of stops handed back to the owner" in text and "no default but waiting" in text
    assert "Q? Recommended: R Default: wait" in text and "First review" in text
    assert path.parent.name == "reviews" and pursuit.REFLECTION_PLACEHOLDER in path.read_text()

    pursuit.log(alpha, "yield", type="waiting")
    text, _, m = review.run(alpha)
    assert m["stops"] == 1 and m["owner_rate"] == 0.0
    assert "Handed to the owner: 100% → 0%" in text


class Screens:
    def __init__(self):
        self.text = {"%1": "a", "%2": "b"}
        self.activity = 0

    def list_panes(self):
        return {"coord-1": (self.activity, "80x24", "%1"), "dev-1": (self.activity, "80x24", "%2")}

    def capture(self, pane):
        return self.text[pane]


def rows():
    base = {"status": "running", "managed": True, "project": "alpha", "tool": "fakeagent"}
    return [
        {**base, "id": "coord-1", "display_name": "Coordinator", "tmux": {"session": "coord-1"}},
        {**base, "id": "dev-1", "display_name": "dev", "tmux": {"session": "dev-1"}},
    ]


def run_turn(checker, screens, pane, start, quiet=70):
    """A turn of output on one pane at `start`, then quiet for `quiet` seconds."""
    screens.activity = int(start)
    screens.text[pane] += "x"
    checker.tick(rows(), start)
    checker.tick(rows(), start + 5)
    checker.tick(rows(), start + quiet)


def test_a_coordinator_turn_without_a_yield_is_neither_logged_nor_prompted(alpha, monkeypatch):
    sent = []
    screens = Screens()
    checker = Checker(lambda p, row, text: sent.append((row["display_name"], text)),
                      watcher=Watcher(screens.list_panes, screens.capture))
    clock = [time.time()]
    monkeypatch.setattr(pursuit.time, "time", lambda: clock[0])

    def turn(yielding=False):
        clock[0] += 100
        screens.activity = int(clock[0])
        screens.text["%1"] += "x"
        checker.tick(rows(), clock[0])
        if yielding:
            pursuit.yield_turn(alpha, "owner", {"question": "Q?", "recommend": "R", "default": "D"})
        checker.tick(rows(), clock[0] + 5)
        checker.tick(rows(), clock[0] + 70)

    checker.tick(rows(), clock[0])
    turn()
    turn(yielding=True)
    for _ in range(3):
        turn()
    assert sent == []
    assert {e["kind"] for e in pursuit.events(alpha)} == {"yield"}


def test_yield_clear_clears_the_coordinator_then_tells_it_once_to_carry_on(alpha, monkeypatch, capsys):
    from types import SimpleNamespace

    from agentop import ops

    delivered, sent = [], []
    monkeypatch.setattr("agentop.cli.deliver", lambda target, text, project: delivered.append((target, text, project)))
    monkeypatch.setattr(ops, "current_session", lambda: ("coord-1", "alpha", "Coordinator"))
    screens = Screens()
    checker = Checker(lambda p, row, text: sent.append((row["display_name"], text)),
                      watcher=Watcher(screens.list_panes, screens.capture))
    clock = [time.time()]
    monkeypatch.setattr(pursuit.time, "time", lambda: clock[0])
    checker.tick(rows(), clock[0])

    def turn(during=None):
        clock[0] += 100
        screens.activity = int(clock[0])
        screens.text["%1"] += "x"
        checker.tick(rows(), clock[0])
        if during:
            during()
        checker.tick(rows(), clock[0] + 5)
        checker.tick(rows(), clock[0] + 70)

    fields = dict.fromkeys(("evidence", "question", "recommend", "default", "meanwhile", "need", "tried", "note"), "")
    turn(lambda: cli.run_yield(SimpleNamespace(kind="owner", project=None, clear=True, **{
        **fields, "question": "Q?", "recommend": "R", "default": "D"})))
    assert delivered == []
    assert "context is cleared" in capsys.readouterr().out
    assert sent[0] == ("Coordinator", "/clear")
    assert len(sent) == 2 and sent[1][0] == "Coordinator" and "agentop coordinator status" in sent[1][1]
    turn(lambda: pursuit.yield_turn(alpha, "owner", {"question": "Q?", "recommend": "R", "default": "D"}))
    assert len(sent) == 2
    kinds = [e["kind"] for e in pursuit.events(alpha)]
    assert kinds.count("clear") == 1 and kinds.count("resume") == 1


def test_done_and_waiting_clear_by_default_and_keep_stops_it(alpha, monkeypatch, capsys):
    from types import SimpleNamespace

    from agentop import ops

    delivered = []
    monkeypatch.setattr("agentop.cli.deliver", lambda target, text, project: delivered.append((target, text, project)))
    monkeypatch.setattr(ops, "current_session", lambda: ("coord-1", "alpha", "Coordinator"))
    direct(alpha)
    pursuit.open_task(alpha, "dev", "claude", "/x", "Add login", ["tests pass"], "results/dev.md", action="M1A1")
    fields = dict.fromkeys(("evidence", "question", "recommend", "default", "meanwhile", "need", "tried", "note"), "")

    def run(kind, **flags):
        cli.run_yield(SimpleNamespace(kind=kind, project=None, **{**fields, "question": "Q?", "recommend": "R",
                                                                   "default": "D", **flags}))

    run("waiting", clear=False, keep=False)
    assert delivered == []
    assert pursuit.pending_clear(alpha)["type"] == "waiting"
    assert "context is cleared" in capsys.readouterr().out
    run("waiting", clear=False, keep=True)
    run("owner", clear=False, keep=False)
    assert [e["kind"] for e in pursuit.events(alpha)].count("clear.pending") == 1


def test_a_yields_clear_waits_until_the_coordinators_screen_is_still(alpha, monkeypatch):
    sent = []
    screens = Screens()
    checker = Checker(lambda p, row, text: sent.append(text), watcher=Watcher(screens.list_panes, screens.capture))
    now = time.time()
    checker.tick(rows(), now)
    pursuit.log(alpha, "clear.pending", type="waiting")
    for at in range(1, 30, 2):
        screens.activity = int(now + at)
        screens.text["%1"] += "x"
        checker.tick(rows(), now + at)
    checker.tick(rows(), now + 35)
    assert sent == []
    checker.tick(rows(), now + 41)
    assert sent == ["/clear"]
    assert pursuit.pending_clear(alpha) is None
    checker.tick(rows(), now + 50)
    assert sent == ["/clear"]


def test_a_waiting_clear_is_not_resumed_and_the_next_report_says_the_context_was_cleared(alpha, monkeypatch):
    from types import SimpleNamespace

    from agentop import ops

    delivered, sent = [], []
    monkeypatch.setattr("agentop.cli.deliver", lambda target, text, project: delivered.append((target, text)))
    monkeypatch.setattr(ops, "current_session", lambda: ("coord-1", "alpha", "Coordinator"))
    direct(alpha)
    pursuit.open_task(alpha, "dev", "fakeagent", "/x", "Add login", ["tests pass"], "results/dev.md", action="M1A1")
    screens = Screens()
    checker = Checker(lambda p, row, text: sent.append(text), watcher=Watcher(screens.list_panes, screens.capture))
    now = time.time()
    checker.tick(rows(), now)
    cli.run_yield(SimpleNamespace(kind="waiting", project=None, clear=False, keep=False, **dict.fromkeys(
        ("evidence", "question", "recommend", "default", "meanwhile", "need", "tried", "note"), "")))
    run_turn(checker, screens, "%1", now + 10)
    assert sent == ["/clear"]

    monkeypatch.setattr(ops, "current_session", lambda: ("dev-1", "alpha", "dev"))
    write(alpha, "results/dev.md")
    cli.run_task(SimpleNamespace(task_command="report", project=None, summary="done", blocked=False), None)
    assert delivered[-1][1].startswith(pursuit.WAKE_NOTE)
    assert pursuit.wake_note(alpha) == ""


def test_a_clear_before_the_final_screen_output_counts_for_that_turn_only(alpha, monkeypatch):
    sent = []
    screens = Screens()
    checker = Checker(lambda p, row, text: sent.append(text),
                      watcher=Watcher(screens.list_panes, screens.capture))
    clock = [time.time()]
    monkeypatch.setattr(pursuit.time, "time", lambda: clock[0])

    def output(at):
        clock[0] = at
        screens.activity = int(at)
        screens.text["%1"] += "x"
        checker.tick(rows(), at)

    def resumed():
        return len(sent)

    def clear_at(at):
        clock[0] = at
        pursuit.log(alpha, "clear")

    start = clock[0]
    checker.tick(rows(), start)
    # A short turn: the clear lands before the tick that first sees the turn's output.
    clear_at(start + 9)
    output(start + 10)
    checker.tick(rows(), start + 15)
    checker.tick(rows(), start + 80)
    assert resumed() == 1
    # A pause before the final text ends busy, so the final text looks like a new turn.
    output(start + 100)
    clear_at(start + 101)
    checker.tick(rows(), start + 106)
    output(start + 110)
    checker.tick(rows(), start + 115)
    checker.tick(rows(), start + 180)
    assert resumed() == 2
    output(start + 200)
    checker.tick(rows(), start + 205)
    checker.tick(rows(), start + 270)
    assert resumed() == 2


def test_a_quiet_task_session_is_reminded_then_the_coordinator_is_told(alpha):
    sent = []
    screens = Screens()
    checker = Checker(lambda p, row, text: sent.append(row["display_name"]),
                      watcher=Watcher(screens.list_panes, screens.capture))
    pursuit.open_task(alpha, "dev", "fakeagent", "/x", "Add login", ["tests pass"], "results/dev.md")
    now = time.time()
    checker.tick(rows(), now)
    run_turn(checker, screens, "%2", now + 10)
    assert sent == []
    checker.tick(rows(), now + 20 + checker.task_quiet_seconds)
    assert sent == ["dev"]
    later = now + 3 * checker.task_quiet_seconds
    run_turn(checker, screens, "%2", later, quiet=checker.task_quiet_seconds + 10)
    assert sent == ["dev", "Coordinator"]
    run_turn(checker, screens, "%2", later * 2, quiet=checker.task_quiet_seconds + 10)
    assert sent == ["dev", "Coordinator"]
    assert [e["kind"] for e in pursuit.events(alpha)].count("task.quiet") == 1


def test_task_start_briefs_the_session_and_report_tells_the_coordinator(alpha, monkeypatch, capsys):
    from types import SimpleNamespace

    from agentop import ops

    started, prompts, delivered = [], [], []
    monkeypatch.setattr(ops, "get_sessions", lambda with_meta=False: [])
    monkeypatch.setattr(
        ops,
        "start",
        lambda tool, cwd, name, params, project: started.append((name, params)) or SimpleNamespace(name=f"{name}-1"),
    )
    monkeypatch.setattr(ops, "first_prompt", lambda name, prompt: prompts.append(prompt))
    monkeypatch.setattr("agentop.cli.deliver", lambda target, text, project: delivered.append((target, text)))

    direct(alpha)
    args = SimpleNamespace(
        task_command="start", project="alpha", name="dev", tool="claude", cwd="/x", model=None, action="M1A1",
        no_remote_control=True,
        goal="Add login", accept=["tests pass", "docs updated"], artifact="results/dev.md", prompt="Read README first.",
    )
    cli.run_task(args, None)
    assert started == [("dev", {"remote_control": False})]
    assert "Goal: Add login" in prompts[0] and "- docs updated" in prompts[0] and "Read README first." in prompts[0]
    assert str(projects.folder("alpha") / "results/dev.md") in prompts[0] and "agentop coordinator task report" in prompts[0]

    monkeypatch.setattr(ops, "current_session", lambda: ("dev-1", "alpha", "dev"))
    write(alpha, "results/dev.md")
    cli.run_task(SimpleNamespace(task_command="report", project=None, summary="12 tests pass", blocked=False), None)
    target, text = delivered[-1]
    assert target == "Coordinator" and "dev reported round 1 of 3: 12 tests pass" in text and "(present)" in text

    with pytest.raises(SystemExit):
        cli.run_yield(SimpleNamespace(kind="waiting", project=None, **dict.fromkeys(
            ("evidence", "question", "recommend", "default", "meanwhile", "need", "tried", "note"), "")))
    assert "only the Coordinator yields" in capsys.readouterr().err


def test_a_coordinator_must_start_tasks_not_plain_sessions(alpha, monkeypatch, capsys):
    from types import SimpleNamespace

    from agentop import cli as core_cli
    from agentop import ops

    monkeypatch.setattr(ops, "current_session", lambda: ("coord-1", "alpha", "Coordinator"))
    args = SimpleNamespace(cwd="/x", project=None, untracked=False, prompt=None, model=None, tool="claude", session_name="dev")
    with pytest.raises(SystemExit):
        core_cli.cmd_start(args)
    assert "agentop coordinator task start" in capsys.readouterr().err


def test_coordinator_clear_waits_until_input_prompt_finishes(alpha, monkeypatch):
    from agentop.plugins.attention import Screen

    class InputWatcher:
        screens = {"coord-1": Screen("", "80x24", 0, waiting_since=100, state="input")}

        def tick(self, sessions, now):
            pass

    watcher = InputWatcher()
    checker = Checker(lambda *args: None, watcher=watcher)
    cleared = []
    monkeypatch.setattr(checker, "_clear", lambda row: cleared.append(row["id"]))
    checker.tick(rows(), 110)
    assert cleared == []
    watcher.screens["coord-1"].state = "done"
    checker.tick(rows(), 111)
    assert cleared == ["coord-1"]


def test_checker_subscribes_to_project_sessions_and_stops_on_feed_clear(alpha, monkeypatch):
    from agentop.status_feed import SessionStatus, StatusFeed

    feed = StatusFeed()
    checker = Checker(lambda *args: None, status_feed=feed)
    cleared = []
    monkeypatch.setattr(checker, "_clear", lambda row: cleared.append(row["id"]))
    checker.tick(rows(), 100)
    assert set(checker.subscriptions) == {"coord-1", "dev-1"}
    feed.publish(SessionStatus("coord-1", "done", 101, "hook", "Stop"))
    checker.tick(rows(), 110)
    assert cleared == ["coord-1"]
    feed.clear()
    checker.tick(rows(), 120)
    assert cleared == ["coord-1"]
    checker.tick([], 130)
    assert checker.subscriptions == {}
    assert feed._subscribers == {}
