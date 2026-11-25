"""A Coordinator's tasks, stop reasons, idle checks and review (docs/0_13_projects.md, section 4.3)."""

import json
import os
import time

import pytest

from agentop import coordinator, projects
from agentop.plugins.projects import cli, pursuit, review, track
from agentop.plugins.projects.checker import Checker
from agentop.status_feed import SessionStatus, StatusFeed


@pytest.fixture
def alpha(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTOP_HOME", str(tmp_path))
    found = projects.create("alpha", "projects")
    coordinator.write_instructions(found)
    return "alpha"


NEXT = {"next": "Wait for dev's report, then check it against the criteria."}


@pytest.fixture(autouse=True)
def no_service(monkeypatch):
    monkeypatch.setattr("agentop.cli._service_api", lambda *a, **k: None)


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
        pursuit.yield_turn(alpha, "waiting", NEXT)
    pursuit.yield_turn(alpha, "owner", {"question": "Goal?", "recommend": "Tell me", "default": "Nothing to pursue yet"})

    pursuit.open_task(alpha, "dev", "claude", "/x", "Add login", ["tests pass"], "results/dev.md")
    with pytest.raises(pursuit.PursuitError, match="no objective"):
        pursuit.yield_turn(alpha, "waiting", NEXT)
    touch_state(alpha)
    assert pursuit.yield_turn(alpha, "waiting", NEXT)["sessions"] == "dev"
    with pytest.raises(pursuit.PursuitError, match="still open"):
        pursuit.yield_turn(alpha, "done", {"evidence": "it works", **NEXT})

    write(alpha, "results/dev.md")
    pursuit.report(alpha, "dev", "done")
    with pytest.raises(pursuit.PursuitError, match="reported and wait for you"):
        pursuit.yield_turn(alpha, "waiting", NEXT)
    pursuit.accept(alpha, "dev", "ran the tests")
    state = projects.folder(alpha) / pursuit.STATE_FILE
    os.utime(state, (time.time() - 100, time.time() - 100))
    with pytest.raises(pursuit.PursuitError, match="before the latest task was closed"):
        pursuit.yield_turn(alpha, "done", {"evidence": "it works", **NEXT})
    touch_state(alpha)
    with pytest.raises(pursuit.PursuitError, match="since the last review"):
        pursuit.yield_turn(alpha, "done", {"evidence": "it works", **NEXT})
    _, path, _ = review.run(alpha)
    with pytest.raises(pursuit.PursuitError, match="reflection"):
        pursuit.yield_turn(alpha, "done", {"evidence": "it works", **NEXT})
    path.write_text(path.read_text().replace(pursuit.REFLECTION_PLACEHOLDER, "I should have split it earlier."))
    assert pursuit.yield_turn(alpha, "done", {"evidence": "it works", **NEXT})["type"] == "done"


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


def rows():
    base = {"status": "running", "managed": True, "project": "alpha", "tool": "fakeagent"}
    return [
        {**base, "id": "coord-1", "display_name": "Coordinator", "tmux": {"session": "coord-1"}},
        {**base, "id": "dev-1", "display_name": "dev", "tmux": {"session": "dev-1"}},
    ]


def hook(sid, state, since):
    return SessionStatus(sid, state, since, "hook", "Stop" if state == "done" else "PostToolUse")


def watching(project="alpha", **limits):
    """A checker over a fake feed, with dev's task open; returns (checker, feed, [(to, text)])."""
    if limits:
        meta = projects.meta_dir(project) / "project.json"
        meta.write_text(json.dumps({**json.loads(meta.read_text()), **limits}))
    sent = []
    feed = StatusFeed()
    checker = Checker(lambda p, row, text: sent.append((row["display_name"], text)), status_feed=feed,
                      pane=lambda row: None)
    pursuit.open_task(project, "dev", "fakeagent", "/x", "Add login", ["tests pass"], "results/dev.md")
    return checker, feed, sent


def kinds(project, kind):
    return [e for e in pursuit.events(project) if e["kind"] == kind]


def test_a_task_session_at_a_question_is_brought_to_the_coordinator_once_per_episode(alpha):
    checker, feed, sent = watching()
    now = time.time()
    feed.publish(SessionStatus("dev-1", "input", now, "hook", "PermissionRequest"))
    checker.tick(rows(), now + 119)
    assert sent == []
    checker.tick(rows(), now + 120)
    checker.tick(rows(), now + 500)
    assert [to for to, _ in sent] == ["Coordinator"]
    assert "[task t1] dev has waited for input for 2m" in sent[0][1] and "Look at its screen" in sent[0][1]
    feed.publish(hook("dev-1", "working", now + 600))
    checker.tick(rows(), now + 601)
    feed.publish(SessionStatus("dev-1", "input", now + 700, "hook", "Notification"))
    checker.tick(rows(), now + 820)
    assert [to for to, _ in sent] == ["Coordinator", "Coordinator"]
    assert [e["session"] for e in kinds(alpha, "task.input")] == ["dev", "dev"]


def test_the_thresholds_come_from_project_json_and_zero_turns_one_off(alpha):
    checker, feed, sent = watching(task_input_after="30s", task_working_after=0)
    feed.publish(SessionStatus("dev-1", "input", 100, "hook", "PermissionRequest"))
    checker.tick(rows(), 130)
    assert len(sent) == 1
    feed.publish(hook("dev-1", "working", 200))
    checker.tick(rows(), 200 + 10 * 3600)
    assert len(sent) == 1


def test_codexs_approval_prompt_seen_on_screen_counts_as_input(alpha):
    checker, feed, sent = watching()
    feed.publish(SessionStatus("dev-1", "input", 100, "screen", "PermissionRequest"))
    checker.tick(rows(), 220)
    assert len(sent) == 1 and "waited for input" in sent[0][1]


def test_a_task_session_working_with_no_new_event_is_brought_to_the_coordinator_once(alpha):
    checker, feed, sent = watching()
    feed.publish(hook("dev-1", "working", 1000))
    checker.tick(rows(), 1000 + 29 * 60)
    assert sent == []
    checker.tick(rows(), 1000 + 30 * 60)
    checker.tick(rows(), 1000 + 50 * 60)
    assert len(sent) == 1 and "dev has been working with no new event for 30m" in sent[0][1]
    # Each tool event starts a new episode.
    feed.publish(hook("dev-1", "working", 1000 + 51 * 60))
    checker.tick(rows(), 1000 + 60 * 60)
    assert len(sent) == 1
    checker.tick(rows(), 1000 + 81 * 60)
    assert len(sent) == 2 and len(kinds(alpha, "task.silent")) == 2


def test_a_lost_stop_the_screen_guessed_counts_as_working_silently(alpha):
    checker, feed, sent = watching()
    feed.publish(SessionStatus("dev-1", "done", 1000, "screen", "screen"))
    checker.tick(rows(), 1000 + 30 * 60)
    assert len(sent) == 1 and "(its screen looks idle)" in sent[0][1]


def test_done_without_a_report_reminds_the_session_then_tells_the_coordinator(alpha):
    checker, feed, sent = watching()
    now = 10_000.0
    feed.publish(hook("dev-1", "done", now))
    checker.tick(rows(), now + 179)
    assert sent == []
    checker.tick(rows(), now + 180)
    checker.tick(rows(), now + 400)
    assert [to for to, _ in sent] == ["dev"] and "only a reminder" in sent[0][1]
    # Its reply to the reminder ends another turn: not reminded again.
    feed.publish(hook("dev-1", "working", now + 401))
    checker.tick(rows(), now + 401)
    feed.publish(hook("dev-1", "done", now + 420))
    checker.tick(rows(), now + 420 + 600)
    assert [to for to, _ in sent] == ["dev"]
    checker.tick(rows(), now + 420 + 2 * 3600)
    checker.tick(rows(), now + 420 + 3 * 3600)
    assert [to for to, _ in sent] == ["dev", "Coordinator"]
    assert "dev ended its turn 2h ago without reporting round 1" in sent[1][1]
    assert len(kinds(alpha, "task.reminded")) == 1 and len(kinds(alpha, "task.quiet")) == 1


def test_a_turn_that_ends_while_a_background_job_runs_is_only_reminded(alpha):
    checker, feed, sent = watching()
    now = 10_000.0
    for n in range(3):
        start = now + n * 1000
        feed.publish(hook("dev-1", "working", start))
        checker.tick(rows(), start)
        feed.publish(hook("dev-1", "done", start + 10))
        checker.tick(rows(), start + 10 + 180)
        # The job finishes before 2 h and wakes the session, which carries on.
    assert [to for to, _ in sent] == ["dev", "dev"]
    assert pursuit.task_for(alpha, "dev")["status"] == "open" and kinds(alpha, "task.quiet") == []


def test_a_reported_task_is_not_reminded(alpha):
    checker, feed, sent = watching()
    write(alpha, "results/dev.md")
    pursuit.report(alpha, "dev", "done")
    feed.publish(hook("dev-1", "done", 100))
    checker.tick(rows(), 100 + 3 * 3600)
    assert sent == []


@pytest.mark.parametrize("status", [
    SessionStatus("dev-1", "done", 0.0, "screen"),
    SessionStatus("dev-1", "working", 0.0, "screen"),
    SessionStatus("dev-1", "unknown", 0.0, "unavailable"),
])
def test_a_task_session_without_hook_status_gets_no_notice(alpha, status):
    checker, feed, sent = watching()
    feed.publish(status)
    checker.tick(rows(), 10)
    checker.tick(rows(), 10 + 3 * 3600)
    assert sent == []


def test_a_notice_while_a_wake_runs_waits_for_the_next_tick(alpha):
    from types import SimpleNamespace

    ran = []
    accept = iter([False, True])
    feed = StatusFeed()
    checker = Checker(lambda *a: None, status_feed=feed, pane=lambda row: SimpleNamespace(agent=None),
                      run=lambda project, fn: ran.append(project) or next(accept))
    pursuit.open_task(alpha, "dev", "fakeagent", "/x", "Add login", ["tests pass"], "results/dev.md")
    feed.publish(hook("coord-1", "working", 100))
    feed.publish(SessionStatus("dev-1", "input", 100, "hook", "PermissionRequest"))
    for at in (300, 302, 304):
        checker.tick(rows(), at)
    assert ran == ["alpha", "alpha"] and len(kinds(alpha, "task.input")) == 1


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
    monkeypatch.setattr(ops, "find_session", lambda name, project=None: {"name": f"{name}-1"})
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
    feed = StatusFeed()
    checker = Checker(lambda *args: None, status_feed=feed)
    cleared = []
    monkeypatch.setattr(checker, "_net", lambda row: cleared.append(row["id"]))
    feed.publish(SessionStatus("coord-1", "input", 100, "hook", "PermissionRequest"))
    checker.tick(rows(), 110)
    assert cleared == []
    feed.publish(SessionStatus("coord-1", "done", 100, "hook", "Stop"))
    checker.tick(rows(), 111)
    assert cleared == ["coord-1"]


def test_checker_subscribes_to_project_sessions_and_stops_on_feed_clear(alpha, monkeypatch):
    feed = StatusFeed()
    checker = Checker(lambda *args: None, status_feed=feed)
    cleared = []
    monkeypatch.setattr(checker, "_net", lambda row: cleared.append(row["id"]))
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
