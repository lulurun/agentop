"""Dialogue orchestrator: relays messages between two actors until done."""

from __future__ import annotations

import logging
import threading

from agentop.dialogue.model import Dialogue

LOG = logging.getLogger(__name__)

_PROMPT_A = """\
Topic: {topic}

You are Agent A — the product manager and decision-maker in a two-agent dialogue \
with Agent B, who will do implementation or research work for you.

Your responsibilities:

1. **Define the goal.** Derive one clear, specific, achievable goal from the topic.

2. **Create a shared progress file** at {progress_file}.
   Write it in Markdown. Include: goal, requirements, open questions, decisions, \
and current status. Keep it updated as the work evolves — Agent B can read it \
for context at any time.

3. **Delegate clearly.** Tell Agent B exactly what to do. Be specific.

4. **Push back.** Critically review everything Agent B produces. \
Do NOT accept output just because it was provided. \
If it is incomplete, wrong, or does not fully meet the requirements — say so \
and ask for a revision. Hold Agent B to a high standard.

5. **Decide when done.** When you are genuinely satisfied — the goal is met, \
the output is correct and complete — update the progress file with a final summary \
and tell Agent B the dialogue is complete.

Start now: define the goal, write the initial {progress_file}, \
then send your first message to Agent B.
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

        d = self.dialogue
        d.actor_a.send(_PROMPT_A.format(topic=d.topic, progress_file=d.progress_path()))

        actor, other = d.actor_a, d.actor_b

        for _ in range(d.max_turns):
            if self._stop.is_set():
                break
            msg = actor.receive()
            if msg is None:
                break
            other.send(msg)
            actor, other = other, actor
