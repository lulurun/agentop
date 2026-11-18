"""What ops and the agent tools return, and the errors they raise in place of {"ok": False, …} dicts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypedDict


class OpsError(Exception):
    """An operation that couldn't be done. The message is for the user. `matches` lists the sessions a name was ambiguous between."""

    def __init__(self, message: str, matches: list[str] | None = None):
        super().__init__(message)
        self.matches = matches or []


class StartFailed(OpsError):
    """A start that failed. `name` is the tmux session kept so its screen shows the error, or None if none was made."""

    def __init__(self, message: str, name: str | None = None):
        super().__init__(message)
        self.name = name


@dataclass(frozen=True)
class StartResult:
    name: str  # the tmux session, which is also the session's name
    pid: int
    agent_started: float | None = None  # with pid, tells this process from a later one given the same pid
    agent_session_id: str | None = None  # the tool's own session ID, when known at launch


@dataclass(frozen=True)
class StopResult:
    sent_exit: bool
    exited: bool
    forced: bool  # the agent was still running when its tmux session was killed
    tmux_killed: bool


@dataclass(frozen=True)
class Sent:
    name: str
    tmux: str


class SavedSession(TypedDict, total=False):
    session_id: str
    title: str | None
    cwd: str
    tool: str
    last_active: float
    resume_cmd: str  # added by ops.get_saved_sessions


def saved_session(tool: str, session_id: str, title: str | None, cwd: str | None, last_active: float) -> SavedSession:
    return {"session_id": session_id, "title": title, "cwd": cwd or "", "tool": tool, "last_active": last_active}

