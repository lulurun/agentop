"""Clearing the Coordinator's context: on wake, as a safety net, and the Next that survives it (spec 9)."""

import json
import time
from types import SimpleNamespace

import pytest

from agentop import coordinator, ops, projects
from agentop.agentclis.base import _plain as plain
from agentop.agentclis.claude import ClaudeAgentCli
from agentop.agentclis.codex import CodexAgentCli
from agentop.plugins.attention import Watcher
from agentop.plugins.projects import cli, pursuit, track, wake
from agentop.plugins.projects.checker import Checker

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


class FakePane(wake.Pane):
    """A Coordinator's pane: its screen, what is typed into it, and its conversation ID."""

    def __init__(self, tool="claude", screen="old", busy=False, clears=True, new_id=True):
        agent = ClaudeAgentCli() if tool == "claude" else CodexAgentCli()
        super().__init__(agent, "coord-1", 4242)
        self.screens = SCREENS[tool]
        self.current = self.screens[screen]
        self.busy, self.clears, self.new_id = busy, clears, new_id and tool == "claude"
        self.id = "conv-1"
        self.typed = []
        self.ticks = 0

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
    ("no yield", "no yield since the last event"),
    ("draft", "a draft is in the prompt"),
    ("other report", "api reported and wait for a decision"),
    ("blocked yield", "the last yield was blocked"),
    ("keep", "the last yield asked to keep the context"),
    ("track", "the track checks fail"),
])
def test_without_a_safe_point_the_report_is_typed_without_a_clear(alpha, monkeypatch, case, reason):
    task()
    pane = FakePane(busy=case == "busy", screen="draft" if case == "draft" else "old")
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
    assert fast[0] - started >= wake.IDLE_SECONDS + wake.CLEAR_TIMEOUT


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


def test_a_placeholder_screen_hint_is_not_a_draft(alpha):
    for tool in ("claude", "codex"):
        agent = FakePane(tool).agent
        assert agent.draft(SCREENS[tool]["hint"]) is False
        assert agent.draft(SCREENS[tool]["draft"]) is True
        assert agent.cleared(plain(SCREENS[tool]["cleared"]))
        assert not agent.cleared(plain(SCREENS[tool]["old"]))


# --- the safety net (9.3), at the end of a Coordinator turn


class Screens:
    def __init__(self):
        self.text = "a"
        self.activity = 0

    def list_panes(self):
        return {"coord-1": (self.activity, "80x24", "%1")}

    def capture(self, pane):
        return self.text


def coordinator_row():
    return {"id": "coord-1", "status": "running", "managed": True, "project": "alpha", "tool": "claude",
            "display_name": "Coordinator", "tmux": {"session": "coord-1"}, "session_id": "conv-1"}


def turn_ends(pane, tokens=None, monkeypatch=None, at=None):
    """One Coordinator turn: output, then a still screen past the checker's wait. Returns what it typed."""
    screens = Screens()
    ran = []
    checker = Checker(lambda p, row, text: ran.append(text), watcher=Watcher(screens.list_panes, screens.capture),
                      pane=lambda row: pane, run=lambda project, fn: ran.append(fn()))
    now = time.time() if at is None else at
    checker.tick([coordinator_row()], now)
    screens.activity = int(now + 1)
    screens.text += "x"
    checker.tick([coordinator_row()], now + 1)
    checker.tick([coordinator_row()], now + 3)
    assert pane.typed == []
    checker.tick([coordinator_row()], now + 6)
    checker.tick([coordinator_row()], now + 12)
    checker.tick([coordinator_row()], now + 30)
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
    fields = dict.fromkeys(("evidence", "meanwhile", "need", "tried", "note"), "")
    cli.run_yield(SimpleNamespace(kind="owner", project=None, clear=True, keep=False, question="Ship it?",
                                  recommend="Yes", default="Ship Friday", next="Draft the release notes while I wait.",
                                  **fields))
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
