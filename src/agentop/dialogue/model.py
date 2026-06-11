"""Dialogue: metadata, two actors, folder-based persistence."""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

from agentop.dialogue.actor import Actor

DIALOGUES_DIR = Path("~/.agent-dashboard/dialogues").expanduser()


class Dialogue:
    def __init__(
        self,
        id: str,
        topic: str,
        actor_a: Actor,
        actor_b: Actor,
        status: str,
        created_at: float = 0.0,
        max_turns: int = 20,
        error: str | None = None,
        pid: int | None = None,
    ):
        self.id = id
        self.topic = topic
        self.actor_a = actor_a
        self.actor_b = actor_b
        self.status = status
        self.created_at = created_at or time.time()
        self.max_turns = max_turns
        self.error = error
        self.pid = pid

    # ------------------------------------------------------------------
    # Paths (private helpers exposed via log_path only)

    def _dir(self) -> Path:
        return DIALOGUES_DIR / self.id

    def _meta_path(self) -> Path:
        return self._dir() / "meta.json"

    def log_path(self) -> Path:
        return self._dir() / "dialogue.log"

    # ------------------------------------------------------------------
    # Persistence

    def save(self) -> None:
        self._dir().mkdir(parents=True, exist_ok=True)
        self._meta_path().write_text(json.dumps({
            "id": self.id,
            "topic": self.topic,
            "actor_a": self.actor_a.to_dict(),
            "actor_b": self.actor_b.to_dict(),
            "status": self.status,
            "created_at": self.created_at,
            "max_turns": self.max_turns,
            "error": self.error,
            "pid": self.pid,
        }, indent=2))

    def update(self, fields: dict) -> None:
        for k, v in fields.items():
            setattr(self, k, v)
        self.save()

    # ------------------------------------------------------------------
    # Classmethods

    @classmethod
    def create(
        cls,
        topic: str,
        actor_a: Actor,
        actor_b: Actor,
        max_turns: int = 20,
        dialogue_id: str | None = None,
    ) -> Dialogue:
        d = cls(
            id=dialogue_id or uuid.uuid4().hex[:8],
            topic=topic,
            actor_a=actor_a,
            actor_b=actor_b,
            status="starting",
            max_turns=max_turns,
        )
        d.save()
        return d

    @classmethod
    def load(cls, dialogue_id: str) -> Dialogue | None:
        p = (DIALOGUES_DIR / dialogue_id) / "meta.json"
        if not p.exists():
            return None
        try:
            data = json.loads(p.read_text())
            return cls(
                id=data["id"],
                topic=data["topic"],
                actor_a=Actor.from_dict(data["actor_a"]),
                actor_b=Actor.from_dict(data["actor_b"]),
                status=data["status"],
                created_at=data.get("created_at", 0.0),
                max_turns=data.get("max_turns", 20),
                error=data.get("error"),
                pid=data.get("pid"),
            )
        except (json.JSONDecodeError, KeyError, TypeError):
            return None
