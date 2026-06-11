"""Dialogue orchestrator: relays messages between two actors until done."""

from __future__ import annotations

import logging
import threading

from agentop.dialogue.actor import DIALOGUE_COMPLETE
from agentop.dialogue.model import Dialogue

LOG = logging.getLogger(__name__)

_PROMPT_A = """\
Topic: {topic}

Your name is {name_a}. You are a product manager in a two-agent dialogue \
with {name_b}, who will do implementation or research work for you.

Your responsibilities:

1. **Define the goal.** Derive one clear, specific, achievable goal from the topic.

2. **Create a shared progress file** at {progress_file}.
   Write it in Markdown. Include: goal, requirements, open questions, decisions, \
and current status. Keep it updated as the work evolves — {name_b} can read it \
for context at any time.

3. **Delegate clearly.** Tell {name_b} exactly what to do. Be specific.

4. **Push back.** Critically review everything {name_b} produces. \
Do NOT accept output just because it was provided. \
If it is incomplete, wrong, or does not fully meet the requirements — say so \
and ask for a revision. Hold {name_b} to a high standard.

5. **Decide when done.** When you are genuinely satisfied — the goal is met, \
the output is correct and complete — update the progress file with a final summary \
and tell {name_b} the dialogue is complete.

DELIMITER RULE (mandatory): Every message you send must begin with exactly one \
of these two delimiter lines (replace N with the current turn number, starting at 1):

* Normal turn — to continue the dialogue:
    --- output from {name_a} turn:N ---

* Final turn — when the goal is fully met and you are done:
    --- complete from {name_a} turn:N ---

{name_b} only receives content after the delimiter line. \
Never write anything before it in your reply.

Start now: define the goal, write the initial {progress_file}, \
then send your first message to {name_b}.
"""

_PROMPT_B = """\
Your name is {name_b}. You are an implementer/researcher in a two-agent \
dialogue with {name_a} (product manager).

DELIMITER RULE (mandatory): Every message you send must begin with exactly this \
line (replace N with the current turn number, starting at 1):

--- output from {name_b} turn:N ---

{name_a} only receives content after this line. \
Never write anything before it in your reply.

{name_a}'s first message to you follows.
"""


class DialogueOrchestrator(threading.Thread):
    NAME_A = "Alex"
    NAME_B = "Bob"

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
        na, nb = self.NAME_A, self.NAME_B
        d.actor_a.attach(self._stop)
        d.actor_b.attach(self._stop)

        d.actor_a.send(
            _PROMPT_A.format(
                topic=d.topic,
                progress_file=d.progress_path(),
                name_a=na,
                name_b=nb,
            )
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
            # Prepend Bob's identity + delimiter rule to the very first message he receives
            if other is d.actor_b and not b_initialized:
                msg = _PROMPT_B.format(name_a=na, name_b=nb) + "\n\n" + msg
                b_initialized = True
            other.send(msg)
            actor, other = other, actor
