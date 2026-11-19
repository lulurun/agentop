"""Dialogue operations: start, list, and stop."""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import tomllib
import uuid
from datetime import datetime, timezone
from pathlib import Path

from agentop import ops as agent_ops
from agentop.agentclis import get_agent
from agentop.results import OpsError
from agentop.tmux import Session

from . import scenarios
from agentop import projects

from .model import FINISHED, OWNER, DialogueMeta, dialogue_names
from .scenarios.reader import load as load_scenario
from .status import DialogueStatus

# The agents' windows keep this size whoever watches them. The orchestrator finds each
# reply's BEGIN/END lines on the screen, so a window narrowed by a viewer could break them.
WINDOW_SIZE = (80, 40)


def _slug(text: str, limit: int = 40) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-").lower()
    return slug[:limit].rstrip("-") or "brief"


def create_dialogue(
    agent_a: str,
    agent_b: str,
    max_turns: int,
    brief_text: str,
    brief_name: str,
    scenario_file: str | Path = "",
) -> DialogueMeta:
    """Make the dialogue's folder, with its brief, scenario and meta.json (status `starting`). Nothing runs yet."""
    dialogue_id = uuid.uuid4().hex[:8]
    resolved_scenario = scenario_file or scenarios.default_path()
    scenario = load_scenario(resolved_scenario)
    brief_name = _slug(brief_name)
    try:
        projects.create(f"{scenario.name}_{brief_name}_{dialogue_id}", OWNER)
    except projects.ProjectError as exc:
        raise OpsError(str(exc)) from exc
    meta = DialogueMeta.create(
        dialogue_id=dialogue_id,
        session_a="",
        session_b="",
        agent_a=agent_a,
        agent_b=agent_b,
        max_turns=max_turns or scenario.max_turns or 20,
        scenario_name=scenario.name,
        brief_name=brief_name,
    )
    folder = meta._dir()
    (folder / "brief.md").write_text(brief_text)
    shutil.copy2(resolved_scenario, folder / "scenario.toml")
    return meta


def _wanted(meta: DialogueMeta, fields: dict) -> DialogueMeta | None:
    """Save fields onto the dialogue as it is on disk now, unless it was stopped or removed meanwhile (None)."""
    current = DialogueMeta.load(meta._dir().name)
    if current is None or current.status != DialogueStatus.STARTING:
        return None
    current.update(fields)
    return current


def launch_dialogue(meta: DialogueMeta) -> dict:
    """Start both agents and the orchestrator for a dialogue made by create_dialogue.

    Takes as long as the tools take to boot. On failure the dialogue is left
    with status `error` and whatever was started is closed. A Stop or Remove
    meanwhile wins: what was started is closed and nothing more is.
    """
    cwd = str(meta._dir())
    short = f"dia{meta.id[:4]}"
    dialogue_params = {}  # {"model": "claude-opus-4-8"}
    started: list[str] = []

    def give_up(message: str, status: str | None = DialogueStatus.ERROR) -> dict:
        for name in started:
            _close_session(name)
        if status:
            _wanted(meta, {"status": status, "error": message, "pid": None})
        return {"ok": False, "error": message, "id": meta.id}

    for side, tool in (("a", meta.agent_a), ("b", meta.agent_b)):
        try:
            name = agent_ops.start(
                tool,
                cwd,
                short_name=f"{short}{side}",
                params=dialogue_params,
                project=meta._dir().name,
                readonly=True,
                counted=False,
            ).name
            error = None
        except OpsError as exc:
            name, error = getattr(exc, "name", None), str(exc)
        if name:
            started.append(name)
            Session.fix_size(name, *WINDOW_SIZE)
            if _wanted(meta, {f"session_{side}": name}) is None:
                return give_up("The dialogue was stopped before it started", None)
        if error:
            return give_up(f"Failed to start agent {side.upper()} ({tool}): {error}")

    # Give the tools time to start up, or the turn-1 prompt lands before the
    # input is ready and the brief is silently dropped.
    time.sleep(max(get_agent(tool).boot_seconds for tool in (meta.agent_a, meta.agent_b)))

    current = DialogueMeta.load(meta._dir().name)
    if current is None or current.status != DialogueStatus.STARTING:
        return give_up("The dialogue was stopped before it started", None)

    with open(current._dir() / "runner.log", "w") as log_f:
        proc = subprocess.Popen(
            [sys.executable, "-m", f"{__package__}.runner", current._dir().name],
            stdout=log_f,
            stderr=log_f,
            close_fds=True,
        )
    current.update({"pid": proc.pid})
    return {
        "ok": True,
        "id": current.id,
        "session_a": current.session_a,
        "session_b": current.session_b,
        "orchestrator_pid": proc.pid,
        "log_path": str(current.log_path()),
        "cwd": cwd,
    }


