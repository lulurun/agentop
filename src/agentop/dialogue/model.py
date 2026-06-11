"""Dialogue metadata model and folder-based persistence."""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

DIALOGUES_DIR = Path("~/.agent-dashboard/dialogues").expanduser()


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
    created_at: float = field(default_factory=time.time)
    max_turns: int = 20
    error: str | None = None
    pid: int | None = None


def dialogue_dir(dialogue_id: str) -> Path:
    return DIALOGUES_DIR / dialogue_id


def meta_path(dialogue_id: str) -> Path:
    return dialogue_dir(dialogue_id) / "meta.json"


def log_path(dialogue_id: str) -> Path:
    return dialogue_dir(dialogue_id) / "dialogue.log"


def save(d: Dialogue) -> None:
    dialogue_dir(d.id).mkdir(parents=True, exist_ok=True)
    meta_path(d.id).write_text(json.dumps(asdict(d), indent=2))


def load(dialogue_id: str) -> Dialogue | None:
    p = meta_path(dialogue_id)
    if not p.exists():
        return None
    try:
        return Dialogue(**json.loads(p.read_text()))
    except (json.JSONDecodeError, TypeError, KeyError):
        return None


def list_all() -> list[Dialogue]:
    DIALOGUES_DIR.mkdir(parents=True, exist_ok=True)
    results = []
    for entry in sorted(DIALOGUES_DIR.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
        if entry.is_dir():
            d = load(entry.name)
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
