"""Watches Projects sessions on the scan tick: a Coordinator turn that ends without `agentop yield` is logged,
one that cleared its context is told to carry on, and a quiet task session is reminded, then the Coordinator told.

Turns are told apart by the screen, as the attention plugin does (tmux only, no transcripts).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field

from agentop import projects
from agentop.plugins.attention import Watcher

from . import pursuit

# An owner typing a prompt also changes the screen; waiting this long keeps a prompt out of a half-typed one.
QUIET_SECONDS = 60.0

RESUME = (
    "[agentop] Your context was cleared. Read AGENTS.md, COORDINATOR_SKILL.md and STATE.md, run agentop task list, "
    "then take the next best action toward the objective."
)


@dataclass
class Turn:
    # A yield counts for the next turn end after it. The screen can't bound a turn's start: a short turn's
    # yield can land before the tick that sees its output, and a pause before the final text ends busy.
    after: float
    handled: float | None = None
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
            turn = self.turns.setdefault(row["id"], Turn(after=now))
            since = screen.waiting_since
            if since is None or turn.handled == since or now - since < self.quiet_seconds:
                continue
            turn.handled = since
            if row.get("display_name") == projects.COORDINATOR:
                self._coordinator(row, turn)
                turn.after = since
            else:
                self._member(row, turn)

    def _coordinator(self, row: dict, turn: Turn) -> None:
        project = row["project"]
        kinds = {e["kind"] for e in pursuit.events(project, turn.after)}
        if "clear" in kinds:
            pursuit.log(project, "resume")
            self._deliver(project, row, RESUME)
        elif "yield" not in kinds:
            # Not typed into the session: the screen can't tell a turn from an owner's /clear, a draft or a popup.
            pursuit.log(project, "turn.unreasoned")

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