def start_dialogue(
    agent_a: str,
    agent_b: str,
    max_turns: int,
    brief_file: str,
    scenario_file: str = "",
) -> dict:
    """Create and launch a dialogue from a brief file, waiting until it runs (`agentop dialogue start`)."""
    meta = create_dialogue(
        agent_a,
        agent_b,
        max_turns,
        Path(brief_file).read_text(),
        Path(brief_file).stem,
        scenario_file,
    )
    return launch_dialogue(meta)


# ---------------------------------------------------------------- the New dialogue wizard


def list_scenarios() -> list[dict]:
    """The built-in scenarios the wizard offers, each with its brief's template and fields."""
    rows = []
    for scenario_id, path in scenarios.catalog().items():
        try:
            scenario = load_scenario(path)
        except (ValueError, OSError, tomllib.TOMLDecodeError):
            continue  # a scenario that can't be read can't be run either
        description = scenario.description or scenarios.DEFAULT_DESCRIPTION
        rows.append(
            {
                "id": scenario_id,
                "name": scenario.name,
                "summary": scenario.summary,
                "shelf": scenario_id.startswith("shelf/"),
                "name_a": scenario.name_a,
                "name_b": scenario.name_b,
                "max_turns": scenario.max_turns or 20,
                "description": description,
                "fields": scenarios.placeholders(description),
            }
        )
    return rows


class DialogueInputError(ValueError):
    pass


def new_dialogue(scenario_id: str, values: dict[str, str], agent_a: str, agent_b: str, max_turns: int = 0) -> DialogueMeta:
    """Check the wizard's answers and make the dialogue's folder and brief. The caller launches it."""
    path = scenarios.catalog().get(scenario_id)
    if path is None:
        raise DialogueInputError(f"No such scenario: {scenario_id}")
    for tool in (agent_a, agent_b):
        if get_agent(tool) is None:
            raise DialogueInputError(f"Unknown tool: {tool}")
    if max_turns < 0 or max_turns > 500:
        raise DialogueInputError("Max turns must be between 1 and 500")
    scenario = load_scenario(path)
    description = scenario.description or scenarios.DEFAULT_DESCRIPTION
    fields = scenarios.placeholders(description)
    missing = [f for f in fields if not str(values.get(f) or "").strip()]
    if missing:
        raise DialogueInputError(f"Fill in every field: {', '.join(missing)}")
    answers = {f: str(values[f]).strip() for f in fields}
    brief = scenarios.fill(description, answers, {"name_a": scenario.name_a, "name_b": scenario.name_b})
    brief_name = answers.get("title") or (answers[fields[0]] if fields else scenario.name)
    return create_dialogue(agent_a, agent_b, max_turns, brief.strip() + "\n", brief_name, path)


def _roles(folder: Path) -> tuple[str, str]:
    try:
        scenario = load_scenario(folder / "scenario.toml")
        return scenario.name_a, scenario.name_b
    except (ValueError, OSError, tomllib.TOMLDecodeError):
        return "A", "B"


def _title(folder: Path) -> str:
    """The brief's first heading, or its first line."""
    try:
        with open(folder / "brief.md", errors="replace") as f:
            for line in f:
                text = line.strip().lstrip("#").strip()
                if text:
                    return text[:120]
    except OSError:
        pass
    return folder.name


