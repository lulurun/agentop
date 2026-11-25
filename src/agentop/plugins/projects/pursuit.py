"""A project's pursuit record: the Coordinator's tasks, its stop reasons, and the event log review reads.

Kept in `<project>/.project/` (tasks.json, events.jsonl) and mirrored into the track (track.py). See docs/0_13_projects.md, section 4.3.
"""

from __future__ import annotations

import fcntl
import json
import re
import time
from contextlib import contextmanager
from pathlib import Path

from agentop import projects
from agentop.jsonfile import write_json_atomic

from . import track
from .track import TrackError as PursuitError

MAX_ROUNDS = 3
STATE_FILE = "STATE.md"
STATE_SECTIONS = ("Objective", "Where things are", "Decisions", "Open questions for the owner", "Next")
REVIEWS_DIR = "reviews"
REFLECTION_PLACEHOLDER = "(The Coordinator writes this.)"
KINDS = ("done", "waiting", "owner", "blocked")
CLEARING_KINDS = ("done", "waiting")

OBJECTIVE_PLACEHOLDER = '(What the project is for, and what "done" means.)'


def _meta(project: str) -> Path:
    return projects.meta_dir(project)


@contextmanager
def _locked(project: str):
    _meta(project).mkdir(parents=True, exist_ok=True)
    with open(_meta(project) / "pursuit.lock", "w") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        yield


def _tasks_path(project: str) -> Path:
    return _meta(project) / "tasks.json"


def _events_path(project: str) -> Path:
    return _meta(project) / "events.jsonl"


def load_tasks(project: str) -> list[dict]:
    try:
        data = json.loads(_tasks_path(project).read_text())
    except (OSError, ValueError):
        return []
    return data.get("tasks", []) if isinstance(data, dict) else []


def _save_tasks(project: str, tasks: list[dict]) -> None:
    write_json_atomic(_tasks_path(project), {"tasks": tasks})


def log(project: str, kind: str, **fields) -> dict:
    event = {"at": time.time(), "kind": kind, **fields}
    _meta(project).mkdir(parents=True, exist_ok=True)
    with open(_events_path(project), "a") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")
    return event


def events(project: str, since: float = 0.0) -> list[dict]:
    try:
        lines = _events_path(project).read_text().splitlines()
    except OSError:
        return []
    found = []
    for line in lines:
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict) and event.get("at", 0) >= since:
            found.append(event)
    return found


WAKE_NOTE = (
    "[agentop] Your context was cleared before this message. Read AGENTS.md and COORDINATOR_SKILL.md, "
    "run agentop coordinator status, then act on it:\n\n"
)


def pending_clear(project: str) -> dict | None:
    """The yield's clear.pending event when its /clear hasn't been sent yet."""
    last = next((e for e in reversed(events(project)) if e["kind"] in ("clear", "clear.pending")), None)
    return last if last is not None and last["kind"] == "clear.pending" else None


def wake_note(project: str) -> str:
    """WAKE_NOTE when the Coordinator was cleared and nothing has reached it since, else ""; logs the wake."""
    last = next((e for e in reversed(events(project)) if e["kind"] in ("clear", "resume", "woken")), None)
    if last is None or last["kind"] != "clear":
        return ""
    log(project, "woken")
    return WAKE_NOTE


def resolve_artifact(project: str, path: str) -> Path:
    """The artifact's absolute path, which must be inside the project folder. Raises PursuitError."""
    root = projects.folder(project).resolve()
    target = (root / Path(path).expanduser()).resolve()
    if target != root and root not in target.parents:
        raise PursuitError(f"The artifact must be inside the project folder {root}: {path}")
    if target == root or projects.META_DIR in target.relative_to(root).parts:
        raise PursuitError(f"The artifact must be a file in the project folder, not {path}")
    return target


def open_tasks(project: str) -> list[dict]:
    return [t for t in load_tasks(project) if t["status"] == "open"]


def _is(task: dict, wanted: str) -> bool:
    return task["session"].lower() == wanted.lower() or task["id"].lower() == wanted.lower()


def task_for(project: str, session: str) -> dict | None:
    """The open task of a session, or with that ID, matched ignoring case."""
    return next((t for t in open_tasks(project) if _is(t, session)), None)


def awaiting_report(task: dict) -> bool:
    return task["status"] == "open" and task["rounds"][-1].get("reported_at") is None


def _current(project: str, task: dict) -> None:
    """A decision on a task goes into the current plan, so a task of an older direction is carried first."""
    if track.exists(project) and task.get("action") and task.get("direction") != track.generation(project):
        raise PursuitError(
            f"{task['id']} belongs to direction {task.get('direction')}, whose files are never written again. "
            f"Carry it first: agentop coordinator task carry {task['id']} --to <M…A…>"
        )


def _update(project: str, session: str, change, event: str, decision: bool = True) -> dict:
    with _locked(project):
        tasks = load_tasks(project)
        task = next((t for t in tasks if t["status"] == "open" and _is(t, session)), None)
        if task is None:
            raise PursuitError(f"{session} has no open task. agentop coordinator task list shows them")
        if decision:
            _current(project, task)
        change(task)
        _save_tasks(project, tasks)
        track.on_task(project, task, event, [t for t in tasks if t["status"] == "open"])
        return task


