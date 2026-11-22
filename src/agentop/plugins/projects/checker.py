"""Watches Projects sessions on the scan tick: a Coordinator turn that ends without `agentop yield` is nudged,
and a task's session that goes quiet without reporting is reminded, then the Coordinator is told.

Turns are told apart by the screen, as the attention plugin does (tmux only, no transcripts).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field

from agentop import projects
from agentop.plugins.attention import Watcher

from . import pursuit

# An owner typing a prompt also changes the screen; waiting this long keeps a nudge out of a half-typed one.
QUIET_SECONDS = 60.0
MAX_NUDGES = 2

NUDGE = (
    "[agentop] Your turn ended without a stop reason. If your context was just cleared, first read AGENTS.md, "
    "COORDINATOR_SKILL.md and STATE.md. Then either take the next best action toward the objective, or, if you "
    "really must stop, record why with agentop yield done, waiting, owner or blocked (agentop yield --help)."
)


@dataclass
class Turn:
    started: float
    busy: bool = False
    handled: float | None = None
    nudges: int = 0
    reminded: dict = field(default_factory=dict)


class Checker:
    def __init__(self, deliver: Callable[[str, str, str], None], quiet_seconds: float = QUIET_SECONDS, watcher=None):
        self._deliver = deliver
        self.quiet_seconds = quiet_seconds
        self.watcher = watcher or Watcher(quiet_seconds=4.0)
        self.turns: dict[str, Turn] = {}
        self._rows: list[dict] = []

    def reset(self) -> None:
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
        self.watcher.tick(rows, now)
        live = {row["id"] for row in rows}
        for sid in self.turns.keys() - live:
            del self.turns[sid]
        for row in rows:
            screen = self.watcher.screens.get(row["id"])
            if screen is None:
                continue
            turn = self.turns.setdefault(row["id"], Turn(started=now))
            if screen.busy and not turn.busy:
                turn.started = screen.changed_at
            turn.busy = screen.busy
            since = screen.waiting_since
            if since is None or turn.handled == since or now - since < self.quiet_seconds:
                continue
            turn.handled = since
            if row.get("display_name") == projects.COORDINATOR:
                self._coordinator(row, turn)
            else:
                self._member(row, turn)

    def _coordinator(self, row: dict, turn: Turn) -> None:
        project = row["project"]
        yielded = [e for e in pursuit.events(project, turn.started) if e["kind"] == "yield"]
        if yielded:
            turn.nudges = 0
            return
        if turn.nudges >= MAX_NUDGES:
            if turn.nudges == MAX_NUDGES:
                pursuit.log(project, "nudge.ignored")
                turn.nudges += 1
            return
        turn.nudges += 1
        pursuit.log(project, "nudge", count=turn.nudges)
        self._deliver(project, row, NUDGE)

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
                self._deliver(project, coordinator, (
                    f"[agentop task {task['id']}] {task['session']} has gone quiet twice without reporting round "
                    f"{len(task['rounds'])}. Look at its screen and decide: answer it, reject with feedback, or fail it."
                ))
