"""Clearing the Coordinator's context: on wake, as a safety net, and the Next that survives it (spec 9)."""

import json
import time
from types import SimpleNamespace

import pytest

from agentop import coordinator, ops, projects
from agentop.agentclis.base import _plain as plain
from agentop.agentclis.claude import ClaudeAgentCli
from agentop.agentclis.codex import CodexAgentCli
from agentop.plugins.projects import cli, pursuit, track, wake
from agentop.plugins.projects.checker import Checker
from agentop.status_feed import SessionStatus, StatusFeed

NEXT = "Wait for dev's report, then check it against the login criteria."
RULE = "─" * 40

SCREENS = {
    "claude": {
        "old": f" ▐▛███▛█   Claude Code v2.1.330\n❯ [agentop] You have just been started\n● Started dev.\n{RULE}\n❯ \n{RULE}\n",
        "cleared": f" ▐▛███▛█   Claude Code v2.1.330\n❯ /clear\n{RULE}\n❯ \n{RULE}\n",
        "draft": f"● Started dev.\n{RULE}\n❯ half a thought\n{RULE}\n",
        "hint": f"● Started dev.\n{RULE}\n❯ \x1b[2mTry \"fix the tests\"\x1b[0m\n{RULE}\n",
    },
    "codex": {
        "old": "• Started dev.\n› [agentop] You have just been started\n\n› \x1b[2mAsk Codex to do anything\x1b[0m\n\n  luna · /p/alpha · Start the project\n",
        "cleared": "  >_ OpenAI Codex (v0.171.0)\n\n› \x1b[2mAsk Codex to do anything\x1b[0m\n\n  luna · /p/alpha\n",
        "draft": "• Started dev.\n› half a thought\n\n  luna · /p/alpha · Start the project\n",
        "hint": "• Started dev.\n› \x1b[2mAsk Codex to do anything\x1b[0m\n\n  luna · /p/alpha · Start the project\n",
    },
}


def feed_status(state, since=0.0):
    """What the attention plugin's feed says: a hook state, a screen guess, or nothing."""
    if state is None:
        return None
    if state == "screen":
        return SessionStatus("coord-1", "done", since, "screen")
    if state == "unknown":
        return SessionStatus("coord-1", "unknown", since, "unavailable")
    return SessionStatus("coord-1", state, since, "hook", "Stop" if state == "done" else "PostToolUse")


class FakePane(wake.Pane):
    """A Coordinator's pane: its screen, its status in the feed, what is typed into it, and its conversation ID.

    `states` is the feed's state at each look, the last one staying; the screen never says whether it is busy.
    """

    def __init__(self, tool="claude", screen="old", states=("done",), clears=True, new_id=True, busy_screen=False):
        agent = ClaudeAgentCli() if tool == "claude" else CodexAgentCli()
        super().__init__(agent, "coord-1", 4242, "coord-1")
        self.screens = SCREENS[tool]
        self.current = self.screens[screen]
        self.states = list(states)
        self.looks = 0
        self.busy, self.clears, self.new_id = busy_screen, clears, new_id and tool == "claude"
        self.id = "conv-1"
        self.typed = []
        self.ticks = 0

    def status(self):
        self.looks += 1
        state = self.states.pop(0) if len(self.states) > 1 else self.states[0]
        return feed_status(state, wake._now() - 10)

    def screen(self, escapes=False):
        self.ticks += 1
        text = self.current + (f"✻ Working ({self.ticks}s)\n" if self.busy else "")
        return text if escapes else plain(text)

    def type(self, text):
        self.typed.append(text)
        if text == "/clear" and self.clears:
            self.current = self.screens["cleared"]
            if self.new_id:
                self.id = "conv-2"
        elif text != "/clear":
            self.current = self.current + f"❯ {text[:20]}\n"

    def conversation(self):
        return self.id if self.new_id else None


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(wake, "_clock", lambda: clock[0])
    monkeypatch.setattr(wake, "_sleep", lambda s: clock.__setitem__(0, clock[0] + s))
    monkeypatch.setattr(wake, "_now", lambda: clock[0])
    monkeypatch.setattr("agentop.cli._service_api", lambda *a, **k: None)
    return clock


