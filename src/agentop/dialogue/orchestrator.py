"""Background thread that relays messages between two agent sessions."""
from __future__ import annotations

import os
import tempfile
import threading
import time

from agentop.dialogue import capture, model
from agentop.tmux import send_to_session


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

_RELAY_TO_B = "Please read and respond to the message in: {path}"

_RELAY_TO_A = "The other agent responded. Please read their reply in: {path}"


def _write_relay_file(content: str) -> str:
    """Write content to a temp file and return its path."""
    fd, path = tempfile.mkstemp(prefix="agentop_relay_", suffix=".txt")
    with os.fdopen(fd, "w") as f:
        f.write(content)
    return path


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
        # Kick off session A with the PM prompt
        send_to_session(d.session_a, _PROMPT_A.format(topic=d.topic))
        time.sleep(1.0)
        baseline: dict[str, str] = {"a": capture.capture_pane(d.session_a)}

        # Session B receives no initial prompt — it's driven entirely by A's messages
        current = "a"

        for _ in range(d.max_turns):
            if self._stop.is_set():
                break

            session = d.session_a if current == "a" else d.session_b
            agent = d.agent_a if current == "a" else d.agent_b

            content = capture.wait_for_idle(session, self._stop)
            if content is None:
                break  # stopped or timed out

            response = capture.extract_new_content(baseline[current], content)
            if not response.strip():
                break  # nothing new — both agents are done

            # Persist the turn
            d = model.load(self.dialogue_id) or d
            d.turns.append(model.Turn(speaker=current, session=session, agent=agent, content=response))
            model.save(d)

            # Relay to the other agent
            other = "b" if current == "a" else "a"
            other_session = d.session_b if other == "b" else d.session_a

            path = _write_relay_file(response)
            if other == "b":
                msg = _RELAY_TO_B.format(path=path)
            else:
                msg = _RELAY_TO_A.format(path=path)
            send_to_session(other_session, msg)
            time.sleep(1.0)
            baseline[other] = capture.capture_pane(other_session)

            current = other

        d = model.load(self.dialogue_id) or d
        d.status = "stopped" if self._stop.is_set() else "completed"
        model.save(d)