def _summary(meta: DialogueMeta) -> dict:
    folder = meta._dir()
    name_a, name_b = _roles(folder)
    return {
        "id": folder.name,
        "title": _title(folder),
        "scenario": meta.scenario_name,
        "status": meta.status,
        "running": meta.status not in FINISHED,
        "agent_a": meta.agent_a,
        "agent_b": meta.agent_b,
        "name_a": name_a,
        "name_b": name_b,
        "session_a": meta.session_a or None,
        "session_b": meta.session_b or None,
        "session_a_alive": bool(meta.session_a) and Session.has(meta.session_a),
        "session_b_alive": bool(meta.session_b) and Session.has(meta.session_b),
        "start_time": meta.start_time,
        "end_time": meta.end_time,
        "max_turns": meta.max_turns,
        "error": meta.error,
    }


def list_dialogues() -> list[dict]:
    result = []
    for name in dialogue_names():
        meta = DialogueMeta.load(name)
        if meta:
            _settle_interrupted(meta)
            result.append(_summary(meta))
    return result


_LAUNCH_SECONDS = 600


def _settle_interrupted(meta: DialogueMeta) -> None:
    """A launch the service was restarted during leaves `starting` with no orchestrator: end it."""
    if meta.status != DialogueStatus.STARTING or meta.pid:
        return
    try:
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(meta.start_time)).total_seconds()
    except ValueError:
        return
    if age > _LAUNCH_SECONDS:
        meta.update({"status": DialogueStatus.ERROR, "error": "Interrupted while starting"})
        _close_open_sessions(meta)


def _close_session(name: str) -> None:
    if name and Session.has(name):
        Session.send_keys(name, "/exit", "Enter")
        time.sleep(3)
        Session.kill(name)


