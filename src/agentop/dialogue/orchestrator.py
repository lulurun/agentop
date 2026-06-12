"""Dialogue orchestrator: relays messages between two actors until done."""

from __future__ import annotations

import logging
import threading

from agentop.dialogue.actor import DIALOGUE_COMPLETE
from agentop.dialogue.model import Dialogue

LOG = logging.getLogger(__name__)

_DELIMITER_RULE_A = """\
REPORTING RULE (mandatory): Do all your thinking and planning freely. \
When ready to send to {name_b}, write a concise message wrapped in \
BEGIN/END delimiters at the very end (replace N with the current turn \
number, starting at 1):

* Normal turn — to continue the dialogue:
    --- BEGIN output from {name_a} turn:N ---
    your concise message to {name_b} here
    --- END output from {name_a} turn:N ---

* Final turn — when the goal is fully met and you are done:
    --- BEGIN complete from {name_a} turn:N ---
    your final summary here
    --- END complete from {name_a} turn:N ---

{name_b} only receives content between the delimiters — \
everything outside them is your working space.\
"""

_DELIMITER_RULE_B = """\
REPORTING RULE (mandatory): Do all your thinking, research, and implementation \
work freely. When your work is complete, write a concise report or summary \
wrapped in BEGIN/END delimiters at the very end (replace N with the current \
turn number, starting at 1):

    --- BEGIN output from {name_b} turn:N ---
    your concise report or summary here
    --- END output from {name_b} turn:N ---

{name_a} only receives the content between the delimiters — \
everything outside them is your working space.\
"""


def _prompt_a(d: Dialogue) -> str:
    fmt = dict(name_a=d.actor_a.name, name_b=d.actor_b.name, topic=d.topic, progress_file=d.progress_path())
    return d.role_a.format(**fmt) + "\n\n" + _DELIMITER_RULE_A.format(**fmt)


def _prompt_b(d: Dialogue) -> str:
    fmt = dict(name_a=d.actor_a.name, name_b=d.actor_b.name, topic=d.topic, progress_file=d.progress_path())
    return d.role_b.format(**fmt) + "\n\n" + _DELIMITER_RULE_B.format(**fmt)


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
        d.actor_a.attach(self._stop)
        d.actor_b.attach(self._stop)

        d.actor_a.send(_prompt_a(d))

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
                msg = _prompt_b(d) + "\n\n" + msg
                b_initialized = True
            other.send(msg)
            actor, other = other, actor
