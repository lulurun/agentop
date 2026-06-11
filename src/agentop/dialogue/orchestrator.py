"""Dialogue orchestrator: relays messages between two actors until done."""

from __future__ import annotations

import logging
import threading

from agentop.dialogue.model import Dialogue

LOG = logging.getLogger(__name__)

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
    def __init__(self, dialogue: Dialogue):
        super().__init__(daemon=True, name=f"dialogue-{dialogue.id}")
        self.dialogue = dialogue
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        self.dialogue.update({"status": "running"})
        try:
            self._loop()
        except Exception as exc:
            LOG.error("Loop for dialogue %s error: %s", self.dialogue.id, exc)
            self.dialogue.update({"status": "error", "error": str(exc)})

        status = "stopped" if self._stop.is_set() else "completed"
        self.dialogue.update({"status": status, "pid": None})

    def _loop(self) -> None:
        self.dialogue.actor_a.attach(self._stop)
        self.dialogue.actor_b.attach(self._stop)

        self.dialogue.actor_a.send(_PROMPT_A.format(topic=self.dialogue.topic))

        actor, other = self.dialogue.actor_a, self.dialogue.actor_b

        for _ in range(self.dialogue.max_turns):
            if self._stop.is_set():
                break
            msg = actor.receive()
            if msg is None:
                break
            other.send(msg)
            actor, other = other, actor
