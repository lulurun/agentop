"""Watches Projects sessions on the scan tick: a Coordinator turn that cleared its context is told to carry on,
and a quiet task session is reminded, then the Coordinator told.

Turns use the attention watcher, including lifecycle events and screen fallback.
"""

from __future__ import annotations

import queue
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from agentop import projects
from agentop.plugins.attention import Watcher

from . import pursuit

# An owner typing a prompt also changes the screen; waiting this long keeps a prompt out of a half-typed one.
QUIET_SECONDS = 60.0
# Codex refuses /clear while a turn runs, so a yield's clear waits for the screen to go still; its spinner
# ticks every second while busy.
CLEAR_SECONDS = 5.0
# A task session can sit on a still screen through a long test run or model call, so a short wait nags busy agents.
TASK_QUIET_SECONDS = 2 * 3600.0

RESUME = (
    "[agentop] Your context was cleared. Read AGENTS.md and COORDINATOR_SKILL.md, run agentop coordinator status, "
    "then take the next best action toward the direction."
)


@dataclass
class Turn:
    # A clear counts for the next turn end after it. The screen can't bound a turn's start: a short turn's
    # clear can land before the tick that sees its output, and a pause before the final text ends busy.
    after: float
    handled: float | None = None
    reminded: dict = field(default_factory=dict)


class Checker:
    def __init__(self, deliver: Callable[[str, str, str], None], quiet_seconds: float = QUIET_SECONDS,
                 task_quiet_seconds: float = TASK_QUIET_SECONDS, watcher=None, status_feed=None):
        self._deliver = deliver
        self.quiet_seconds = quiet_seconds
        self.task_quiet_seconds = task_quiet_seconds
        self.status_feed = status_feed
        self.subscriptions = {}
        self.statuses = {}
        self.watcher = watcher or Watcher(quiet_seconds=4.0)
        self.turns: dict[str, Turn] = {}
        self._rows: list[dict] = []

    def reset(self) -> None:
        for sid, subscription in self.subscriptions.items():
            self.status_feed.unsubscribe(sid, subscription)
        self.subscriptions = {}
        self.statuses = {}
        self.watcher.reset()
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
                owned[project] = bool(found and found.get("owner") == "projects")
            if owned[project]:
                rows.append(row)
        self._rows = rows
        live = {row["id"] for row in rows}
        if self.status_feed is None:
            self.watcher.tick(rows, now)
        else:
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
            if self.status_feed is None:
                screen = self.watcher.screens.get(row["id"])
                if screen is None:
                    continue
                state, since = screen.state, screen.waiting_since
            else:
                status = self.statuses.get(row["id"])
                if status is None:
                    continue
                state = status.state
                since = status.since if state in ("done", "input") else None
            turn = self.turns.setdefault(row["id"], Turn(after=now))
            coordinator = row.get("display_name") == projects.COORDINATOR
            if state == "input":
                continue
            if coordinator and since is not None and now - since >= CLEAR_SECONDS:
                self._clear(row)
            quiet = self.quiet_seconds if coordinator else self.task_quiet_seconds
            if since is None or turn.handled == since or now - since < quiet:
                continue
            turn.handled = since
            if coordinator:
                self._coordinator(row, turn)
                turn.after = since
            else:
                self._member(row, turn)

    def _coordinator(self, row: dict, turn: Turn) -> None:
        project = row["project"]
        # Turns without a yield aren't counted: an idle screen also changes (Codex's usage warnings, /status, a draft).
        # After done or waiting there is nothing to carry on with, and a prompt would only yield and clear again.
        if any(e["kind"] == "clear" and e.get("type") not in pursuit.CLEARING_KINDS
               for e in pursuit.events(project, turn.after)):
            pursuit.log(project, "resume")
            self._deliver(project, row, RESUME)

    def _clear(self, row: dict) -> None:
        project = row["project"]
        pending = pursuit.pending_clear(project)
        if pending is not None:
            self._deliver(project, row, "/clear")
            pursuit.log(project, "clear", type=pending.get("type"))

    def _member(self, row: dict, turn: Turn) -> None:
        project = row["project"]
        task = pursuit.task_for(project, row.get("display_name") or "")
        if task is None or not pursuit.awaiting_report(task):
            return
        key = (task["id"], len(task["rounds"]))
        times = turn.reminded.get(key, 0)
        turn.reminded[key] = times + 1
        if times == 0:
            self._deliver(project, row, (
                f"[agentop task {task['id']}] You have gone quiet without reporting. If you are done, run "
                "agentop task report \"…\". If something stops you, run agentop task report --blocked \"…\"."
            ))
        elif times == 1:
            pursuit.log(project, "task.quiet", task=task["id"], session=task["session"])
            coordinator = next(
                (r for r in self._rows if r.get("project") == project and r.get("display_name") == projects.COORDINATOR),
                None,
            )
            if coordinator is not None:
                self._deliver(project, coordinator, pursuit.wake_note(project) + (
                    f"[agentop task {task['id']}] {task['session']} has gone quiet twice without reporting round "
                    f"{len(task['rounds'])}. Look at its screen and decide: answer it, reject with feedback, or fail it."
                ))
