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

_RELAY = "Please read and respond to the message in: {path}"


def _write_file(content: str) -> str:
    fd, path = tempfile.mkstemp(prefix="agentop_relay_", suffix=".txt")
    with os.fdopen(fd, "w") as f:
        f.write(content)
    return path


class Actor:
    """Represents one participant in a dialogue: owns its tmux session and baseline."""

    def __init__(self, id: str, session: str, agent: str, stop_event: threading.Event):
        self.id = id            # "a" or "b"
        self.session = session  # tmux session name
        self.agent = agent      # "claude", "gemini", etc.
        self._stop = stop_event
        self._baseline = ""

    def send(self, text: str) -> None:
        """Write text to a temp file, instruct the agent to read it, then snapshot baseline."""
        path = _write_file(text)
        send_to_session(self.session, _RELAY.format(path=path))
        time.sleep(1.0)
        self._baseline = capture.capture_pane(self.session)

    def receive(self) -> str | None:
        """Wait until the agent is idle, return new content since last send (or None if stopped)."""
        content = capture.wait_for_idle(self.session, self._stop)
        if content is None:
            return None
        response = capture.extract_new_content(self._baseline, content)
        return response.strip() or None


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
        actor_a = Actor("a", d.session_a, d.agent_a, self._stop)
        actor_b = Actor("b", d.session_b, d.agent_b, self._stop)

        # Session A drives the conversation; B is a blank slate
        actor_a.send(_PROMPT_A.format(topic=d.topic))

        actor, other = actor_a, actor_b

        for _ in range(d.max_turns):
            if self._stop.is_set():
                break

            msg = actor.receive()
            if msg is None:
                break

            d = model.load(self.dialogue_id) or d
            d.turns.append(model.Turn(speaker=actor.id, session=actor.session, agent=actor.agent, content=msg))
            model.save(d)

            other.send(msg)
            actor, other = other, actor

        d = model.load(self.dialogue_id) or d
        d.status = "stopped" if self._stop.is_set() else "completed"
        model.save(d)