def open_task(project: str, session: str, tool: str, cwd: str, goal: str, accept: list[str], artifact: str,
              action: str | None = None, model: str | None = None, brief: str = "") -> dict:
    """Record a new task before its session starts; with a track, its ID comes from the counter. Raises PursuitError."""
    goal = goal.strip()
    accept = [a.strip() for a in accept if a.strip()]
    if not goal:
        raise PursuitError("A task needs --goal: the one outcome it delivers")
    if not accept:
        raise PursuitError("A task needs at least one --accept: a criterion you will check its result against")
    resolve_artifact(project, artifact)
    if action is not None and not track.exists(project):
        raise PursuitError("--action needs a track: run agentop coordinator direction new --text - first")
    with _locked(project):
        tasks = load_tasks(project)
        if any(t["status"] == "open" and t["session"].lower() == session.lower() for t in tasks):
            raise PursuitError(f"{session} already has an open task. Accept, reject, or fail it first")
        if action is not None:
            action = track.check_action(project, action)
        if track.exists(project):
            task_id, direction = track.next_task_id(project, action)
        else:
            task_id, direction = f"t{len(tasks) + 1}", None
        task = {
            "id": task_id,
            "action": action,
            "direction": direction,
            "model": model,
            "brief": (brief or "").strip()[:500],
            "session": session,
            "tool": tool,
            "cwd": cwd,
            "goal": goal,
            "accept": accept,
            "artifact": artifact,
            "status": "open",
            "opened_at": time.time(),
            "rounds": [{"sent_at": time.time()}],
        }
        tasks.append(task)
        _save_tasks(project, tasks)
        track.on_task(project, task, "open", [t for t in tasks if t["status"] == "open"])
    log(project, "task.open", task=task["id"], session=session, tool=tool)
    return task


def report(project: str, session: str, summary: str, blocked: bool = False) -> dict:
    """The session's result for the current round. Without `blocked`, its artifact must exist. Raises PursuitError."""
    if not summary.strip():
        raise PursuitError("A report needs a summary: what you did, and the evidence it works")

    def change(task):
        current = task["rounds"][-1]
        if current.get("reported_at") is not None:
            raise PursuitError(f"Round {len(task['rounds'])} of {task['id']} is reported already; wait for the Coordinator")
        present = resolve_artifact(project, task["artifact"]).exists()
        if not present and not blocked:
            raise PursuitError(
                f"The artifact {task['artifact']} does not exist. Write it, or report with --blocked and say what stops you"
            )
        current.update(reported_at=time.time(), summary=summary.strip(), blocked=blocked, artifact_present=present)

    task = _update(project, session, change, "report", decision=False)
    log(project, "task.report", task=task["id"], session=task["session"], round=len(task["rounds"]), blocked=blocked)
    return task


def _reported(task: dict) -> None:
    if task["rounds"][-1].get("reported_at") is None:
        raise PursuitError(
            f"{task['session']} has not reported round {len(task['rounds'])} of {task['id']} yet. "
            "Wait for it, or look at its screen"
        )


def accept(project: str, session: str, checked: str) -> dict:
    """Accept the current round's result. Raises PursuitError."""
    if not checked.strip():
        raise PursuitError("Accepting needs --checked: what you verified yourself, against which criteria")

    def change(task):
        _reported(task)
        if not resolve_artifact(project, task["artifact"]).exists():
            raise PursuitError(f"The artifact {task['artifact']} does not exist, so the result can't be accepted")
        task.update(status="accepted", closed_at=time.time(), checked=checked.strip())

    task = _update(project, session, change, "accept")
    log(project, "task.accept", task=task["id"], session=task["session"], rounds=len(task["rounds"]))
    return task


def reject(project: str, session: str, feedback: str) -> dict:
    """Send the result back for another round. Raises PursuitError, also when the rounds are used up."""
    if not feedback.strip():
        raise PursuitError("Feedback is needed: which criterion failed, and what to change")

    def change(task):
        _reported(task)
        if len(task["rounds"]) >= MAX_ROUNDS:
            raise PursuitError(
                f"{task['id']} has used all {MAX_ROUNDS} rounds. Close it with agentop coordinator task fail {task['session']} --note …, "
                "then replan or take the decision to the owner"
            )
        task["rounds"][-1]["feedback"] = feedback.strip()
        task["rounds"].append({"sent_at": time.time()})

    task = _update(project, session, change, "reject")
    log(project, "task.reject", task=task["id"], session=task["session"], round=len(task["rounds"]) - 1)
    return task


def fail(project: str, session: str, note: str, decision: bool = True) -> dict:
    if not note.strip():
        raise PursuitError("Failing needs --note: what is missing, and what you will do instead")

    def change(task):
        task.update(status="failed", closed_at=time.time(), note=note.strip())

    task = _update(project, session, change, "fail", decision)
    log(project, "task.fail", task=task["id"], session=task["session"], rounds=len(task["rounds"]))
    return task