@pytest.fixture
def alpha(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTOP_HOME", str(tmp_path))
    found = projects.create("alpha", "projects")
    coordinator.write_instructions(found)
    pursuit.new_direction("alpha", "Ship login.")
    with pursuit._locked("alpha"):
        track.add_action("alpha", "M1", "Add login")
        track.add_action("alpha", "M1", "Add logout")
    return "alpha"


def events(kind):
    return [e for e in pursuit.events("alpha") if e["kind"] == kind]


def task(name="dev", action="M1A1"):
    return pursuit.open_task("alpha", name, "claude", "/x", f"{name}'s goal", ["tests pass"], f"results/{name}.md", action=action)


def waiting(**extra):
    return pursuit.yield_turn("alpha", "waiting", {"next": NEXT, **extra})


def report(monkeypatch, pane, name="dev", summary="12 tests pass", blocked=False):
    """`agentop coordinator task report` run in the task's session, with the Coordinator's pane faked."""
    row = {"name": "Coordinator-1", "managed": True, "tool": pane.agent.name, "tmux": {"session": "coord-1"}}
    monkeypatch.setattr(ops, "current_session", lambda: (f"{name}-1", "alpha", name))
    monkeypatch.setattr(ops, "find_session", lambda target, project=None: row)
    monkeypatch.setattr(wake, "pane_of", lambda r: pane)
    path = projects.folder("alpha") / f"results/{name}.md"
    path.parent.mkdir(exist_ok=True)
    path.write_text("result")
    cli.run_task(SimpleNamespace(task_command="report", project=None, summary=summary, blocked=blocked), None)


@pytest.mark.parametrize("tool, how", [("claude", "a new session id"), ("codex", "the cleared screen")])
def test_a_report_to_an_idle_waiting_coordinator_clears_then_types_one_prompt(alpha, monkeypatch, tool, how):
    task()
    waiting()
    pane = FakePane(tool)
    report(monkeypatch, pane)
    assert len(pane.typed) == 2 and pane.typed[0] == "/clear"
    prompt = pane.typed[1]
    assert prompt.startswith(wake.CLEARED) and NEXT in prompt and "yield waiting" in prompt
    assert "[task P1_M1A1-1] dev reported round 1 of 3: 12 tests pass" in prompt
    assert prompt.index(NEXT) < prompt.index("dev reported")
    assert [e["how"] for e in events("clear.verified")] == [how]
    assert len(events("clear.sent")) == 1 and events("clear.skipped") == []
    assert events("wake")[-1]["cleared"] == "cleared"


def test_a_blocked_report_is_a_wake_too(alpha, monkeypatch):
    task()
    waiting()
    pane = FakePane()
    report(monkeypatch, pane, summary="no access to the repo", blocked=True)
    assert pane.typed[0] == "/clear" and "dev BLOCKED round 1" in pane.typed[1]
    assert events("clear.sent")[0]["source"] == "task.blocked"


@pytest.mark.parametrize("case, reason", [
    ("busy", "the Coordinator is busy"),
    ("input", "the Coordinator is waiting for input"),
    ("no hooks", "status unknown"),
    ("feed off", "status unknown"),
    ("no status", "status unknown"),
    ("no yield", "no yield since the last event"),
    ("draft", "a draft is in the prompt"),
    ("other report", "api reported and wait for a decision"),
    ("blocked yield", "the last yield was blocked"),
    ("keep", "the last yield asked to keep the context"),
    ("track", "the track checks fail"),
])
def test_without_a_safe_point_the_report_is_typed_without_a_clear(alpha, monkeypatch, case, reason):
    task()
    states = {"busy": ("working",), "input": ("input",), "no hooks": ("screen",), "feed off": ("unknown",),
              "no status": (None,)}.get(case, ("done",))
    pane = FakePane(states=states, screen="draft" if case == "draft" else "old")
    if case == "other report":
        task("api", "M1A2")
        path = projects.folder("alpha") / "results/api.md"
        path.parent.mkdir(exist_ok=True)
        path.write_text("x")
        pursuit.report("alpha", "api", "done")
        pursuit.log("alpha", "yield", type="waiting")
    elif case == "blocked yield":
        pursuit.yield_turn("alpha", "blocked", {"need": "access", "tried": "asking"})
    elif case == "track":
        waiting()
        plan = track.plan_path("alpha")
        plan.write_text(plan.read_text().replace("- M1A2 Add logout  [planned]", "- M1A2 Add logout  [done]"))
    else:
        waiting(**({"keep": "yes"} if case == "keep" else {}))
    if case == "no yield":
        pursuit.log("alpha", "wake", source="owner")
    report(monkeypatch, pane)
    assert len(pane.typed) == 1 and pane.typed[0].startswith("[task P1_M1A1-1] dev reported round 1")
    assert [e["reason"] for e in events("clear.skipped")][0].startswith(reason)
    assert events("clear.sent") == []


def test_an_unverified_clear_still_delivers_the_report_and_says_so(alpha, monkeypatch, fast):
    task()
    waiting()
    pane = FakePane("codex", clears=False)
    started = fast[0]
    report(monkeypatch, pane)
    assert pane.typed[0] == "/clear" and len(pane.typed) == 2
    assert pane.typed[1].startswith("[agentop] agentop typed /clear but couldn't confirm it within 20s")
    assert NEXT in pane.typed[1] and "dev reported round 1" in pane.typed[1]
    assert events("clear.skipped")[0]["reason"] == "not verified within 20s"
    assert events("clear.verified") == []
    assert fast[0] - started >= wake.CLEAR_TIMEOUT


def test_a_second_report_before_the_next_yield_is_not_cleared_again(alpha, monkeypatch):
    task()
    task("api", "M1A2")
    waiting()
    pane = FakePane()
    report(monkeypatch, pane)
    report(monkeypatch, pane, "api", "api done")
    assert pane.typed.count("/clear") == 1
    assert pane.typed[-1].startswith("[task P1_M1A2-1] api reported")
    assert events("clear.skipped")[-1]["reason"] == "no yield since the last event"


def test_a_finished_turn_after_a_wake_can_clear_without_another_yield(alpha, monkeypatch):
    task()
    waiting()
    pursuit.log(alpha, "wake", source="task.silent")
    pane = FakePane()
    ended = time.time() + 1
    pane.status = lambda: feed_status("done", ended)
    monkeypatch.setattr(wake, "_now", lambda: ended + 2)
    report(monkeypatch, pane)
    assert pane.typed[0] == "/clear"
    assert "dev reported" in pane.typed[1]


def test_a_draft_defers_a_notice_without_recording_a_wake(alpha):
    pane = FakePane(screen="draft")
    checker = Checker(lambda *args: None, status_feed=StatusFeed(), pane=lambda row: pane,
                      run=lambda project, fn: fn())
    task()
    waiting()
    assert not checker._tell_coordinator(alpha, {}, "inspect dev", "task.silent")
    assert not events("wake") and not pane.typed
    pane.current = pane.screens["old"]
    assert checker._tell_coordinator(alpha, {}, "inspect dev", "task.silent")
    assert events("wake")[-1]["source"] == "task.silent"


def test_a_placeholder_screen_hint_is_not_a_draft(alpha):
    for tool in ("claude", "codex"):
        agent = FakePane(tool).agent
        assert agent.draft(SCREENS[tool]["hint"]) is False
        assert agent.draft(SCREENS[tool]["draft"]) is True
        assert agent.cleared(plain(SCREENS[tool]["cleared"]))
        assert not agent.cleared(plain(SCREENS[tool]["old"]))


# --- the safety net (9.3), at the end of a Coordinator turn


def coordinator_row():
    return {"id": "coord-1", "status": "running", "managed": True, "project": "alpha", "tool": "claude",
            "display_name": "Coordinator", "tmux": {"session": "coord-1"}, "session_id": "conv-1"}


def turn_ends(pane, end="done"):
    """One Coordinator turn as the feed reports it: working, then `end`, past the checker's wait. Returns what it ran."""
    feed = StatusFeed()
    pane.status = lambda: feed.latest("coord-1")
    ran = []
    checker = Checker(lambda p, row, text: ran.append(text), status_feed=feed,
                      pane=lambda row: pane, run=lambda project, fn: ran.append(fn()))
    now = wake._now()
    feed.publish(feed_status("working", now))
    for at in (now, now + 1, now + 3):
        checker.tick([coordinator_row()], at)
    assert pane.typed == [] and ran == []
    if end is None:
        feed.clear()
    else:
        feed.publish(feed_status(end, now + 4))
    for at in (now + 5, now + 7, now + 12, now + 30):
        checker.tick([coordinator_row()], at)
    return ran


def test_a_large_context_is_cleared_at_the_next_safe_point_only(alpha, monkeypatch):
    task()
    waiting()
    monkeypatch.setattr(wake, "context_share", lambda row, agent: (130_000, 200_000))
    pane = FakePane()
    assert turn_ends(pane) == ["cleared"]
    assert pane.typed == ["/clear"]
    assert events("clear.sent")[0]["why"] == "the context holds 130000 of 200000 tokens"
    # The next report finds it cleared: no second clear, and the prompt says so.
    report(monkeypatch, pane)
    assert pane.typed.count("/clear") == 1 and pane.typed[-1].startswith(wake.CLEARED)
    assert events("wake")[-1]["cleared"] == "already cleared"


def test_the_safety_net_never_clears_over_a_draft_or_below_the_threshold(alpha, monkeypatch):
    task()
    waiting()
    monkeypatch.setattr(wake, "context_share", lambda row, agent: (130_000, 200_000))
    pane = FakePane(screen="draft")
    assert turn_ends(pane) == ["not cleared"]
    assert pane.typed == [] and events("clear.skipped")[0]["reason"] == "a draft is in the prompt"

    meta = projects.meta_dir("alpha") / "project.json"
    meta.write_text(json.dumps({**json.loads(meta.read_text()), "clear_context_at": 0.8}))
    pane = FakePane()
    assert turn_ends(pane) == []
    assert pane.typed == [] and events("clear.sent") == []


def test_a_closed_milestone_clears_at_the_next_safe_point(alpha, monkeypatch, capsys):
    monkeypatch.setattr(wake, "context_share", lambda row, agent: (1_000, 200_000))
    monkeypatch.setattr(ops, "current_session", lambda: ("coord-1", "alpha", "Coordinator"))
    for ref in ("M1A1", "M1A2"):
        with pursuit._locked("alpha"):
            track.add_note("alpha", ref, "did it myself", "coordinator")
            track.set_action("alpha", ref, "done", "", "")
    cli.coordinator_milestone(SimpleNamespace(milestone_command="close", milestone="M1", summary="Login in."), "alpha")
    with pursuit._locked("alpha"):
        track.add_milestone("alpha", "Next", None)
        track.add_action("alpha", "M2", "More")
    task("dev", "M2A1")
    waiting()
    pane = FakePane("codex")
    assert turn_ends(pane) == ["cleared"]
    assert events("clear.verified")[0]["why"] == "M1 was closed"
    pursuit.log("alpha", "wake", source="owner")
    waiting()
    pane = FakePane("codex")
    assert turn_ends(pane) == []


def test_yield_clear_goes_through_the_same_check_then_says_carry_on(alpha, monkeypatch, capsys):
    monkeypatch.setattr(wake, "context_share", lambda row, agent: None)
    monkeypatch.setattr(ops, "current_session", lambda: ("coord-1", "alpha", "Coordinator"))
    fields = dict.fromkeys(("question", "recommend", "default", "meanwhile", "need", "tried", "note"), "")
    cli.run_yield(SimpleNamespace(kind="done", project=None, clear=True, keep=False, evidence="login ships",
                                  next="Draft the release notes while I wait.", **fields))
    assert "once it has ended and it is safe" in capsys.readouterr().out
    pane = FakePane(screen="draft")
    assert turn_ends(pane) == ["not cleared"] and pane.typed == []
    pane = FakePane()
    assert turn_ends(pane) == ["cleared"]
    assert pane.typed[0] == "/clear" and pane.typed[1].startswith(wake.RESUME)
    assert "Draft the release notes while I wait." in pane.typed[1]
    assert pursuit.pending_clear("alpha") is None
    pane = FakePane()
    assert turn_ends(pane) == []


def owner_yield(clear=False, question="M1 is done. May I merge login into main?"):
    fields = dict.fromkeys(("evidence", "meanwhile", "need", "tried", "note", "next"), "")
    cli.run_yield(SimpleNamespace(kind="owner", project=None, clear=clear, keep=False, question=question,
                                  recommend="Yes", default="Hold the merge", **fields))


def close_m1():
    for ref in ("M1A1", "M1A2"):
        with pursuit._locked("alpha"):
            track.add_note("alpha", ref, "did it myself", "coordinator")
            track.set_action("alpha", ref, "done", "", "")
    cli.coordinator_milestone(SimpleNamespace(milestone_command="close", milestone="M1", summary="Login in."), "alpha")


@pytest.mark.parametrize("clear", [False, True])
def test_an_open_owner_ask_is_never_cleared_off_the_screen_until_the_owner_wakes_it(alpha, monkeypatch, capsys, clear):
    monkeypatch.setattr(wake, "context_share", lambda row, agent: (190_000, 200_000))
    monkeypatch.setattr(ops, "current_session", lambda: ("coord-1", "alpha", "Coordinator"))
    close_m1()
    owner_yield(clear)
    ask = pursuit.events("alpha")[-1 - clear]["action"]
    pane = FakePane()
    assert turn_ends(pane) == ["not cleared"] and pane.typed == []
    skipped = events("clear.skipped")[-1]
    assert skipped["reason"] == f"{ask} asks the owner and is still open"
    assert skipped["why"] == ("the yield asked for it" if clear else "M1 was closed")
    assert turn_ends(FakePane()) == ["not cleared"]
    # The owner answers: the ask closes, and the deferred clear happens at the end of the next yield's turn.
    pursuit.wake_note("alpha", "owner")
    with pursuit._locked("alpha"):
        track.answer("alpha", ask, "Yes, merge it")
        track.add_action("alpha", ask.partition("A")[0], "Merge login")
    pursuit.yield_turn("alpha", "done", {"evidence": "merged", "next": "Pick the next milestone with the owner."})
    pane = FakePane()
    assert turn_ends(pane) == ["cleared"] and pane.typed[0] == "/clear"
    assert events("clear.verified")[-1]["why"] == ("the yield asked for it" if clear else "M1 was closed")


def test_a_task_event_still_clears_over_an_owner_yield(alpha, monkeypatch, capsys):
    monkeypatch.setattr(ops, "current_session", lambda: ("coord-1", "alpha", "Coordinator"))
    task()
    owner_yield()
    pane = FakePane()
    report(monkeypatch, pane)
    assert pane.typed[0] == "/clear" and events("wake")[-1]["cleared"] == "cleared"


def test_a_report_that_clears_over_an_open_owner_ask_shows_the_ask_again(alpha, monkeypatch, capsys):
    monkeypatch.setattr(ops, "current_session", lambda: ("coord-1", "alpha", "Coordinator"))
    task()
    owner_yield()
    ask = pursuit.events("alpha")[-1]["action"]
    pane = FakePane()
    report(monkeypatch, pane)
    assert pane.typed[0] == "/clear" and events("wake")[-1]["cleared"] == "cleared"
    prompt = pane.typed[1]
    assert "Open questions for the owner:" in prompt
    assert f"{ask}: M1 is done. May I merge login into main?" in prompt
    assert "Recommend: Yes" in prompt and "Default: Hold the merge" in prompt
    assert prompt.index("Open questions") < prompt.index("dev reported round 1")


def test_a_report_that_clears_with_no_open_ask_types_the_prompt_unchanged(alpha, monkeypatch):
    task()
    waiting()
    pane = FakePane()
    report(monkeypatch, pane)
    assert pane.typed[0] == "/clear"
    assert "Open questions" not in pane.typed[1]
    head = wake.CLEARED + "\n\n" + pursuit.read_next("alpha") + "\n\n"
    assert pane.typed[1].startswith(head) and "dev reported round 1" in pane.typed[1][len(head):].split("\n")[0]


def test_the_resume_prompt_after_a_yield_clear_shows_an_open_owner_ask(alpha, monkeypatch):
    task()
    waiting()
    with pursuit._locked("alpha"):
        track.ask("alpha", None, "Ship it on Friday?", "Yes", "Wait for Monday")
    pane = FakePane()
    assert wake.safety_clear("alpha", pane, "the yield asked for it", explicit=True) == "cleared"
    assert pane.typed[0] == "/clear" and pane.typed[1].startswith(wake.RESUME)
    assert "Ship it on Friday?" in pane.typed[1] and "Default: Wait for Monday" in pane.typed[1]


def test_the_safety_net_waits_for_the_feed_not_the_screen(alpha, monkeypatch):
    task()
    waiting()
    monkeypatch.setattr(wake, "context_share", lambda row, agent: (130_000, 200_000))
    # A screen that keeps changing doesn't hold the clear back once the feed says the turn ended.
    pane = FakePane(busy_screen=True)
    assert turn_ends(pane) == ["cleared"] and pane.typed == ["/clear"]


@pytest.mark.parametrize("end", ["input", "unknown", "screen", None])
def test_the_safety_net_never_clears_without_a_hook_turn_end(alpha, monkeypatch, end):
    task()
    waiting()
    monkeypatch.setattr(wake, "context_share", lambda row, agent: (130_000, 200_000))
    pane = FakePane()
    assert turn_ends(pane, end) == [] and pane.typed == []
    skipped = [e["reason"] for e in events("clear.skipped")]
    assert skipped == ([] if end == "input" else [wake.UNKNOWN])
    assert events("clear.sent") == []


def test_a_report_while_the_turn_ends_waits_for_its_stop_in_the_feed(alpha, monkeypatch, fast):
    task()
    waiting()
    pane = FakePane(states=("working", "working", "working", "done"))
    started = fast[0]
    report(monkeypatch, pane)
    assert pane.typed[0] == "/clear" and pane.looks >= 4
    assert fast[0] - started < wake.TURN_END_WAIT + 5


def test_a_report_to_a_busy_coordinator_waits_a_little_then_skips(alpha, monkeypatch, fast):
    task()
    waiting()
    pane = FakePane(states=("working",))
    started = fast[0]
    report(monkeypatch, pane)
    assert fast[0] - started >= wake.TURN_END_WAIT
    assert events("clear.skipped")[0]["reason"] == "the Coordinator is busy"
    assert len(pane.typed) == 1 and "dev reported round 1" in pane.typed[0]


def test_a_clear_right_after_a_stop_waits_a_moment(alpha, monkeypatch, fast):
    task()
    waiting()
    pane = FakePane()
    pane.status = lambda: SessionStatus("coord-1", "done", fast[0], "hook", "Stop")
    started = fast[0]
    assert wake.turn_blocker(pane) is None
    assert fast[0] - started >= wake.AFTER_STOP_SECONDS


def test_without_a_service_the_cli_asks_the_bridge_and_without_one_does_not_clear(alpha, monkeypatch):
    task()
    waiting()
    pane = wake.Pane(ClaudeAgentCli(), "coord-1", 4242, "coord-1")
    monkeypatch.setattr(wake.Pane, "type", lambda self, text: typed.append(text))
    monkeypatch.setattr(wake.Pane, "screen", lambda self, escapes=False: SCREENS["claude"]["old"])
    typed = []
    report(monkeypatch, pane)
    assert len(typed) == 1 and "dev reported round 1" in typed[0]
    assert events("clear.skipped")[0]["reason"] == wake.UNKNOWN


# --- Next (9.4)


def test_waiting_and_done_need_a_real_next_written_to_next_md(alpha, monkeypatch, capsys):
    task()
    with pytest.raises(pursuit.PursuitError, match="'waiting' needs --next"):
        pursuit.yield_turn("alpha", "waiting", {})
    for placeholder in ("TBD", "…", "<what you will do next>", "continue", "n/a", "wait"):
        with pytest.raises(pursuit.PursuitError, match="--next must say what you will do next"):
            pursuit.yield_turn("alpha", "waiting", {"next": placeholder})
    with pytest.raises(pursuit.PursuitError, match="'done' needs --evidence and --next"):
        pursuit.yield_turn("alpha", "done", {})
    pursuit.yield_turn("alpha", "owner", {"question": "Q?", "recommend": "R", "default": "D"})
    pursuit.yield_turn("alpha", "blocked", {"need": "access", "tried": "asking"})
    assert not pursuit.next_path("alpha").exists()

    event = waiting()
    assert event["next"] == NEXT
    path = projects.folder("alpha") / "track/1/NEXT.md"
    assert pursuit.next_path("alpha") == path
    text = path.read_text()
    assert text.startswith("# Next\nDate: ") and "· yield waiting" in text and text.rstrip().endswith(NEXT)
    pursuit.yield_turn("alpha", "waiting", {"next": "Check api's report first, it unblocks dev."})
    assert "Check api's report first" in path.read_text() and NEXT not in path.read_text()
    assert [p.name for p in path.parent.iterdir() if p.name.startswith(".")] == []

    monkeypatch.setattr(ops, "current_session", lambda: ("coord-1", "alpha", "Coordinator"))
    out = cli.status_text("alpha")
    assert out.startswith("# Next\nDate: ") and out.index("Check api's report first") < out.index("# Direction 1")


def test_the_cli_refuses_waiting_without_next(alpha, monkeypatch, capsys):
    task()
    monkeypatch.setattr(ops, "current_session", lambda: ("coord-1", "alpha", "Coordinator"))
    fields = dict.fromkeys(("evidence", "question", "recommend", "default", "meanwhile", "need", "tried", "note"), "")
    with pytest.raises(SystemExit):
        cli.run_yield(SimpleNamespace(kind="waiting", project=None, clear=False, keep=False, next="", **fields))
    assert "'waiting' needs --next" in capsys.readouterr().err
    cli.run_yield(SimpleNamespace(kind="waiting", project=None, clear=False, keep=False, next=NEXT, **fields))
    assert "Next is in track/1/NEXT.md" in capsys.readouterr().out


def test_the_service_route_types_the_event(fake_agents, monkeypatch):
    from tests.test_projects import wait_for_screen
    from tests.test_service_live import run_service

    with run_service() as service:
        base = "/plugins/projects/projects"
        assert service.http.post(base, json={"name": "alpha"}).status_code == 201
        row = next(r for r in service.http.get("/sessions").json() if r["project"] == "alpha")
        service.wait_for(row["id"], "running")
        answer = service.http.post(f"{base}/alpha/wake", json={"text": "[task t1] dev reported", "source": "task.report"})
        assert answer.status_code == 200, answer.text
        assert answer.json() == {"outcome": "not cleared"}
        wait_for_screen(row["id"], "got: [task t1] dev reported")
        assert events("clear.skipped")[0]["reason"] == "no yield since the last event"
        assert service.http.post(f"{base}/nope/wake", json={"text": "x"}).status_code == 404


def test_each_tool_reads_its_conversation_and_context_from_its_own_records(tmp_path):
    import os

    claude = ClaudeAgentCli(home=tmp_path)
    (tmp_path / ".claude/sessions").mkdir(parents=True)
    assert claude.conversation_id(os.getpid()) is None
    (tmp_path / f".claude/sessions/{os.getpid()}.json").write_text(json.dumps({"sessionId": "conv-9"}))
    assert claude.conversation_id(os.getpid()) == "conv-9"
    log = tmp_path / "t.jsonl"
    usage = {"input_tokens": 2, "cache_read_input_tokens": 100_000, "cache_creation_input_tokens": 20_000, "output_tokens": 8}
    log.write_text("\n".join(json.dumps(r) for r in (
        {"type": "assistant", "message": {"id": "a", "model": "claude-haiku-5-5", "usage": {"input_tokens": 5}}},
        {"type": "assistant", "message": {"id": "b", "model": "claude-haiku-5-5", "usage": usage}},
        {"type": "user", "message": {"content": "hi"}},
    )) + "\n")
    assert claude.context_tokens(log) == (120_010, 200_000)

    rollout = tmp_path / "rollout-2026-11-25T03-05-00-x.jsonl"
    info = {"last_token_usage": {"input_tokens": 150_000, "output_tokens": 500}, "model_context_window": 258_400}
    rollout.write_text(json.dumps({"type": "event_msg", "payload": {"type": "token_count", "info": info}}) + "\n")
    assert CodexAgentCli(home=tmp_path).context_tokens(rollout) == (150_500, 258_400)
