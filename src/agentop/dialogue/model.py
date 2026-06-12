"""Dialogue model: persisted metadata and runtime dialogue object."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

LOG = logging.getLogger(__name__)

DIALOGUES_DIR = Path("~/.agent-dashboard/dialogues").expanduser()


class DialogueMeta:
    """Persisted metadata for a dialogue — written to meta.json in the dialogue folder."""

    def __init__(
        self,
        id: str,
        session_a: str,
        session_b: str,
        agent_a: str,
        agent_b: str,
        max_turns: int,
        start_time: str,
        status: str,
        pid: int | None = None,
        error: str | None = None,
    ):
        self.id = id
        self.session_a = session_a
        self.session_b = session_b
        self.agent_a = agent_a
        self.agent_b = agent_b
        self.max_turns = max_turns
        self.start_time = start_time
        self.status = status
        self.pid = pid
        self.error = error

    def _dir(self) -> Path:
        return DIALOGUES_DIR / self.id

    def _meta_path(self) -> Path:
        return self._dir() / "meta.json"

    def log_path(self) -> Path:
        return self._dir() / "dialogue.log"

    def progress_path(self) -> Path:
        return self._dir() / "progress.md"

    def save(self) -> None:
        self._dir().mkdir(parents=True, exist_ok=True)
        self._meta_path().write_text(
            json.dumps(
                {
                    "id": self.id,
                    "session_a": self.session_a,
                    "session_b": self.session_b,
                    "agent_a": self.agent_a,
                    "agent_b": self.agent_b,
                    "max_turns": self.max_turns,
                    "start_time": self.start_time,
                    "status": self.status,
                    "pid": self.pid,
                    "error": self.error,
                },
                indent=2,
            )
        )

    def update(self, fields: dict) -> None:
        for k, v in fields.items():
            setattr(self, k, v)
        LOG.info("%s %s", self.id, self.status)
        self.save()

    @classmethod
    def create(
        cls,
        dialogue_id: str,
        session_a: str,
        session_b: str,
        agent_a: str,
        agent_b: str,
        max_turns: int,
    ) -> DialogueMeta:
        meta = cls(
            id=dialogue_id,
            session_a=session_a,
            session_b=session_b,
            agent_a=agent_a,
            agent_b=agent_b,
            max_turns=max_turns,
            start_time=datetime.now(timezone.utc).isoformat(),
            status="starting",
        )
        meta.save()
        return meta

    @classmethod
    def load(cls, dialogue_id: str) -> DialogueMeta | None:
        p = (DIALOGUES_DIR / dialogue_id) / "meta.json"
        if not p.exists():
            return None
        try:
            data = json.loads(p.read_text())
            return cls(
                id=data["id"],
                session_a=data["session_a"],
                session_b=data["session_b"],
                agent_a=data["agent_a"],
                agent_b=data["agent_b"],
                max_turns=data["max_turns"],
                start_time=data["start_time"],
                status=data["status"],
                pid=data.get("pid"),
                error=data.get("error"),
            )
        except (json.JSONDecodeError, KeyError, TypeError):
            return None


class Dialogue:
    """Runtime dialogue object — wraps DialogueMeta with loaded topic, actors, and role prompts."""

    def __init__(self, meta: DialogueMeta, topic: str, actor_a, actor_b, role_a: str, role_b: str):
        self.meta = meta
        self.topic = topic
        self.actor_a = actor_a
        self.actor_b = actor_b
        self.role_a = role_a
        self.role_b = role_b

    @property
    def id(self) -> str:
        return self.meta.id

    @property
    def status(self) -> str:
        return self.meta.status

    @property
    def max_turns(self) -> int:
        return self.meta.max_turns

    @property
    def pid(self) -> int | None:
        return self.meta.pid

    def update(self, fields: dict) -> None:
        self.meta.update(fields)

    def log_path(self) -> Path:
        return self.meta.log_path()

    def progress_path(self) -> Path:
        return self.meta.progress_path()

    @classmethod
    def from_meta(cls, meta: DialogueMeta) -> Dialogue:
        from agentop.dialogue import scenarios
        from agentop.dialogue.actor import Actor
        from agentop.dialogue.capturer import Capturer

        folder = meta._dir()
        topic = (folder / "brief.md").read_text().strip()
        scenario = scenarios.load(folder / "scenario.toml")

        actor_a = Actor(id="a", session=meta.session_a, name=scenario.name_a, capturer=Capturer(), tool=meta.agent_a)
        actor_b = Actor(id="b", session=meta.session_b, name=scenario.name_b, capturer=Capturer(), tool=meta.agent_b)

        return cls(
            meta=meta,
            topic=topic,
            actor_a=actor_a,
            actor_b=actor_b,
            role_a=scenario.role_a,
            role_b=scenario.role_b,
        )
