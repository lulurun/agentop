"""Dialogue operations: start and stop."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path

from agentop import ops as agent_ops
from agentop.dialogue.actor import Actor
from agentop.dialogue.capturer import get_capturer
from agentop.dialogue.model import Dialogue


def start_dialogue(
    topic: str,
    agent_a: str,
    agent_b: str,
    cwd_a: str,
    cwd_b: str,
    max_turns: int = 20,
) -> dict:
    dialogue_id = uuid.uuid4().hex[:8]

    result_a = agent_ops.start(agent_a, cwd_a, short_name=f"dia{dialogue_id[:4]}a")
    if not result_a.get("ok"):
        return {"ok": False, "error": f"Failed to start agent A ({agent_a}): {result_a.get('error')}"}

    result_b = agent_ops.start(agent_b, cwd_b, short_name=f"dia{dialogue_id[:4]}b")
    if not result_b.get("ok"):
        return {"ok": False, "error": f"Failed to start agent B ({agent_b}): {result_b.get('error')}"}

    time.sleep(3)  # Give tmux sessions a moment to start up

    from agentop.dialogue.orchestrator import DialogueOrchestrator
    actor_a = Actor(id="a", session=result_a["name"], name=DialogueOrchestrator.NAME_A, capturer=get_capturer(agent_a))
    actor_b = Actor(id="b", session=result_b["name"], name=DialogueOrchestrator.NAME_B, capturer=get_capturer(agent_b))

    d = Dialogue.create(
        topic=topic,
        actor_a=actor_a,
        actor_b=actor_b,
        max_turns=max_turns,
        dialogue_id=dialogue_id,
    )

    runner = Path(__file__).parent / "runner.py"
    with open(d.log_path().parent / "runner.log", "w") as log_f:
        proc = subprocess.Popen(
            [sys.executable, str(runner), d.id],
            stdout=log_f,
            stderr=log_f,
            close_fds=True,
        )

    d.update({"pid": proc.pid})

    return {
        "ok": True,
        "id": d.id,
        "session_a": actor_a.session,
        "session_b": actor_b.session,
        "orchestrator_pid": proc.pid,
    }


def stop_dialogue(dialogue_id: str) -> dict:
    d = Dialogue.load(dialogue_id)
    if not d:
        return {"ok": False, "error": f"Dialogue {dialogue_id!r} not found"}
    if d.pid:
        try:
            os.kill(d.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    d.update({"status": "stopped", "pid": None})
    return {"ok": True}
