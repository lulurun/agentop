"""High-level dialogue operations: start, stop, status."""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import uuid
from pathlib import Path

from agentop import ops as agent_ops
from agentop.dialogue import model


def start_dialogue(
    topic: str,
    agent_a: str,
    agent_b: str,
    cwd_a: str,
    cwd_b: str,
    max_turns: int = 20,
) -> dict:
    """Start two agent sessions and launch the dialogue orchestrator."""
    dialogue_id = uuid.uuid4().hex[:8]

    result_a = agent_ops.start(agent_a, cwd_a, short_name=f"dia{dialogue_id[:4]}a")
    if not result_a.get("ok"):
        return {"ok": False, "error": f"Failed to start agent A ({agent_a}): {result_a.get('error')}"}

    result_b = agent_ops.start(agent_b, cwd_b, short_name=f"dia{dialogue_id[:4]}b")
    if not result_b.get("ok"):
        return {"ok": False, "error": f"Failed to start agent B ({agent_b}): {result_b.get('error')}"}

    d = model.create(
        topic=topic,
        agent_a=agent_a,
        agent_b=agent_b,
        session_a=result_a["name"],
        session_b=result_b["name"],
        cwd_a=cwd_a,
        cwd_b=cwd_b,
        max_turns=max_turns,
        dialogue_id=dialogue_id,
    )

    runner = Path(__file__).parent / "runner.py"
    log_path = model.DIALOGUES_DIR / f"{d.id}.log"
    model._ensure_dir()

    with open(log_path, "w") as log_f:
        proc = subprocess.Popen(
            [sys.executable, str(runner), d.id],
            stdout=log_f,
            stderr=log_f,
            close_fds=True,
        )

    d.pid = proc.pid
    model.save(d)

    return {
        "ok": True,
        "id": d.id,
        "session_a": result_a["name"],
        "session_b": result_b["name"],
        "orchestrator_pid": proc.pid,
    }


def stop_dialogue(dialogue_id: str) -> dict:
    """Stop a running dialogue orchestrator (leaves agent sessions alive)."""
    d = model.load(dialogue_id)
    if not d:
        return {"ok": False, "error": f"Dialogue {dialogue_id!r} not found"}
    if d.pid:
        try:
            os.kill(d.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    d.status = "stopped"
    d.pid = None
    model.save(d)
    return {"ok": True}


def get_status(dialogue_id: str) -> dict | None:
    d = model.load(dialogue_id)
    if not d:
        return None
    return {
        "id": d.id,
        "topic": d.topic,
        "status": d.status,
        "agent_a": d.agent_a,
        "agent_b": d.agent_b,
        "session_a": d.session_a,
        "session_b": d.session_b,
        "turns": len(d.turns),
        "max_turns": d.max_turns,
        "error": d.error,
        "created_at": d.created_at,
        "pid": d.pid,
    }