def carry(project: str, ref: str, to: str) -> dict:
    """Re-home an open task (of an older direction, or with no action) under an action of the current plan."""
    if not track.exists(project):
        raise PursuitError("There is no track/ yet: run agentop coordinator direction new --text - first")
    with _locked(project):
        tasks = load_tasks(project)
        task = next((t for t in tasks if t["status"] == "open" and _is(t, ref)), None)
        if task is None:
            raise PursuitError(f"{ref} has no open task. agentop coordinator task list shows them")
        old = track.carry(project, task, to)
        _save_tasks(project, tasks)
        track.on_task(project, task, "carry", [t for t in tasks if t["status"] == "open"], **{"from": old})
    log(project, "task.carry", task=task["id"], session=task["session"], **{"from": old})
    return task


def new_direction(project: str, text: str, copy_plan: bool = False, keep_open: bool = False) -> int:
    with _locked(project):
        return track.new_direction(project, text, copy_plan, open_tasks(project), keep_open)


def problems(project: str, kind: str | None = None) -> list[str]:
    """What blocks a yield: the track's checks, or for a project still on STATE.md (no track/), the old ones."""
    if not track.exists(project) and (projects.folder(project) / STATE_FILE).is_file():
        return state_problems(project)
    return track.problems(project, open_tasks(project), kind)


def state_problems(project: str) -> list[str]:
    """What is wrong with STATE.md: missing, a section gone, or older than the last closed task."""
    path = projects.folder(project) / STATE_FILE
    if not path.is_file():
        return [f"{STATE_FILE} is missing"]
    text = path.read_text()
    found = {m.group(1).strip().lower() for m in re.finditer(r"^##\s+(.+)$", text, re.M)}
    problems = [f"{STATE_FILE} has no section '## {s}'" for s in STATE_SECTIONS if s.lower() not in found]
    if OBJECTIVE_PLACEHOLDER in text and load_tasks(project):
        problems.append(f"{STATE_FILE} still has no objective: write it before delegating more")
    closed = [e["at"] for e in events(project) if e["kind"] in ("task.accept", "task.fail")]
    if closed and path.stat().st_mtime < max(closed):
        problems.append(f"{STATE_FILE} was last changed before the latest task was closed: bring it up to date")
    return problems


def latest_review(project: str) -> Path | None:
    folder = projects.folder(project) / REVIEWS_DIR
    found = sorted(folder.glob("*.md")) if folder.is_dir() else []
    return found[-1] if found else None


def yield_turn(project: str, kind: str, fields: dict[str, str]) -> dict:
    """Record why the Coordinator ends its turn, after checking the reason holds. Raises PursuitError."""
    fields = {k: (v or "").strip() for k, v in fields.items()}
    required = {
        "done": ("evidence",),
        "waiting": (),
        "owner": ("question", "recommend", "default"),
        "blocked": ("need", "tried"),
    }
    if kind not in required:
        raise PursuitError(f"The kind is one of {', '.join(KINDS)}")
    missing = [f"--{k}" for k in required[kind] if not fields.get(k)]
    if missing:
        raise PursuitError(f"'{kind}' needs {' and '.join(missing)}")
    found = problems(project, kind)
    if found:
        raise PursuitError("; ".join(found))
    pending = [t for t in open_tasks(project) if awaiting_report(t)]
    reported = [t for t in open_tasks(project) if not awaiting_report(t)]
    if reported:
        names = ", ".join(t["session"] for t in reported)
        raise PursuitError(
            f"{names} reported and wait for you: accept, reject, or fail first (agentop coordinator task show <name>)"
        )
    if kind == "waiting" and not pending:
        raise PursuitError("Nothing is pending: no open task awaits a report. Take the next action instead")
    if kind == "done":
        if pending:
            raise PursuitError(f"Tasks are still open: {', '.join(t['session'] for t in pending)}. Use 'waiting', or close them")
        closed = [e["at"] for e in events(project) if e["kind"] in ("task.accept", "task.fail")]
        reviews = [e["at"] for e in events(project) if e["kind"] == "review"]
        if closed and (not reviews or max(reviews) < max(closed)):
            raise PursuitError(
                "Tasks were closed since the last review. Run agentop coordinator review, write your reflection, then yield"
            )
        review = latest_review(project)
        if review is not None and REFLECTION_PLACEHOLDER in review.read_text():
            raise PursuitError(f"Write your reflection in {review} before yielding done")
    if kind == "waiting":
        fields["sessions"] = ", ".join(t["session"] for t in pending)
    if kind == "owner" and track.exists(project):
        with _locked(project):
            action, refreshed = track.ask(project, None, fields["question"], fields["recommend"], fields["default"])
        fields["action"] = track.action_ref(action)
        fields["asked"] = str(track.asked_times(action)) if refreshed else ""
    return log(project, "yield", type=kind, **{k: v for k, v in fields.items() if v})
