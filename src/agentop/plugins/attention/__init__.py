"""Mark sessions waiting for input from lifecycle events or screen fallback."""

from __future__ import annotations

import hashlib
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from agentop import signals, tmux
from agentop.status_feed import SessionStatus

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
    state: str | None = None
    source: str = "screen"
    event: str = ""
    state_since: float = 0.0
    settle: int = 0
    fallback: signals.ScreenFallback = field(default_factory=signals.ScreenFallback)


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
        self.readers: dict[str, tuple[str, signals.EventReader]] = {}
        self.stats = {"at": None, "sessions": 0, "captured": 0, "ms": 0.0, "skipped": 0}
        self._lock = threading.Lock()

    def waiting(self) -> dict[str, float]:
        """{session ID: when it started waiting}."""
        return {sid: s.waiting_since for sid, s in self.screens.items() if s.waiting_since is not None}

    def reset(self) -> None:
        with self._lock:
            self.screens = {}
            self.readers = {}

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
        hooked = {row["id"] for row in sessions if row.get("managed")}
        targets = {
            row["id"]: row["tmux"]["session"]
            for row in sessions
            if row.get("status") == "running" and (row.get("tmux") or {}).get("session")
        }
        for sid in self.screens.keys() - targets.keys():
            del self.screens[sid]
        for sid in self.readers.keys() - targets.keys():
            del self.readers[sid]

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
                status = None
                if sid in hooked:
                    reader = self.readers.get(sid)
                    if reader is None or reader[0] != name:
                        reader = (name, signals.EventReader(name))
                        self.readers[sid] = reader
                    status = reader[1].read()
                if status is not None:
                    source = "hook"
                    if status.state == "working":
                        inferred = screen.fallback.apply(status, self._capture(pane_id), now)
                        source = "screen" if inferred is not status else "hook"
                        status = inferred
                        captured += 1
                    screen.state = status.state
                    screen.source = source
                    screen.event = status.event
                    screen.state_since = status.at
                    screen.busy = status.state == "working"
                    screen.waiting_since = status.at if status.state in ("done", "input") else None
                else:
                    state = "done" if screen.waiting_since is not None else "working" if screen.busy else "unknown"
                    if screen.state != state or screen.source != "screen":
                        screen.state_since = now
                    screen.state = state
                    screen.source = "screen"
                    screen.event = ""

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
    feed = api.status_feed
    last_waiting = set()
    previous = set()

    def on_scan(sessions):
        nonlocal last_waiting, previous
        watcher.tick(sessions)
        for sid, screen in tuple(watcher.screens.items()):
            feed.publish(SessionStatus(sid, screen.state or "unknown", screen.state_since, screen.source, screen.event))
        current = set(watcher.screens)
        for sid in previous - current:
            feed.publish(SessionStatus(sid, "unknown", time.time(), "unavailable"))
        previous = current
        waiting = watcher.waiting()
        if set(waiting) != last_waiting:
            last_waiting = set(waiting)
            api.emit({"waiting": waiting})

    from .bridge import serve

    async def bridge():
        try:
            await serve(feed, api.status_socket_path)
        finally:
            feed.clear()

    api.task(bridge)
    api.on("scan", on_scan)
    def reset():
        nonlocal last_waiting, previous
        last_waiting = set()
        previous = set()
        watcher.reset()
        feed.clear()

    api.on_disable(reset)

    @api.router.get("/state")
    def state():
        return {"waiting": watcher.waiting(), "stats": watcher.stats, "quiet_seconds": watcher.quiet_seconds}
