"""Watches Projects sessions on the scan tick: a Coordinator at a safe point is cleared when its yield asked,
its context is large or a milestone closed; a quiet task session is reminded, then the Coordinator told.

Turns come from the attention plugin's status feed (lifecycle events, with its screen fallback).
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from agentop import projects
from agentop.plugins.attention import Watcher

from . import pursuit, wake

LOG = logging.getLogger(__name__)

# Codex refuses /clear while a turn runs; its spinner ticks every second while busy.
CLEAR_SECONDS = 5.0
# A task session can sit on a still screen through a long test run or model call, so a short wait nags busy agents.
TASK_QUIET_SECONDS = 2 * 3600.0


@dataclass
class Turn:
    handled: float | None = None
    netted: float | None = None
    reminded: dict = field(default_factory=dict)


class Checker:
    def __init__(self, deliver: Callable[[str, dict, str], None], task_quiet_seconds: float = TASK_QUIET_SECONDS,
                 watcher=None, pane=None, run=None, status_feed=None):
        self._deliver = deliver
        self.task_quiet_seconds = task_quiet_seconds
        self.status_feed = status_feed
        self.subscriptions = {}
        self.statuses = {}
        self.watcher = watcher or Watcher(quiet_seconds=4.0)
        self.turns: dict[str, Turn] = {}
        self._rows: list[dict] = []
        self._pane = pane or wake.pane_of
        self._run = run or self._in_thread
        self._busy: set[str] = set()
        self._busy_lock = threading.Lock()

    def _in_thread(self, project: str, fn: Callable[[], object]) -> None:
        """A clear waits on the screen for up to half a minute, so it runs beside the tick, one per project."""
        with self._busy_lock:
            if project in self._busy:
                return
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
            turn = self.turns.setdefault(row["id"], Turn())
            if state == "input":
                continue
            if row.get("display_name") == projects.COORDINATOR:
                if since is not None and now - since >= CLEAR_SECONDS and turn.netted != since:
                    turn.netted = since
                    self._net(row)
                continue
            if since is None or turn.handled == since or now - since < self.task_quiet_seconds:
                continue
            turn.handled = since
            self._member(row, turn)

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

    def _tell_coordinator(self, project: str, row: dict, text: str, source: str, task: str | None = None) -> None:
        pane = self._pane(row)
        if pane is None:
            self._deliver(project, row, pursuit.wake_note(project, source) + text)
        else:
            self._run(project, lambda: wake.deliver_event(project, pane, text, source, task))

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
                f"[task {task['id']}] You have gone quiet without reporting. If you are done, run "
                "agentop coordinator task report \"…\". If something stops you, run agentop coordinator task report --blocked \"…\"."
            ))
        elif times == 1:
            pursuit.log(project, "task.quiet", task=task["id"], session=task["session"])
            coordinator = next(
                (r for r in self._rows if r.get("project") == project and r.get("display_name") == projects.COORDINATOR),
                None,
            )
            if coordinator is not None:
                self._tell_coordinator(project, coordinator, (
                    f"[task {task['id']}] {task['session']} has gone quiet twice without reporting round "
                    f"{len(task['rounds'])}. Look at its screen and decide: answer it, reject with feedback, or fail it."
                ), "task.quiet", task["id"])
