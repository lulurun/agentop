"""Dialogue orchestrator: relays messages between two actors until done."""

from __future__ import annotations

import logging
import threading

from agentop.dialogue.actor import DIALOGUE_COMPLETE
from agentop.dialogue.model import Dialogue

LOG = logging.getLogger(__name__)

_DELIMITER_RULE_A = """\
DELIMITER RULE (mandatory): Every message you send must be wrapped with \
BEGIN/END delimiters (replace N with the current turn number, starting at 1):

* Normal turn — to continue the dialogue:
    --- BEGIN output from {name_a} turn:N ---
    your message here
    --- END output from {name_a} turn:N ---

* Final turn — when the goal is fully met and you are done:
    --- BEGIN complete from {name_a} turn:N ---
    your final message here
    --- END complete from {name_a} turn:N ---

{name_b} only receives content between the delimiters. \
Never write anything outside them.\
"""

_DELIMITER_RULE_B = """\
DELIMITER RULE (mandatory): Every message you send must be wrapped with \
BEGIN/END delimiters (replace N with the current turn number, starting at 1):

    --- BEGIN output from {name_b} turn:N ---
    your message here
    --- END output from {name_b} turn:N ---

{name_a} only receives content between the delimiters. \
Never write anything outside them.\
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
        d = self.dialogue
        na, nb = d.actor_a.name, d.actor_b.name
        d.actor_a.attach(self._stop)
        d.actor_b.attach(self._stop)

        fmt = dict(name_a=na, name_b=nb, topic=d.topic, progress_file=d.progress_path())

        d.actor_a.send(
            d.role_a.format(**fmt) + "\n\n" + _DELIMITER_RULE_A.format(**fmt)
        )

        actor, other = d.actor_a, d.actor_b
        b_initialized = False

        for _ in range(d.max_turns):
            if self._stop.is_set():
                break
            msg = actor.receive()
            if msg is None:
                break
            if msg is DIALOGUE_COMPLETE:
                LOG.info("dialogue %s complete signal from %s", d.id, actor.name)
                break
            if other is d.actor_b and not b_initialized:
                msg = d.role_b.format(**fmt) + "\n\n" + _DELIMITER_RULE_B.format(**fmt) + "\n\n" + msg
                b_initialized = True
            other.send(msg)
            actor, other = other, actor
