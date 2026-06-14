from __future__ import annotations

from agentop.actor import Actor
from agentop.actors.antigravity import AntigravityActor
from agentop.actors.codex import CodexActor

_ACTOR_MAP: dict[str, type[Actor]] = {
    "antigravity": AntigravityActor,
    "codex": CodexActor,
}


def get_actor(name: str) -> type[Actor]:
    return _ACTOR_MAP.get(name, Actor)
