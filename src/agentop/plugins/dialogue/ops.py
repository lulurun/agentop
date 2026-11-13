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
from agentop.agentclis import get_agent
from agentop.tmux import Session

from . import scenarios
from .model import DialogueMeta, dialogues_dir
from .scenarios.reader import load as load_scenario


def start_dialogue(
    agent_a: str,
    agent_b: str,
    max_turns: int,
    brief_file: str,
    scenario_file: str = "",
) -> dict:
    dialogue_id = uuid.uuid4().hex[:8]
    resolved_scenario = scenario_file or scenarios.default_path()
    scenario = load_scenario(resolved_scenario)
    scenario_name = scenario.name
    brief_name = Path(brief_file).stem
    dialogue_dir = dialogues_dir() / f"{scenario_name}_{brief_name}_{dialogue_id}"
    dialogue_dir.mkdir(parents=True, exist_ok=True)
    cwd = str(dialogue_dir)

    shutil.copy2(brief_file, dialogue_dir / "brief.md")
    shutil.copy2(resolved_scenario, dialogue_dir / "scenario.toml")

    if not max_turns:
        max_turns = scenario.max_turns or 20

    dialogue_params = {}  # {"model": "claude-opus-4-8"}

    result_a = agent_ops.start(agent_a, cwd, short_name=f"dia{dialogue_id[:4]}a", params=dialogue_params)
    if not result_a.get("ok"):
        return {"ok": False, "error": f"Failed to start agent A ({agent_a}): {result_a.get('error')}"}

    result_b = agent_ops.start(agent_b, cwd, short_name=f"dia{dialogue_id[:4]}b", params=dialogue_params)
    if not result_b.get("ok"):
        return {"ok": False, "error": f"Failed to start agent B ({agent_b}): {result_b.get('error')}"}

    # Give the tools time to start up, or the turn-1 prompt lands before the
    # input is ready and the brief is silently dropped.
    time.sleep(max(get_agent(tool).boot_seconds for tool in (agent_a, agent_b)))

    meta = DialogueMeta.create(
        dialogue_id=dialogue_id,
        session_a=result_a["name"],
        session_b=result_b["name"],
        agent_a=agent_a,
        agent_b=agent_b,
        max_turns=max_turns,
        scenario_name=scenario_name,
        brief_name=brief_name,
    )

    with open(meta.log_path().parent / "runner.log", "w") as log_f:
        proc = subprocess.Popen(
            [sys.executable, "-m", f"{__package__}.runner", meta.id],
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
        "log_path": str(meta.log_path()),
        "cwd": cwd,
    }


def list_dialogues() -> list[dict]:
    if not dialogues_dir().exists():
        return []
    result = []
    for meta_file in sorted(dialogues_dir().glob("*/meta.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        meta = DialogueMeta.load(meta_file.parent.name)
        if meta:
            result.append(
                {
                    "id": meta_file.parent.name,
                    "status": meta.status,
                    "agent_a": meta.agent_a,
                    "agent_b": meta.agent_b,
                    "session_a": meta.session_a,
                    "session_b": meta.session_b,
                    "session_a_alive": Session.has(meta.session_a),
                    "session_b_alive": Session.has(meta.session_b),
                    "start_time": meta.start_time,
                    "max_turns": meta.max_turns,
                    "error": meta.error,
                }
            )
    return result


def _close_open_sessions(meta: DialogueMeta) -> None:
    """Stop the orchestrator and kill any still-open tmux sessions."""
    if meta.pid:
        try:
            os.kill(meta.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    for session in (meta.session_a, meta.session_b):
        if Session.has(session):
            Session.send_keys(session, "/exit", "Enter")
            time.sleep(3)
            Session.kill(session)


_LOG_TAIL_CHARS = 20_000


def get_dialogue(dialogue_id: str) -> dict | None:
    meta = DialogueMeta.load(dialogue_id)
    if not meta:
        return None
    dialogue_dir = meta._dir()

    def read_text(name: str, tail_chars: int | None = None) -> str:
        path = dialogue_dir / name
        if not path.exists():
            return ""
        text = path.read_text(errors="replace")
        if tail_chars and len(text) > tail_chars:
            text = text[-tail_chars:]
        return text

    return {
        "id": dialogue_dir.name,
        "status": meta.status,
        "agent_a": meta.agent_a,
        "agent_b": meta.agent_b,
        "session_a": meta.session_a,
        "session_b": meta.session_b,
        "session_a_alive": Session.has(meta.session_a),
        "session_b_alive": Session.has(meta.session_b),
        "start_time": meta.start_time,
        "max_turns": meta.max_turns,
        "error": meta.error,
        "cwd": str(dialogue_dir),
        "brief": read_text("brief.md"),
        "progress": read_text("progress.md"),
        "log_tail": read_text("dialogue.log", tail_chars=_LOG_TAIL_CHARS),
    }


def list_dialogues_by_status(status: str) -> list[DialogueMeta]:
    if not dialogues_dir().exists():
        return []
    result = []
    for meta_file in dialogues_dir().glob("*/meta.json"):
        meta = DialogueMeta.load(meta_file.parent.name)
        if meta and meta.status == status:
            result.append(meta)
    return result


def remove_dialogue(dialogue_id: str) -> dict:
    """Remove a specific dialogue by ID regardless of status.

    If the dialogue has open tmux sessions, stops and closes them first.
    """
    meta = DialogueMeta.load(dialogue_id)
    if not meta:
        return {"ok": False, "error": f"Dialogue {dialogue_id!r} not found"}
    _close_open_sessions(meta)
    shutil.rmtree(meta._dir())
    return {"ok": True}


def remove_dialogues_by_status(status: str) -> dict:
    """Remove all dialogues with the given status."""
    candidates = list_dialogues_by_status(status)
    removed = []
    for meta in candidates:
        _close_open_sessions(meta)
        shutil.rmtree(meta._dir())
        removed.append(meta.id)
    return {"ok": True, "removed": removed}


RUNNING = ("starting", "running")


def stop_running() -> list[str]:
    """Stop every dialogue that is still running, as its Stop button would. Returns their IDs."""
    stopped = []
    for d in list_dialogues():
        if d["status"] in RUNNING:
            stop_dialogue(d["id"])
            stopped.append(d["id"])
    return stopped


def stop_dialogue(dialogue_id: str) -> dict:
    meta = DialogueMeta.load(dialogue_id)
    if not meta:
        return {"ok": False, "error": f"Dialogue {dialogue_id!r} not found"}
    if meta.pid:
        try:
            os.kill(meta.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    meta.update({"status": "stopped", "pid": None})
    # The orchestrator process was just killed, so it cannot run its own
    # session cleanup — close both tmux sessions here instead. Everything
    # said so far is already in dialogue.log, so the sessions are safe to drop.
    _close_open_sessions(meta)
    return {"ok": True}
