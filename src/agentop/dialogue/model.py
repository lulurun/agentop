"""Dialogue data model and JSON persistence."""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

DIALOGUES_DIR = Path("~/.agent-dashboard/dialogues").expanduser()


@dataclass
class Turn:
    speaker: str    # "a" or "b"
    session: str    # tmux session name
    agent: str      # "claude", "gemini", etc.
    content: str
    timestamp: float = field(default_factory=time.time)


@dataclass
class Dialogue:
    id: str
    topic: str
    agent_a: str
    agent_b: str
    session_a: str
    session_b: str
    cwd_a: str
    cwd_b: str
    status: str     # "starting" | "running" | "stopped" | "completed" | "error"
    turns: list[Turn] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    max_turns: int = 20
    error: str | None = None
    pid: int | None = None  # orchestrator process PID


def _ensure_dir() -> Path:
    DIALOGUES_DIR.mkdir(parents=True, exist_ok=True)
    return DIALOGUES_DIR


def path_for(dialogue_id: str) -> Path:
    _ensure_dir()
    return DIALOGUES_DIR / f"{dialogue_id}.json"


def save(d: Dialogue) -> None:
    path_for(d.id).write_text(json.dumps(asdict(d), indent=2))


def load(dialogue_id: str) -> Dialogue | None:
    p = path_for(dialogue_id)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text())
        turns = [Turn(**t) for t in data.pop("turns", [])]
        return Dialogue(turns=turns, **data)
    except (json.JSONDecodeError, TypeError, KeyError):
        return None


def list_all() -> list[Dialogue]:
    _ensure_dir()
    results = []
    for p in sorted(DIALOGUES_DIR.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
        d = load(p.stem)
        if d:
            results.append(d)
    return results


def create(
    topic: str,
    agent_a: str,
    agent_b: str,
    session_a: str,
    session_b: str,
    cwd_a: str,
    cwd_b: str,
    max_turns: int = 20,
    dialogue_id: str | None = None,
) -> Dialogue:
    d = Dialogue(
        id=dialogue_id or uuid.uuid4().hex[:8],
        topic=topic,
        agent_a=agent_a,
        agent_b=agent_b,
        session_a=session_a,
        session_b=session_b,
        cwd_a=cwd_a,
        cwd_b=cwd_b,
        status="starting",
        max_turns=max_turns,
    )
    save(d)
    return d
