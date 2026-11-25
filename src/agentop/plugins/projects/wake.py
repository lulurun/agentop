"""Clearing a Coordinator's context: at a wake, before a task event is typed, and at a safe point as a safety net.

A clear is sent only when it is safe, and verified before the next prompt is typed. See docs/0_13_projects.md, section 4.3.
"""

from __future__ import annotations

import fcntl
import time
from contextlib import contextmanager
from dataclasses import dataclass

from agentop import projects
from agentop.agentclis import get_agent
from agentop.agentclis.base import AgentCli
from agentop.tmux import CapturePane

from . import pursuit

CLEAR_TIMEOUT = 20.0
# Codex's spinner ticks every second while a turn runs, Claude's more often, so a screen still this long is between turns.
IDLE_SECONDS = 5.0
POLL_SECONDS = 0.5
# Right after a clear the tool is still redrawing, and a prompt typed into that can be lost.
SETTLE_SECONDS = 1.0
SETTLE_TIMEOUT = 5.0
CONTEXT_SHARE = 0.6

CLEARED = "[agentop] Your context was cleared. Run agentop coordinator status (it starts with your Next), then handle this:"
UNVERIFIED = (
    "[agentop] agentop typed /clear but couldn't confirm it within {seconds:.0f}s, so your context may not have been "
    "cleared. If you lack context, run agentop coordinator status (it starts with your Next). Then handle this:"
)
RESUME = (
    "[agentop] Your context was cleared, as your yield asked. Run agentop coordinator status (it starts with your Next), "
    "then take the next best action toward the direction."
)

_sleep = time.sleep
_clock = time.monotonic


@dataclass
class Pane:
    agent: AgentCli
    tmux: str
    pid: int | None = None

    def screen(self, escapes: bool = False) -> str:
        return CapturePane.screen(self.tmux, escapes)

    def type(self, text: str) -> None:
        self.agent.deliver(self.tmux, text)

    def conversation(self) -> str | None:
        return self.agent.conversation_id(self.pid)


def pane_of(row: dict) -> Pane | None:
    agent = get_agent(row.get("tool") or "")
    name = (row.get("tmux") or {}).get("session")
    return Pane(agent, name, row.get("pid")) if agent is not None and name else None


@contextmanager
def _one_at_a_time(project: str):
    # Two reports at once: the second sees the first's wake, so it doesn't clear what the first just typed.
    folder = projects.meta_dir(project)
    folder.mkdir(parents=True, exist_ok=True)
    with open(folder / "wake.lock", "w") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        yield


def blocker(project: str, explicit: bool = False, task: str | None = None) -> str | None:
    """Why the record says clearing isn't safe now, or None (spec 9.2). `task` is the one whose event is being delivered."""
    found = pursuit.events(project)
    last_yield = next((e for e in reversed(found) if e["kind"] == "yield"), None)
    last_wake = next((e for e in reversed(found) if e["kind"] == pursuit.WAKE), None)
    if last_yield is None or (last_wake is not None and last_wake["at"] >= last_yield["at"]):
        return "no yield since the last event"
    if not explicit and last_yield.get("type") not in pursuit.CLEARING_KINDS:
        return f"the last yield was {last_yield.get('type')}"
    if not explicit and last_yield.get("keep"):
        return "the last yield asked to keep the context"
    others = [t["session"] for t in pursuit.open_tasks(project) if not pursuit.awaiting_report(t) and t["id"] != task]
    if others:
        return f"{', '.join(others)} reported and wait for a decision"
    problems = pursuit.problems(project)
    if problems:
        return f"the track checks fail: {problems[0]}"
    return None


def idle(pane: Pane, seconds: float = IDLE_SECONDS) -> bool:
    """Whether the screen stays the same for `seconds`. The one place the hook state can replace the screen."""
    first = pane.screen()
    deadline = _clock() + seconds
    while _clock() < deadline:
        _sleep(POLL_SECONDS)
        if pane.screen() != first:
            return False
    return True


def screen_blocker(pane: Pane) -> str | None:
    """Why the screen says clearing isn't safe now: a turn running, or a draft in the input line."""
    if pane.agent.prompt_mark is None:
        return f"{pane.agent.name}'s screen isn't read, so it isn't cleared"
    if not idle(pane):
        return "the Coordinator is busy"
    draft = pane.agent.draft(pane.screen(escapes=True))
    if draft is None:
        return "the input line wasn't found on the screen"
    if draft:
        return "a draft is in the prompt"
    return None


