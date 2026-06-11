"""Dialogue orchestrator: relays messages between two actors until done."""
from __future__ import annotations

import threading

from agentop.dialogue import model
from agentop.dialogue.actor import Actor

_PROMPT_A = """\
Topic: {topic}

You are participating in a structured two-agent dialogue with another AI assistant. \
Act as a product manager:
- Derive a clear, specific goal from the topic
- Define the key requirements
- Propose an implementation approach or plan

The other agent will review your proposal and respond. Keep iterating — reviewing \
their feedback and refining — until you reach a satisfactory outcome.

Please begin: state your goal and requirements for the topic above.
"""


class DialogueOrchestrator(threading.Thread):
    def __init__(self, dialogue_id: str):
        super().__init__(daemon=True, name=f"dialogue-{dialogue_id}")
        self.dialogue_id = dialogue_id
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        d = model.load(self.dialogue_id)
        if not d:
            return
        d.status = "running"
        model.save(d)
        try:
            self._loop(d)
        except Exception as exc:
            d = model.load(self.dialogue_id) or d
            d.status = "error"
            d.error = str(exc)
            model.save(d)

    def _loop(self, d: model.Dialogue) -> None:
        log = model.log_path(d.id)
        actor_a = Actor("a", d.session_a, d.agent_a, self._stop, log)
        actor_b = Actor("b", d.session_b, d.agent_b, self._stop, log)

        actor_a.send(_PROMPT_A.format(topic=d.topic))

        actor, other = actor_a, actor_b

        for _ in range(d.max_turns):
            if self._stop.is_set():
                break
            msg = actor.receive()
            if msg is None:
                break
            other.send(msg)
            actor, other = other, actor

        d = model.load(self.dialogue_id) or d
        d.status = "stopped" if self._stop.is_set() else "completed"
        model.save(d)
