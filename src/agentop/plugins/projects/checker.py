"""Clear Coordinators at safe points and bring task sessions needing attention to them."""

from __future__ import annotations

import logging
import queue
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from agentop import projects
from agentop.service.config import parse_duration
from agentop.status_feed import SessionStatus

from . import pursuit, wake

LOG = logging.getLogger(__name__)

# Codex refuses /clear while a turn runs, and for a moment after it.
CLEAR_SECONDS = 2.0
# Each can be set per project in project.json (seconds, or "2m", "30m", "2h"; 0 turns it off).
THRESHOLDS = {
    "task_input_after": 120.0,
    "task_working_after": 30 * 60.0,
    "task_unreported_after": 2 * 3600.0,
}


@dataclass
class Turn:
    netted: float | None = None
    unknown: bool = False
    told: set = field(default_factory=set)


def thresholds(project: dict | None) -> dict[str, float | None]:
    """The project's thresholds in seconds, None where turned off."""
    out = {}
    for key, default in THRESHOLDS.items():
        value = (project or {}).get(key, default)
        try:
            out[key] = parse_duration(value)
        except ValueError:
            LOG.warning("%s: %s=%r is not a duration; using %ss", (project or {}).get("name"), key, value, default)
            out[key] = default
    return out


def feed_state(status: SessionStatus | None) -> SessionStatus | None:
    """A task session's state for notices: the hooks', or the screen's when the hooks say working but it disagrees.

    A notice is cheap, unlike a clear, so the screen fallback's Codex approval prompt and lost-Stop guess count here.
    """
    if wake.known(status) is not None:
        return status
    if status is not None and status.source == "screen" and status.event:
        return SessionStatus(status.session_id, "input" if status.state == "input" else "working", status.since,
                             status.source, status.event)
    return None


