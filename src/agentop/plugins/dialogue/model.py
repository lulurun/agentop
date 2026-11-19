"""Dialogue model: persisted metadata and runtime dialogue object."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from agentop import paths, projects, registry
from agentop.agent_instance import AgentInstance
from agentop.agentclis import get_agent

from .scenarios.reader import load as load_scenario
from .status import DialogueStatus

LOG = logging.getLogger(__name__)

TURNS_FILE = "turns.jsonl"
# A dialogue in one of these has ended: its relay is over and its sessions are closed or closing.
FINISHED = frozenset(
    {
        DialogueStatus.STOPPED,
        DialogueStatus.COMPLETED,
        DialogueStatus.ERROR,
        DialogueStatus.AGENT_REPEATEDLY_MISSING_DELIMITER,
    }
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


OWNER = "dialogue"


def dialogues_dir() -> Path:
    """Where dialogues are: each is a project of the plugin's, ~/.agentop/projects/<scenario>_<brief>_<id>/."""
    return paths.projects_dir()


def _legacy_dir() -> Path:
    return paths.plugins_dir() / "dialogue" / "dialogues"


def _migrate() -> None:
    """Move dialogues from the plugin's own folder, from before projects, into projects of their own."""
    legacy = _legacy_dir()
    if not legacy.is_dir():
        return
    for old in sorted(legacy.iterdir()):
        name = old.name
        if not (old / "meta.json").is_file() or not projects.is_valid_name(name) or projects.folder(name).exists():
            LOG.warning("Dialogue %s left in %s: it can't be a project's folder", name, legacy)
            continue
        projects.folder(name).parent.mkdir(parents=True, exist_ok=True)
        old.rename(projects.folder(name))
        projects.adopt(name, OWNER)
        try:
            data = json.loads((projects.folder(name) / "meta.json").read_text())
        except (OSError, ValueError):
            data = {}
        for session in (data.get("session_a"), data.get("session_b")):
            if session:
                registry.move_session(session, name)
    if not any(legacy.iterdir()):
        legacy.rmdir()


def dialogue_names() -> list[str]:
    """Every dialogue's folder name, newest first."""
    _migrate()
    found = [p for p in projects.list_projects(OWNER) if (Path(p["folder"]) / "meta.json").is_file()]
    return [p["name"] for p in sorted(found, key=lambda p: (Path(p["folder"]) / "meta.json").stat().st_mtime, reverse=True)]


def is_dialogue(name: str) -> bool:
    found = projects.get(name)
    return bool(found and found.get("owner") == OWNER and (projects.folder(name) / "meta.json").is_file())


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
    scenario_name: str = ""
    brief_name: str = ""
    end_time: str | None = None

    def _dir(self) -> Path:
        if self.scenario_name and self.brief_name:
            return dialogues_dir() / f"{self.scenario_name}_{self.brief_name}_{self.id}"
        return dialogues_dir() / self.id

    def _meta_path(self) -> Path:
        return self._dir() / "meta.json"

    def log_path(self) -> Path:
        return self._dir() / "dialogue.log"

    def progress_path(self) -> Path:
        return self._dir() / "progress.md"

    def turns_path(self) -> Path:
        return self._dir() / TURNS_FILE

    def record_turn(self, turn: int, side: str, name: str, status: str, body: str) -> None:
        """Append one message an agent sent to turns.jsonl, the dialogue's transcript."""
        line = {"turn": turn, "side": side, "name": name, "status": status, "body": body, "at": _now()}
        with open(self.turns_path(), "a") as f:
            f.write(json.dumps(line) + "\n")

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
                    "scenario_name": self.scenario_name,
                    "brief_name": self.brief_name,
                    "end_time": self.end_time,
                },
                indent=2,
            )
        )

    def update(self, fields: dict) -> None:
        for k, v in fields.items():
            setattr(self, k, v)
        if self.status in FINISHED and not self.end_time:
            self.end_time = _now()
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
        scenario_name: str = "",
        brief_name: str = "",
    ) -> DialogueMeta:
        meta = cls(
            id=dialogue_id,
            session_a=session_a,
            session_b=session_b,
            agent_a=agent_a,
            agent_b=agent_b,
            max_turns=max_turns,
            start_time=_now(),
            status=DialogueStatus.STARTING,
            scenario_name=scenario_name,
            brief_name=brief_name,
        )
        meta.save()
        return meta

    @classmethod
    def load(cls, dialogue_id: str) -> DialogueMeta | None:
        if is_dialogue(dialogue_id):
            name = dialogue_id
        else:
            # The end of a name is enough on the command line, when only one dialogue has it.
            matches = [n for n in dialogue_names() if n.endswith(dialogue_id)]
            if len(matches) != 1:
                return None
            name = matches[0]
        p = dialogues_dir() / name / "meta.json"
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
                scenario_name=data.get("scenario_name", ""),
                brief_name=data.get("brief_name", ""),
                end_time=data.get("end_time"),
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
