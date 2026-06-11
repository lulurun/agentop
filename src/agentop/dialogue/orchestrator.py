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
        d = self.dialogue
        d.update({"status": "running"})
        LOG.info("dialogue %s started: %s", d.id, d.topic)
        try:
            self._loop(d)
        except Exception as exc:
            LOG.error("dialogue %s error: %s", d.id, exc)
            d.update({"status": "error", "error": str(exc)})

    def _loop(self, d: Dialogue) -> None:
        d.actor_a.attach(self._stop)
        d.actor_b.attach(self._stop)

        d.actor_a.send(_PROMPT_A.format(topic=d.topic))

        actor, other = d.actor_a, d.actor_b

        for _ in range(d.max_turns):
            if self._stop.is_set():
                break
            msg = actor.receive()
            if msg is None:
                break
            other.send(msg)
            actor, other = other, actor

        status = "stopped" if self._stop.is_set() else "completed"
        LOG.info("dialogue %s %s", d.id, status)
        d.update({"status": status, "pid": None})
