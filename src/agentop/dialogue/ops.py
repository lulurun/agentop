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
from agentop.dialogue.capturer import Capturer
from agentop.dialogue.model import Dialogue, DIALOGUES_DIR
from agentop.tmux import Session


def start_dialogue(
    topic: str,
    agent_a: str,
    agent_b: str,
    cwd_a: str,
    cwd_b: str,
    max_turns: int = 20,
    brief_file: str | None = None,
    scenario_file: str = "",
) -> dict:
    dialogue_id = uuid.uuid4().hex[:8]

    # Create dialogue folder and copy brief file before starting agents
    dialogue_dir = DIALOGUES_DIR / dialogue_id
    dialogue_dir.mkdir(parents=True, exist_ok=True)
    if brief_file:
        import shutil
        shutil.copy2(brief_file, dialogue_dir / "brief.md")

    result_a = agent_ops.start(agent_a, cwd_a, short_name=f"dia{dialogue_id[:4]}a")
    if not result_a.get("ok"):
        return {"ok": False, "error": f"Failed to start agent A ({agent_a}): {result_a.get('error')}"}

    result_b = agent_ops.start(agent_b, cwd_b, short_name=f"dia{dialogue_id[:4]}b")
    if not result_b.get("ok"):
        return {"ok": False, "error": f"Failed to start agent B ({agent_b}): {result_b.get('error')}"}

    time.sleep(8)  # Give tmux sessions a moment to start up (codex needs time to boot MCP servers)

    from agentop.dialogue.orchestrator import DialogueOrchestrator
    actor_a = Actor(id="a", session=result_a["name"], name=DialogueOrchestrator.NAME_A, capturer=Capturer(), tool=agent_a)
    actor_b = Actor(id="b", session=result_b["name"], name=DialogueOrchestrator.NAME_B, capturer=Capturer(), tool=agent_b)

    d = Dialogue.create(
        topic=topic,
        actor_a=actor_a,
        actor_b=actor_b,
        max_turns=max_turns,
        scenario_file=scenario_file,
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


def _session_alive(name: str) -> bool:
    return Session.has(name)


def list_dialogues() -> list[dict]:
    if not DIALOGUES_DIR.exists():
        return []
    result = []
    for meta in sorted(DIALOGUES_DIR.glob("*/meta.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        d = Dialogue.load(meta.parent.name)
        if d:
            result.append({
                "id": d.id,
                "status": d.status,
                "topic": d.topic,
                "session_a": d.actor_a.session,
                "session_b": d.actor_b.session,
                "session_a_alive": _session_alive(d.actor_a.session),
                "session_b_alive": _session_alive(d.actor_b.session),
            })
    return result


def stop_dialogue(dialogue_id: str, close: bool = False) -> dict:
    d = Dialogue.load(dialogue_id)
    if not d:
        return {"ok": False, "error": f"Dialogue {dialogue_id!r} not found"}
    if d.pid:
        try:
            os.kill(d.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    d.update({"status": "stopped", "pid": None})
    if close:
        for session in (d.actor_a.session, d.actor_b.session):
            if Session.has(session):
                Session.send_keys(session, "/exit", "Enter")
                time.sleep(3)
                Session.kill(session)
    return {"ok": True}