def _close_open_sessions(meta: DialogueMeta) -> None:
    """Stop the orchestrator and kill any still-open tmux sessions."""
    if meta.pid:
        try:
            os.kill(meta.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    for session in (meta.session_a, meta.session_b):
        _close_session(session)


_LOG_TAIL_CHARS = 20_000
# The orchestrator's log line before each message body, from parser.py (dialogues from before turns.jsonl).
_LOG_BODY = re.compile(r"^\[(\S+)\] \S+ \[(.+?)\] turn:(\d+) body:$")
_LOG_STATUS = re.compile(r"\[([^\[\]]+)\] turn:(\d+) status:(\w+) body_len:")
_LOG_LINE = re.compile(r"^\[\d{4}-\d\d-\d\dT[\d:]+\] ")


def _turns_from_log(folder: Path, name_a: str) -> list[dict]:
    try:
        lines = (folder / "dialogue.log").read_text(errors="replace").splitlines()
    except OSError:
        return []
    turns: list[dict] = []
    statuses: dict[tuple[str, int], str] = {}
    current: dict | None = None
    for line in lines:
        if current is not None:
            if _LOG_LINE.match(line):
                current["body"] = "\n".join(current["body"]).strip()
                turns.append(current)
                current = None
            else:
                current["body"].append(line)
                continue
        if m := _LOG_STATUS.search(line):
            statuses[(m.group(1), int(m.group(2)))] = m.group(3)
        if m := _LOG_BODY.match(line):
            name, turn = m.group(2), int(m.group(3))
            current = {
                "turn": turn,
                "side": "a" if name == name_a else "b",
                "name": name,
                "status": statuses.get((name, turn), "continue"),
                "body": [],
                "at": m.group(1),
            }
    if current is not None:
        current["body"] = "\n".join(current["body"]).strip()
        turns.append(current)
    return turns


def get_turns(dialogue_id: str) -> list[dict] | None:
    """Every message the agents sent, in order: turns.jsonl, or for an older dialogue its log."""
    meta = DialogueMeta.load(dialogue_id)
    if not meta:
        return None
    folder = meta._dir()
    try:
        lines = meta.turns_path().read_text(errors="replace").splitlines()
    except FileNotFoundError:
        return _turns_from_log(folder, _roles(folder)[0])
    turns = []
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and isinstance(row.get("body"), str):
            turns.append(row)
    return turns


def _stats(meta: DialogueMeta, turns: list[dict]) -> dict:
    def words(side: str) -> int:
        return sum(len(t["body"].split()) for t in turns if t.get("side") == side)

    duration = None
    try:
        start = datetime.fromisoformat(meta.start_time)
        if meta.end_time:
            end = datetime.fromisoformat(meta.end_time)
        elif meta.status in FINISHED and turns and turns[-1].get("at"):
            # Ended before end_time was kept: its last message is near enough. The log's times are local.
            end = datetime.fromisoformat(turns[-1]["at"]).astimezone()
        elif meta.status in FINISHED:  # and nothing was said: meta.json was last written as it ended
            end = datetime.fromtimestamp(meta._meta_path().stat().st_mtime, timezone.utc)
        else:
            end = None
        if end is not None:
            duration = max(0.0, (end - start).total_seconds())
    except (ValueError, OSError):
        pass
    retries = 0
    try:
        with open(meta.log_path(), errors="replace") as f:
            retries = sum("delimiter not found" in line for line in f)
    except OSError:
        pass
    return {
        "turns": len(turns),
        "turns_a": sum(t.get("side") == "a" for t in turns),
        "turns_b": sum(t.get("side") == "b" for t in turns),
        "words_a": words("a"),
        "words_b": words("b"),
        "retries": retries,
        "duration_seconds": duration,
        "completed_by": next((t["name"] for t in reversed(turns) if t.get("status") == "complete"), None),
    }


# Files the dialogue made that the app lists and shows. Everything else in the folder is the plugin's own.
_ARTIFACT_DIR = "deliverables"
_SHOWN_FILES = ("brief.md", "progress.md")
MAX_FILES = 500
MAX_FILE_BYTES = 1024 * 1024


def list_files(dialogue_id: str) -> list[dict] | None:
    meta = DialogueMeta.load(dialogue_id)
    if not meta:
        return None
    folder = meta._dir()
    rows = []
    candidates = [folder / name for name in _SHOWN_FILES]
    artifacts = folder / _ARTIFACT_DIR
    if artifacts.is_dir() and not artifacts.is_symlink():
        candidates += sorted(p for p in artifacts.rglob("*") if ".git" not in p.relative_to(artifacts).parts)
    for path in candidates[: MAX_FILES + len(_SHOWN_FILES)]:
        if path.is_symlink() or not path.is_file():
            continue
        stat = path.stat()
        rows.append({"path": str(path.relative_to(folder)), "size": stat.st_size, "modified": stat.st_mtime})
    return rows


def read_file(dialogue_id: str, relative: str) -> dict | None:
    """One listed file's text. Anything not in list_files is None, so nothing else in the folder or beyond is reachable."""
    listed = {row["path"]: row for row in list_files(dialogue_id) or []}
    row = listed.get(relative)
    if row is None:
        return None
    meta = DialogueMeta.load(dialogue_id)
    with open(meta._dir() / relative, "rb") as f:
        data = f.read(MAX_FILE_BYTES + 1)
    binary = b"\0" in data[:8192]
    return {
        "path": relative,
        "size": row["size"],
        "truncated": len(data) > MAX_FILE_BYTES,
        "binary": binary,
        "text": "" if binary else data[:MAX_FILE_BYTES].decode(errors="replace"),
    }


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

    turns = get_turns(dialogue_id) or []
    return {
        **_summary(meta),
        "cwd": str(dialogue_dir),
        "brief": read_text("brief.md"),
        "progress": read_text("progress.md"),
        "log_tail": read_text("dialogue.log", tail_chars=_LOG_TAIL_CHARS),
        "stats": _stats(meta, turns),
    }


def list_dialogues_by_status(status: str) -> list[DialogueMeta]:
    result = []
    for name in dialogue_names():
        meta = DialogueMeta.load(name)
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
