"""Lifecycle events for managed Claude Code and Codex sessions."""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from agentop.paths import state_dir

EVENTS = ("SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "PermissionRequest", "Stop")


def event_path(session: str) -> Path:
    key = hashlib.sha256(session.encode()).hexdigest()
    return state_dir() / "signals" / f"{key}.jsonl"


def launch_flags(tool: str, session: str) -> str:
    if tool not in ("claude", "codex"):
        return ""
    path = event_path(session)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".signals-")
    os.close(fd)
    os.replace(temporary, path)
    command = f"{shlex.quote(sys.executable)} {shlex.quote(str(Path(__file__).with_name('signal_hook.py')))} {shlex.quote(str(path))} {tool}"
    events = EVENTS + (("Notification", "StopFailure", "PostToolUseFailure") if tool == "claude" else ("Interrupt",))
    if tool == "claude":
        settings = {"hooks": {event: [{"hooks": [{"type": "command", "command": command}]}] for event in events}}
        return " --settings " + shlex.quote(json.dumps(settings))
    return "".join(" -c " + shlex.quote(f'hooks.{event}=[{{hooks=[{{type="command",command={json.dumps(command)}}}]}}]') for event in events)


@dataclass
class Status:
    state: str = "idle"
    at: float = 0.0
    event: str = ""
    tool: str = ""
    active: bool = False

    def apply(self, item: dict) -> None:
        event = item.get("event", "")
        kind = item.get("kind", "")
        tool = item.get("tool", "")
        self.active = True
        state = self.state
        if event == "SessionStart":
            state = "idle"
        elif event in ("UserPromptSubmit", "PreToolUse", "PostToolUse", "PostToolUseFailure"):
            state = "input" if event == "PreToolUse" and kind in ("request_user_input", "AskUserQuestion") else "working"
        elif event == "PermissionRequest" and tool == "claude":
            state = "input"
        elif event == "Notification" and kind in ("permission_prompt", "elicitation_dialog"):
            state = "input"
        elif event in ("Stop", "Interrupt", "StopFailure"):
            state = "done"
        if state != self.state or event in ("UserPromptSubmit", "PreToolUse", "PostToolUse", "PostToolUseFailure", "PermissionRequest", "Stop", "Interrupt", "StopFailure"):
            self.state, self.at = state, item.get("at", time.time())
        self.event, self.tool = event, tool


class EventReader:
    """Replay existing events once, then read only complete appended records."""

    def __init__(self, session: str):
        self.path = event_path(session)
        self.identity: tuple[int, int] | None = None
        self.offset = 0
        self.status = Status()

    def read(self) -> Status | None:
        try:
            with self.path.open("rb") as stream:
                info = os.fstat(stream.fileno())
                identity = (info.st_dev, info.st_ino)
                if identity != self.identity or info.st_size < self.offset:
                    self.identity = identity
                    self.offset = 0
                    self.status = Status()
                stream.seek(self.offset)
                while line := stream.readline():
                    if not line.endswith(b"\n"):
                        break
                    self.offset = stream.tell()
                    try:
                        item = json.loads(line)
                        if isinstance(item, dict):
                            self.status.apply(item)
                    except (ValueError, TypeError):
                        continue
                return self.status if self.status.active else None
        except OSError:
            self.identity = None
            self.offset = 0
            self.status = Status()
            return None


def read_status(session: str) -> Status | None:
    return EventReader(session).read()


@dataclass
class ScreenFallback:
    saw_busy: bool = False
    missing: int = 0
    ended_at: float = 0.0
    input_at: float = 0.0
    last_event_at: float = 0.0

    def apply(self, status: Status, screen: str, now: float) -> Status:
        if status.at != self.last_event_at:
            self.saw_busy = False
            self.missing = 0
            self.ended_at = 0.0
            self.input_at = 0.0
            self.last_event_at = status.at
        if status.state != "working":
            return status
        text = screen.lower()
        if status.tool == "codex" and status.event == "PermissionRequest" and (
            ("approve" in text and ("yes" in text or "allow" in text))
            or ("would you like to run the following command?" in text and "yes, proceed" in text)
        ):
            self.input_at = self.input_at or now
            return Status("input", self.input_at, status.event, status.tool, True)
        if "esc to interrupt" in text:
            self.saw_busy = True
            self.missing = 0
        elif self.saw_busy:
            self.missing += 1
            if self.missing >= 2:
                self.ended_at = self.ended_at or now
                return Status("done", self.ended_at, "screen", status.tool, True)
        return status