def _settle(pane: Pane) -> None:
    deadline = _clock() + SETTLE_TIMEOUT
    last, since = pane.screen(), _clock()
    while _clock() < deadline:
        _sleep(POLL_SECONDS)
        screen = pane.screen()
        if screen != last:
            last, since = screen, _clock()
        elif _clock() - since >= SETTLE_SECONDS:
            return


def clear(pane: Pane, timeout: float = CLEAR_TIMEOUT) -> str | None:
    """Type /clear and wait for a sign it landed. Returns the sign, or None if none came within `timeout`."""
    before_id, before = pane.conversation(), pane.screen()
    pane.type("/clear")
    deadline = _clock() + timeout
    how = None
    while how is None and _clock() < deadline:
        _sleep(POLL_SECONDS)
        now_id = pane.conversation()
        if before_id and now_id and now_id != before_id:
            how = "a new session id"
            continue
        screen = pane.screen()
        if screen != before and pane.agent.cleared(screen):
            how = "the cleared screen"
    if how is not None:
        _settle(pane)
    return how


def compose(project: str, head: str, text: str = "") -> str:
    """The prompt after a clear: its first line, the Next verbatim, then the event."""
    parts = [head]
    found = pursuit.read_next(project)
    if found:
        parts.append(found)
    if text.strip():
        parts.append(text.strip())
    return "\n\n".join(parts)


def _clear_logged(project: str, pane: Pane, **fields) -> str | None:
    pursuit.log(project, "clear.sent", **fields)
    started = _clock()
    how = clear(pane)
    if how is None:
        pursuit.log(project, "clear.skipped", reason=f"not verified within {CLEAR_TIMEOUT:.0f}s", **fields)
    else:
        pursuit.log(project, "clear.verified", how=how, seconds=round(_clock() - started, 1), **fields)
    return how


def deliver_event(project: str, pane: Pane, text: str, source: str, task: str | None = None) -> str:
    """Type a task event into the Coordinator, clearing its context first when safe (spec 9.1). Returns what happened."""
    with _one_at_a_time(project):
        reason = blocker(project, task=task)
        if reason is None and pursuit.cleared(project):
            outcome, prompt = "already cleared", compose(project, CLEARED, text)
        else:
            reason = reason or screen_blocker(pane)
            if reason is not None:
                pursuit.log(project, "clear.skipped", reason=reason, source=source)
                outcome, prompt = "not cleared", (pursuit.WAKE_NOTE if pursuit.cleared(project) else "") + text
            elif _clear_logged(project, pane, source=source):
                outcome, prompt = "cleared", compose(project, CLEARED, text)
            else:
                outcome, prompt = "unverified", compose(project, UNVERIFIED.format(seconds=CLEAR_TIMEOUT), text)
        pane.type(prompt)
        pursuit.log(project, pursuit.WAKE, source=source, cleared=outcome)
        return outcome


def context_share(row: dict, agent: AgentCli) -> tuple[int, int] | None:
    session_id = row.get("session_id")
    if not session_id:
        return None
    _, files = agent.transcript_files(session_id)
    return agent.context_tokens(files[0]) if files else None


def clear_reason(project: str, row: dict, agent: AgentCli) -> tuple[str, bool] | None:
    """Why the Coordinator should be cleared at this safe point (spec 9.3), and whether its yield asked; None if not."""
    if pursuit.cleared(project):
        return None
    if pursuit.pending_clear(project) is not None:
        return "the yield asked for it", True
    found = pursuit.events(project)
    last_clear = max((e["at"] for e in found if e["kind"] == "clear.verified"), default=0.0)
    closed = [e for e in found if e["kind"] == "milestone.close" and e["at"] > last_clear]
    if closed:
        return f"M{closed[-1].get('milestone')} was closed", False
    share = context_share(row, agent)
    limit = (projects.get(project) or {}).get("clear_context_at", CONTEXT_SHARE)
    if share and share[1] and share[0] >= float(limit) * share[1]:
        return f"the context holds {share[0]} of {share[1]} tokens", False
    return None


def safety_clear(project: str, pane: Pane, why: str, explicit: bool = False) -> str:
    """Clear at a safe point with no event to deliver; after a yield --clear, tell it to carry on. Returns what happened."""
    with _one_at_a_time(project):
        reason = blocker(project, explicit=explicit) or screen_blocker(pane)
        if reason is not None:
            pursuit.log(project, "clear.skipped", reason=reason, why=why)
            return "not cleared"
        if not _clear_logged(project, pane, why=why):
            return "unverified"
        if explicit:
            pane.type(compose(project, RESUME))
            pursuit.log(project, pursuit.WAKE, source="resume", cleared="cleared")
        return "cleared"