def age(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    return f"{seconds // 3600}h {seconds % 3600 // 60}m" if seconds % 3600 >= 60 else f"{seconds // 3600}h"


class Checker:
    def __init__(self, deliver: Callable[[str, dict, str], None], status_feed, pane=None, run=None):
        self._deliver = deliver
        self.status_feed = status_feed
        self.subscriptions = {}
        self.statuses = {}
        self.turns: dict[str, Turn] = {}
        self._rows: list[dict] = []
        self._pane = pane or (lambda row: wake.pane_of(row, status_feed.latest))
        self._run = run or self._in_thread
        self._busy: set[str] = set()
        self._busy_lock = threading.Lock()

    def _in_thread(self, project: str, fn: Callable[[], object]) -> bool:
        """A clear waits for its sign for up to half a minute, so it runs beside the tick, one per project."""
        with self._busy_lock:
            if project in self._busy:
                return False
            self._busy.add(project)

        def run():
            try:
                fn()
            except Exception as exc:  # noqa: BLE001 - a failed clear or paste is logged, and the next tick goes on
                LOG.warning("%s: %s", project, exc)
            finally:
                with self._busy_lock:
                    self._busy.discard(project)

        threading.Thread(target=run, daemon=True, name=f"wake-{project}").start()
        return True

    def reset(self) -> None:
        for sid, subscription in self.subscriptions.items():
            self.status_feed.unsubscribe(sid, subscription)
        self.subscriptions = {}
        self.statuses = {}
        self.turns = {}

    def tick(self, sessions: list[dict], now: float | None = None) -> None:
        now = time.time() if now is None else now
        owned = {}
        rows = []
        for row in sessions:
            project = row.get("project")
            if not row.get("managed") or row.get("readonly") or not project or project == projects.DEFAULT:
                continue
            if project not in owned:
                found = projects.get(project)
                owned[project] = thresholds(found) if found and found.get("owner") == "projects" else None
            if owned[project] is not None:
                rows.append(row)
        self._rows = rows
        live = {row["id"] for row in rows}
        for sid in self.subscriptions.keys() - live:
            self.status_feed.unsubscribe(sid, self.subscriptions.pop(sid))
            self.statuses.pop(sid, None)
        for sid in live:
            if sid not in self.subscriptions:
                self.subscriptions[sid] = self.status_feed.subscribe(sid)
            try:
                self.statuses[sid] = self.subscriptions[sid].get_nowait()
            except queue.Empty:
                pass
        for sid in self.turns.keys() - live:
            del self.turns[sid]
        for row in rows:
            turn = self.turns.setdefault(row["id"], Turn())
            if row.get("display_name") != projects.COORDINATOR:
                self._task_session(row, turn, owned[row["project"]], now)
                continue
            status = wake.known(self.statuses.get(row["id"]))
            if status is None:
                if not turn.unknown:
                    self._unknown(row)
                turn.unknown = True
                continue
            turn.unknown = False
            if status.state == "done" and now - status.since >= CLEAR_SECONDS and turn.netted != status.since:
                turn.netted = status.since
                self._net(row)

    def _task_session(self, row: dict, turn: Turn, limits: dict, now: float) -> None:
        """At most one notice per episode: a state, from the `since` of the event that began it."""
        status = feed_state(self.statuses.get(row["id"]))
        if status is None or status.state not in ("input", "working", "done"):
            return
        waited = now - status.since
        episode = (status.state, status.since)
        due = [key for key in (("task_input_after",) if status.state == "input" else
                               ("task_working_after",) if status.state == "working" else
                               ("task_unreported_after",))
               if limits[key] is not None and waited >= limits[key] and (key, *episode) not in turn.told]
        if not due:
            return
        task = pursuit.task_for(row["project"], row.get("display_name") or "")
        for key in due:
            if task is None or (status.state == "done" and not pursuit.awaiting_report(task)) \
                    or self._notice(key, row, task, status, waited):
                turn.told.add((key, *episode))

    def _notice(self, key: str, row: dict, task: dict, status: SessionStatus, waited: float) -> bool:
        project, session, n = row["project"], task["session"], len(task["rounds"])
        head = f"[task {task['id']}] {session}"
        if key == "task_input_after":
            kind, text = "task.input", (
                f"{head} has waited for input for {age(waited)}"
                f"{' (an approval prompt on its screen)' if status.source == 'screen' else ''}. "
                "Inspect its recent session output: answer it, or tell the owner if it needs them."
            )
        elif key == "task_working_after":
            kind, text = "task.silent", (
                f"{head} has been working with no new event for {age(waited)}"
                f"{' (its screen looks idle)' if status.source == 'screen' else ''}. A tool call may hang or its Stop "
                "was lost. Inspect its recent session output and decide: wait, nudge it, or fail it."
            )
        else:
            kind, text = "task.quiet", (
                f"{head} ended its turn {age(waited)} ago without reporting round {n}. "
                "Inspect its recent session output and decide whether to wait, nudge it, or fail the task."
            )
        coordinator = next(
            (r for r in self._rows if r.get("project") == project and r.get("display_name") == projects.COORDINATOR),
            None,
        )
        if coordinator is None:
            pursuit.log(project, kind, task=task["id"], session=session, seconds=round(waited), told=False)
            return True
        if not self._tell_coordinator(project, coordinator, text, kind, task["id"]):
            return False
        pursuit.log(project, kind, task=task["id"], session=session, seconds=round(waited), told=True)
        return True

    def _net(self, row: dict) -> None:
        """At the end of each Coordinator turn: the safety net of spec 9.3, and the clear a yield asked for."""
        project = row["project"]
        pane = self._pane(row)
        if pane is None:
            return
        found = wake.clear_reason(project, row, pane.agent)
        if found is not None:
            why, explicit = found
            self._run(project, lambda: wake.safety_clear(project, pane, why, explicit))

    def _unknown(self, row: dict) -> None:
        """A Coordinator whose status turned unknown is never cleared on a guess; say so once if a clear was due."""
        project = row["project"]
        pane = self._pane(row)
        found = wake.clear_reason(project, row, pane.agent) if pane is not None else None
        if found is not None:
            pursuit.log(project, "clear.skipped", reason=wake.UNKNOWN, why=found[0])

    def _tell_coordinator(self, project: str, row: dict, text: str, source: str, task: str | None = None) -> bool:
        """Whether it was sent or started: a wake already running for the project defers it to a later tick."""
        pane = self._pane(row)
        if pane is None:
            self._deliver(project, row, pursuit.wake_note(project, source) + text)
            return True
        if pane.agent is not None and pane.agent.draft(pane.screen(escapes=True)) is True:
            return False
        return self._run(project, lambda: wake.deliver_event(project, pane, text, source, task)) is not False
