"""Dialogue operations: start, list, and stop."""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path

from agentop import ops as agent_ops
from agentop.dialogue import scenarios
from agentop.dialogue.model import DIALOGUES_DIR, DialogueMeta
from agentop.dialogue.scenarios.reader import load as load_scenario
from agentop.tmux import Session


def start_dialogue(
    agent_a: str,
    agent_b: str,
    cwd_a: str,
    cwd_b: str,
    max_turns: int,
    brief_file: str,
    scenario_file: str = "",
) -> dict:
    dialogue_id = uuid.uuid4().hex[:8]
    dialogue_dir = DIALOGUES_DIR / dialogue_id
    dialogue_dir.mkdir(parents=True, exist_ok=True)

    resolved_scenario = scenario_file or scenarios.default_path()
    shutil.copy2(brief_file, dialogue_dir / "brief.md")
    shutil.copy2(resolved_scenario, dialogue_dir / "scenario.toml")

    if not max_turns:
        scenario = load_scenario(resolved_scenario)
        max_turns = scenario.max_turns or 20

    dialogue_params = {"model": "claude-opus-4-8"}

    result_a = agent_ops.start(agent_a, cwd_a, short_name=f"dia{dialogue_id[:4]}a", params=dialogue_params)
    if not result_a.get("ok"):
        return {"ok": False, "error": f"Failed to start agent A ({agent_a}): {result_a.get('error')}"}

    result_b = agent_ops.start(agent_b, cwd_b, short_name=f"dia{dialogue_id[:4]}b", params=dialogue_params)
    if not result_b.get("ok"):
        return {"ok": False, "error": f"Failed to start agent B ({agent_b}): {result_b.get('error')}"}

    time.sleep(8)  # Give tmux sessions a moment to start up (codex needs time to boot MCP servers)

    meta = DialogueMeta.create(
        dialogue_id=dialogue_id,
        session_a=result_a["name"],
        session_b=result_b["name"],
        agent_a=agent_a,
        agent_b=agent_b,
        max_turns=max_turns,
    )

    runner = Path(__file__).parent / "runner.py"
    with open(meta.log_path().parent / "runner.log", "w") as log_f:
        proc = subprocess.Popen(
            [sys.executable, str(runner), meta.id],
            stdout=log_f,
            stderr=log_f,
            close_fds=True,
        )

    meta.update({"pid": proc.pid})

    return {
        "ok": True,
        "id": meta.id,
        "session_a": meta.session_a,
        "session_b": meta.session_b,
        "orchestrator_pid": proc.pid,
    }


def list_dialogues() -> list[dict]:
    if not DIALOGUES_DIR.exists():
        return []
    result = []
    for meta_file in sorted(DIALOGUES_DIR.glob("*/meta.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        meta = DialogueMeta.load(meta_file.parent.name)
        if meta:
            result.append(
                {
                    "id": meta.id,
                    "status": meta.status,
                    "agent_a": meta.agent_a,
                    "agent_b": meta.agent_b,
                    "session_a": meta.session_a,
                    "session_b": meta.session_b,
                    "session_a_alive": Session.has(meta.session_a),
                    "session_b_alive": Session.has(meta.session_b),
                }
            )
    return result


def stop_dialogue(dialogue_id: str, close: bool = False) -> dict:
    meta = DialogueMeta.load(dialogue_id)
    if not meta:
        return {"ok": False, "error": f"Dialogue {dialogue_id!r} not found"}
    if meta.pid:
        try:
            os.kill(meta.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    meta.update({"status": "stopped", "pid": None})
    if close:
        for session in (meta.session_a, meta.session_b):
            if Session.has(session):
                Session.send_keys(session, "/exit", "Enter")
                time.sleep(3)
                Session.kill(session)
    return {"ok": True}
