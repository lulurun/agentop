"""Factory for creating the right Actor subclass for each agent type."""

from __future__ import annotations

from agentop.dialogue.actor import Actor
from agentop.dialogue.capturer import Capturer
from agentop.tmux import Session

_AGY_IDLE_SECONDS = 15.0


class _AntigravityCapturer(Capturer):
    idle_seconds: float = _AGY_IDLE_SECONDS


class _AntigravityActor(Actor):
    """agy treats bare newlines as Enter, so multi-line prompts need bracketed paste."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._capturer = _AntigravityCapturer()

    def send(self, text: str) -> None:
        Session.paste_text_bracketed(self.session, text)
        Session.send_keys(self.session, "Enter")


_AGENT_ACTOR_MAP: dict[str, type[Actor]] = {
    "antigravity": _AntigravityActor,
}


class ActorFactory:
    @staticmethod
    def create(agent_name: str, id: str, session: str, name: str) -> Actor:
        actor_class = _AGENT_ACTOR_MAP.get(agent_name, Actor)
        return actor_class(id=id, session=session, name=name)
