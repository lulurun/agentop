"""Dialogue orchestrator: relays messages between two actors until done."""

from __future__ import annotations

import logging
import os
import threading

from agentop.dialogue.actor import STATUS_COMPLETE, Actor
from agentop.dialogue.model import Dialogue

LOG = logging.getLogger(__name__)

_MAX_RETRIES = 2  # retries after first attempt = up to 3 total receives per turn

_PROTOCOL_RULE = f"""\
OUTPUT FORMAT (mandatory): Do all your thinking freely. When ready to send \
your message, output it using this exact XML structure at the very end:

<agentop_message turn="{{turn}}" from="{{name}}" nonce="{{nonce}}">
your message here
<agentop_status>ok</agentop_status>
</agentop_message>

Rules:
- Replace nothing — use the exact turn number, name, and nonce shown above.
- To signal you are completely done, use <agentop_status>{STATUS_COMPLETE}</agentop_status>.
- Everything outside the XML block is your private workspace and is not forwarded.\
"""

_RECOVERY_PROMPT = """\
Your previous response did not contain the required XML block. Please re-send \
your message now using exactly this format:

<agentop_message turn="{turn}" from="{name}" nonce="{nonce}">
your message here
<agentop_status>ok</agentop_status>
</agentop_message>\
"""


def _new_nonce() -> str:
    return os.urandom(4).hex()


def _format_rule(name: str, turn: int, nonce: str) -> str:
    return _PROTOCOL_RULE.format(turn=turn, name=name, nonce=nonce)


def _recovery_prompt(name: str, turn: int, nonce: str) -> str:
    return _RECOVERY_PROMPT.format(turn=turn, name=name, nonce=nonce)


def _fmt(d: Dialogue) -> dict:
    return dict(
        name_a=d.actor_a.name,
        name_b=d.actor_b.name,
        brief=d.topic,
        topic=d.topic,  # backward compat for legacy scenario prompts
        progress_file=d.progress_path(),
    )


def _prompt_a(d: Dialogue, turn: int, nonce: str) -> str:
    fmt = _fmt(d)
    rule = _format_rule(d.actor_a.name, turn, nonce)
    parts = [d.role_a.format(**fmt)]
    if d.opening:
        parts.append(d.opening.format(**fmt))
    parts.append(rule)
    return "\n\n".join(parts)


def _prompt_b(d: Dialogue, turn: int, nonce: str) -> str:
    fmt = _fmt(d)
    rule = _format_rule(d.actor_b.name, turn, nonce)
    return d.role_b.format(**fmt) + "\n\n" + rule


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

    def _receive_with_retry(self, actor: Actor, turn: int, nonce: str) -> tuple[str, str] | None:
        """Receive from actor, retrying up to _MAX_RETRIES times on parse_failure."""
        for attempt in range(_MAX_RETRIES + 1):
            if self._stop.is_set():
                return None
            result = actor.receive(nonce)
            if result is None:
                return None  # timeout / stop
            body, status = result
            if status != "parse_failure":
                return (body, status)
            if attempt < _MAX_RETRIES:
                LOG.warning(
                    "[%s] turn:%d attempt:%d parse_failure — sending recovery prompt", actor.name, turn, attempt + 1
                )
                actor.send(_recovery_prompt(actor.name, turn, nonce))
        LOG.error("[%s] turn:%d exhausted retries", actor.name, turn)
        return ("", "parse_error")

    def _loop(self) -> None:
        d = self.dialogue
        d.actor_a.attach(self._stop)
        d.actor_b.attach(self._stop)

        # Turn 1: initialize actor_a with its role + protocol rule
        current_nonce = _new_nonce()
        d.actor_a.send(_prompt_a(d, turn=1, nonce=current_nonce))

        actor, other = d.actor_a, d.actor_b
        b_initialized = False
        turn = 1

        for _ in range(d.max_turns):
            if self._stop.is_set():
                break

            result = self._receive_with_retry(actor, turn, current_nonce)

            if result is None:
                break

            body, status = result

            if status == "parse_error":
                d.update({"status": "parse_error", "error": f"actor {actor.name} turn {turn} exhausted retries"})
                break

            if status == STATUS_COMPLETE:
                LOG.info("dialogue %s complete signal from %s turn %d", d.id, actor.name, turn)
                break

            turn += 1
            current_nonce = _new_nonce()

            if other is d.actor_b and not b_initialized:
                # Prepend actor_b's role + protocol rule on first contact
                msg = _prompt_b(d, turn=turn, nonce=current_nonce) + "\n\n" + body
                b_initialized = True
            else:
                msg = body + "\n\n" + _format_rule(other.name, turn, current_nonce)

            other.send(msg)
            actor, other = other, actor
