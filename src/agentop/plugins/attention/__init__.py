"""Waiting for input: marks sessions whose agent was working and has stopped.

A session is **busy** while its screen keeps changing, and becomes **waiting**
once a busy screen has stayed the same for `QUIET_SECONDS`. Any later change
(the agent carrying on, or someone typing) clears it. An agent that has been
idle all along is never marked: only the change from busy to quiet is.

It runs on the service's `scan` tick (2s) and adds no loop of its own. Each
tick costs one `tmux list-panes -a` for every session at once, plus one
`capture-pane` of the visible screen (no scrollback) of the first pane in
the first window, where agentop starts the agent, for each session whose
pane printed something since its last capture. Idle sessions cost nothing
beyond the shared list. A resize (opening the session in a differently sized
terminal) makes the agent redraw, so the screen after a resize is taken as the
new baseline rather than as work.

Turning the plugin off stops its handler, so it then costs nothing at all.
"""

from __future__ import annotations

import hashlib
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from agentop import tmux

QUIET_SECONDS = 4.0
# Ticks after a resize whose changes are taken as the agent redrawing.
SETTLE_TICKS = 1
PANE_FORMAT = "#{window_index}\t#{pane_index}\t#{pane_id}\t#{window_activity}\t#{pane_width}x#{pane_height}\t#{session_name}"


def list_panes() -> dict[str, tuple[int, str, str]] | None:
    """{tmux session: (last output, epoch seconds; size; pane ID)} for each session's first pane, or None if tmux failed.

    agentop starts an agent in the first pane of a session's first window, so
    that pane is the one watched, whichever pane or window is active.
    """
    try:
        r = tmux.run("list-panes", "-a", "-F", PANE_FORMAT, timeout=3, text=True)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return {} if "no server" in r.stderr or "No such file" in r.stderr else None
    first: dict[str, tuple[tuple[int, int], int, str, str]] = {}
    for line in r.stdout.splitlines():
        parts = line.split("\t", 5)
        if len(parts) != 6 or not all(p.isdigit() for p in (parts[0], parts[1], parts[3])):
            continue
        window, pane, pane_id, activity, size, name = parts
        order = (int(window), int(pane))
        if name not in first or order < first[name][0]:
            first[name] = (order, int(activity), size, pane_id)
    return {name: (activity, size, pane_id) for name, (_, activity, size, pane_id) in first.items()}


def capture(pane_id: str) -> str:
    """The visible text of one pane, by its ID (`%12`), which tmux never matches by prefix."""
    try:
        r = tmux.run("capture-pane", "-p", "-t", pane_id, text=True)
    except (OSError, subprocess.SubprocessError):
        return ""
    return r.stdout if r.returncode == 0 else ""


def digest(text: str) -> str:
    return hashlib.blake2b(text.encode(), digest_size=16).hexdigest()


@dataclass
class Screen:
    digest: str
    size: str
    captured_at: float
    busy: bool = False
    changed_at: float = 0.0
    waiting_since: float | None = None
    settle: int = 0


class Watcher:
    """The busy and waiting state of each session, updated once a tick."""

    def __init__(
        self,
        list_panes: Callable[[], dict | None] = list_panes,
        capture: Callable[[str], str] = capture,
        quiet_seconds: float = QUIET_SECONDS,
    ):
        self._list_panes = list_panes
        self._capture = capture
        self.quiet_seconds = quiet_seconds
        self.screens: dict[str, Screen] = {}
        self.stats = {"at": None, "sessions": 0, "captured": 0, "ms": 0.0, "skipped": 0}
        self._lock = threading.Lock()

    def waiting(self) -> dict[str, float]:
        """{session ID: when it started waiting}."""
        return {sid: s.waiting_since for sid, s in self.screens.items() if s.waiting_since is not None}

    def reset(self) -> None:
        with self._lock:
            self.screens = {}

    def tick(self, sessions: list[dict], now: float | None = None) -> bool:
        """Look at the sessions once. Returns whether the set of waiting sessions changed.

        Ticks don't wait for each other, so one that finds the last still running is skipped.
        """
        if not self._lock.acquire(blocking=False):
            self.stats["skipped"] += 1
            return False
        try:
            return self._tick(sessions, time.time() if now is None else now)
        finally:
            self._lock.release()

    def _tick(self, sessions: list[dict], now: float) -> bool:
        started = time.perf_counter()
        before = set(self.waiting())
        targets = {
            row["id"]: row["tmux"]["session"]
            for row in sessions
            if row.get("status") == "running" and (row.get("tmux") or {}).get("session")
        }
        for sid in self.screens.keys() - targets.keys():
            del self.screens[sid]

        panes = self._list_panes()
        captured = 0
        if panes is not None:
            for sid, name in targets.items():
                pane = panes.get(name)
                if pane is None:
                    continue
                activity, size, pane_id = pane
                screen = self.screens.get(sid)
                if screen is None:
                    # The first look is only a baseline, so a restart marks nothing.
                    self.screens[sid] = Screen(digest(self._capture(pane_id)), size, now)
                    captured += 1
                    continue
                if size != screen.size:
                    screen.size = size
                    screen.settle = SETTLE_TICKS + 1
                # tmux keeps the last output to the second, so output in the second
                # of the last capture may have come after it: look once more then.
                if activity >= int(screen.captured_at) or screen.settle:
                    text_digest = digest(self._capture(pane_id))
                    captured += 1
                    screen.captured_at = now
                    if screen.settle:
                        screen.settle -= 1
                        screen.digest = text_digest
                    elif text_digest != screen.digest:
                        screen.digest = text_digest
                        screen.busy = True
                        screen.changed_at = now
                        screen.waiting_since = None
                if screen.busy and now - screen.changed_at >= self.quiet_seconds:
                    screen.busy = False
                    screen.waiting_since = now

        self.stats = {
            "at": now,
            "sessions": len(targets),
            "captured": captured,
            "ms": round((time.perf_counter() - started) * 1000, 1),
            "skipped": self.stats["skipped"],
        }
        return set(self.waiting()) != before


def register(api):
    watcher = Watcher()

    def on_scan(sessions):
        if watcher.tick(sessions):
            api.emit({"waiting": watcher.waiting()})

    api.on("scan", on_scan)
    # Off means nothing is watched, so coming back on starts from a fresh baseline.
    api.on_disable(watcher.reset)

    @api.router.get("/state")
    def state():
        return {"waiting": watcher.waiting(), "stats": watcher.stats, "quiet_seconds": watcher.quiet_seconds}
