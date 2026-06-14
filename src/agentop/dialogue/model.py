"""Dialogue model: persisted metadata and runtime dialogue object."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from agentop.agent_instance import AgentInstance
from agentop.agentclis import get_agent
from agentop.dialogue.scenarios.reader import load as load_scenario
from agentop.dialogue.status import DialogueStatus

LOG = logging.getLogger(__name__)

DIALOGUES_DIR = Path("~/.agent-dashboard/dialogues").expanduser()


@dataclass
class DialogueMeta:
    id: str
    session_a: str
    session_b: str
    agent_a: str
    agent_b: str
    max_turns: int
    start_time: str
    status: str
    pid: int | None = None
    error: str | None = None

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
            status=DialogueStatus.STARTING,
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
    def __init__(
        self,
        meta: DialogueMeta,
        topic: str,
        instance_a: AgentInstance,
        instance_b: AgentInstance,
        role_a: str,
        role_b: str,
        opening: str | None = None,
    ):
        self.meta = meta
        self.topic = topic
        self.instance_a = instance_a
        self.instance_b = instance_b
        self.role_a = role_a
        self.role_b = role_b
        self.opening = opening

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
        folder = meta._dir()
        topic = (folder / "brief.md").read_text().strip()
        scenario = load_scenario(folder / "scenario.toml")

        agent_a = get_agent(meta.agent_a)
        agent_b = get_agent(meta.agent_b)
        instance_a = AgentInstance(agent_a, meta.session_a, scenario.name_a)
        instance_b = AgentInstance(agent_b, meta.session_b, scenario.name_b)

        return cls(
            meta=meta,
            topic=topic,
            instance_a=instance_a,
            instance_b=instance_b,
            role_a=scenario.role_a,
            role_b=scenario.role_b,
            opening=scenario.opening,
        )
