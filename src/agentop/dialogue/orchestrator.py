"""Dialogue orchestrator: relays messages between two actors until done."""

from __future__ import annotations

import logging
import os
import threading

from agentop.actor import Actor
from agentop.dialogue.model import Dialogue
from agentop.dialogue.parser import ResponseParser
from agentop.dialogue.status import DialogueStatus, ReceiveStatus

LOG = logging.getLogger(__name__)

_MAX_RETRIES = 2  # retries after first attempt = up to 3 total receives per turn

_PROTOCOL_RULE = f"""\
OUTPUT FORMAT (mandatory): Do all your thinking freely. When ready to send \
your message, wrap it in BEGIN/END delimiters at the very end:

--- BEGIN {{name}} turn:{{turn}} nonce:{{nonce}} status:{ReceiveStatus.CONTINUE} ---
your message here
--- END {{name}} turn:{{turn}} nonce:{{nonce}} status:{ReceiveStatus.CONTINUE} ---

To signal you are completely done:

--- BEGIN {{name}} turn:{{turn}} nonce:{{nonce}} status:{ReceiveStatus.COMPLETE} ---
your final summary here
--- END {{name}} turn:{{turn}} nonce:{{nonce}} status:{ReceiveStatus.COMPLETE} ---

Rules:
- Replace nothing — use the exact name, turn number, and nonce shown above.
- Everything outside the delimiters is your private workspace and is not forwarded.\
"""

_RECOVERY_PROMPT = f"""\
Your previous response did not contain the required BEGIN/END delimiters. \
Please re-send your message now using exactly this format:

--- BEGIN {{name}} turn:{{turn}} nonce:{{nonce}} status:{ReceiveStatus.CONTINUE} ---
your message here
--- END {{name}} turn:{{turn}} nonce:{{nonce}} status:{ReceiveStatus.CONTINUE} ---\
"""


def _new_nonce() -> str:
    return os.urandom(4).hex()


def _format_rule(name: str, turn: int, nonce: str) -> str:
    return _PROTOCOL_RULE.format(turn=turn, name=name, nonce=nonce)


def _recovery_prompt(name: str, turn: int, nonce: str) -> str:
    return _RECOVERY_PROMPT.format(turn=turn, name=name, nonce=nonce)


def _fmt(d: Dialogue) -> dict:
    return dict(
        name_a=d.instance_a.actor.name,
        name_b=d.instance_b.actor.name,
        brief=d.topic,
        topic=d.topic,  # backward compat for legacy scenario prompts
        progress_file=d.progress_path(),
    )


def _prompt_a(d: Dialogue, turn: int, nonce: str) -> str:
    fmt = _fmt(d)
    rule = _format_rule(d.instance_a.actor.name, turn, nonce)
    parts = [d.role_a.format(**fmt)]
    if d.opening:
        parts.append(d.opening.format(**fmt))
    parts.append(rule)
    return "\n\n".join(parts)


def _prompt_b(d: Dialogue, turn: int, nonce: str) -> str:
    fmt = _fmt(d)
    rule = _format_rule(d.instance_b.actor.name, turn, nonce)
    return d.role_b.format(**fmt) + "\n\n" + rule


class DialogueOrchestrator(threading.Thread):
    def __init__(self, dialogue: Dialogue):
        super().__init__(daemon=True, name=f"dialogue-{dialogue.id}")
        self.dialogue = dialogue
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        self.dialogue.update({"status": DialogueStatus.RUNNING})
        try:
            self._loop()
        except Exception as exc:
            LOG.error("Loop for dialogue %s error: %s", self.dialogue.id, exc)
            self.dialogue.update({"status": DialogueStatus.ERROR, "error": str(exc)})

        dialogue_status = DialogueStatus.STOPPED if self._stop.is_set() else DialogueStatus.COMPLETED
        self.dialogue.update({"status": dialogue_status, "pid": None})

    _parser = ResponseParser()

    def _receive_with_retry(self, actor: Actor, turn: int, nonce: str) -> tuple[str, ReceiveStatus] | None:
        """Receive from actor, retrying up to _MAX_RETRIES times on delimiter not found."""
        for attempt in range(_MAX_RETRIES + 1):
            if self._stop.is_set():
                return None
            raw = actor.receive()
            if raw is None:
                return None  # timeout / stop
            body, receive_status = self._parser.parse(raw, actor.name, turn, nonce)
            if receive_status != ReceiveStatus.DELIMITER_NOT_FOUND_WILL_RETRY:
                return (body, receive_status)
            if attempt < _MAX_RETRIES:
                LOG.warning(
                    "[%s] turn:%d attempt:%d delimiter not found — sending recovery prompt", actor.name, turn, attempt + 1
                )
                actor.send(_recovery_prompt(actor.name, turn, nonce))
        LOG.error("[%s] turn:%d exhausted retries", actor.name, turn)
        return ("", ReceiveStatus.DELIMITER_NOT_FOUND_RETRIES_EXHAUSTED)

    def _loop(self) -> None:
        d = self.dialogue
        d.instance_a.attach(self._stop)
        d.instance_b.attach(self._stop)

        # Turn 1: initialize actor_a with its role + protocol rule
        current_nonce = _new_nonce()
        d.instance_a.actor.send(_prompt_a(d, turn=1, nonce=current_nonce))

        actor, other = d.instance_a.actor, d.instance_b.actor
        b_initialized = False
        turn = 1

        for _ in range(d.max_turns):
            if self._stop.is_set():
                break

            result = self._receive_with_retry(actor, turn, current_nonce)

            if result is None:
                break

            body, receive_status = result

            if receive_status == ReceiveStatus.DELIMITER_NOT_FOUND_RETRIES_EXHAUSTED:
                d.update(
                    {"status": DialogueStatus.AGENT_REPEATEDLY_MISSING_DELIMITER, "error": f"actor {actor.name} turn {turn} exhausted retries"}
                )
                break

            if receive_status == ReceiveStatus.COMPLETE:
                LOG.info("dialogue %s complete signal from %s turn %d", d.id, actor.name, turn)
                break

            turn += 1
            current_nonce = _new_nonce()

            if other is d.instance_b.actor and not b_initialized:
                # Prepend actor_b's role + protocol rule on first contact
                msg = _prompt_b(d, turn=turn, nonce=current_nonce) + "\n\n" + body
                b_initialized = True
            else:
                msg = body + "\n\n" + _format_rule(other.name, turn, current_nonce)

            other.send(msg)
            actor, other = other, actor
