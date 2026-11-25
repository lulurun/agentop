"""A project's pursuit record: the Coordinator's tasks, its stop reasons, and the event log review reads.

Kept in `<project>/.project/` (tasks.json, events.jsonl) and mirrored into the track (track.py). See docs/0_13_projects.md, section 4.3.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import tempfile
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
# The yields after which agentop may clear the context at the next wake (spec 9.2).
CLEARING_KINDS = ("done", "waiting", "owner")
NEEDS_NEXT = ("done", "waiting")
NEXT_FILE = "NEXT.md"
# A --next made of these alone says nothing a cleared context can act on.
NEXT_PLACEHOLDERS = {
    "", "tbd", "todo", "next", "na", "n a", "none", "nothing", "same", "same as before", "wait", "waiting",
    "continue", "carry on", "see above", "as above", "the next action", "next action", "done",
}

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
    "[agentop] Your context was cleared before this message. Run agentop coordinator status "
    "(it starts with your Next), then act on it:\n\n"
)
# What reaches the Coordinator through agentop; a yield after the last of them ended the turn that handled it.
WAKE = "wake"


def close_request(project: str) -> dict | None:
    last = next((e for e in reversed(events(project)) if e["kind"] in ("close.request", "close.complete")), None)
    return last if last and last["kind"] == "close.request" else None


def request_close(project: str, note: str = "") -> dict:
    with _locked(project):
        return close_request(project) or log(project, "close.request", note=note.strip())


def close_notice(project: str) -> str:
    request = close_request(project)
    if request is None:
        return ""
    tasks = open_tasks(project)
    next_step = (f"Wait for {track.task_names(tasks)}; review reports and accept or fail normally."
                 if tasks else f"No task is open: run agentop project close {project} now.")
    return ("[agentop] Priority: close this project. Start no new ordinary work. " + next_step
            + " Unrelated planned work and warnings do not block closing."
            + (f" Request: {request['note']}" if request.get("note") else "") + "\n\n")


def pending_clear(project: str) -> dict | None:
    """The yield --clear request (clear.pending) when no clear has been verified since it."""
    last = next((e for e in reversed(events(project)) if e["kind"] in ("clear.verified", "clear.pending")), None)
    return last if last is not None and last["kind"] == "clear.pending" else None


def cleared(project: str) -> bool:
    """Whether the Coordinator's context was cleared and nothing has reached it since."""
    last = next((e for e in reversed(events(project)) if e["kind"] in ("clear.verified", WAKE)), None)
    return last is not None and last["kind"] == "clear.verified"


def wake_note(project: str, source: str = "send") -> str:
    """Carry pending priority work across a clear and log the wake."""
    note = WAKE_NOTE if cleared(project) else ""
    log(project, WAKE, source=source, cleared=bool(note))
    return note + change_notice(project)


def next_path(project: str) -> Path:
    n = track.generation(project)
    return track.gen_dir(project, n) / NEXT_FILE if n else _meta(project) / NEXT_FILE


def check_next(text: str) -> str:
    """The --next text, stripped. Raises PursuitError for an empty or placeholder one."""
    text = (text or "").strip()
    words = re.sub(r"[\W_]+", " ", text).strip().lower()
    if words in NEXT_PLACEHOLDERS or (text.startswith("<") and text.endswith(">")) or len(words) < 8:
        raise PursuitError(
            "--next must say what you will do next and why, in a sentence: it is all a cleared context starts from"
        )
    return text


def write_next(project: str, kind: str, text: str) -> Path:
    """NEXT.md, replaced whole so a reader never sees half of it."""
    path = next_path(project)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = f"# Next\nDate: {time.strftime('%Y-%m-%d %H:%M')} · yield {kind}\n\n{text.strip()}\n"
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(body)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return path


def read_next(project: str) -> str:
    try:
        return next_path(project).read_text().strip()
    except OSError:
        return ""


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


def _update(project: str, session: str, change, event: str) -> dict:
    with _locked(project):
        tasks = load_tasks(project)
        task = next((t for t in tasks if t["status"] == "open" and _is(t, session)), None)
        if task is None:
            raise PursuitError(f"{session} has no open task. agentop coordinator task list shows them")
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
        if not projects.exists(project):
            raise PursuitError(f"No project called {project}")
        tasks = load_tasks(project)
        if close_request(project):
            raise PursuitError("No new task starts: project close is pending; drain existing tasks, then close")
        if any(t["status"] == "open" and t["session"].lower() == session.lower() for t in tasks):
            raise PursuitError(f"{session} already has an open task. Accept, reject, or fail it first")
        refusal = track.change_refusal(project, [t for t in tasks if t["status"] == "open"])
        if refusal:
            raise PursuitError(f"No new task starts: {refusal[0].lower()}{refusal[1:]}")
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

    task = _update(project, session, change, "report")
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


def fail(project: str, session: str, note: str) -> dict:
    if not note.strip():
        raise PursuitError("Failing needs --note: what is missing, and what you will do instead")

    def change(task):
        task.update(status="failed", closed_at=time.time(), note=note.strip())

    task = _update(project, session, change, "fail")
    log(project, "task.fail", task=task["id"], session=task["session"], rounds=len(task["rounds"]))
    return task


def new_direction(project: str, text: str | None = None, copy_plan: bool = False) -> int:
    with _locked(project):
        return track.new_direction(project, text, copy_plan, open_tasks(project))


def change_direction(project: str, text: str) -> tuple[dict, bool, list[dict]]:
    """Record a direction change that waits for the open tasks; returns (action, replaced, open tasks)."""
    with _locked(project):
        tasks = open_tasks(project)
        action, replaced = track.change_direction(project, text, tasks)
    log(project, "direction.change", action=track.action_ref(action), replaced=replaced, waits=[t["id"] for t in tasks])
    return action, replaced, tasks


def change_due(project: str) -> str:
    """When a direction change is pending and no task is open, the line telling the Coordinator to make it; else ""."""
    if close_request(project) or not track.exists(project) or open_tasks(project):
        return ""
    return track.change_refusal(project, [])


def change_notice(project: str) -> str:
    """Priority close or direction work at the head of a wake prompt."""
    if close_request(project):
        return close_notice(project)
    due = change_due(project)
    return f"[agentop] {due}; it takes the pending text.\n\n" if due else ""


def problems(project: str, kind: str | None = None) -> list[str]:
    """What blocks a yield: the track's checks, or for a project still on STATE.md (no track/), the old ones."""
    if close_request(project):
        return []
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
        "done": ("evidence", "next"),
        "waiting": ("next",),
        "owner": ("question", "recommend", "default"),
        "blocked": ("need", "tried"),
    }
    if kind not in required:
        raise PursuitError(f"The kind is one of {', '.join(KINDS)}")
    missing = [f"--{k}" for k in required[kind] if not fields.get(k)]
    if missing:
        raise PursuitError(f"'{kind}' needs {' and '.join(missing)}")
    if kind in NEEDS_NEXT or fields.get("next"):
        fields["next"] = check_next(fields.get("next", ""))
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
        if close_request(project):
            raise PursuitError(f"Project close is pending: drain existing tasks, then agentop project close {project}")
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
    if fields.get("next"):
        write_next(project, kind, fields["next"])
    return log(project, "yield", type=kind, **{k: v for k, v in fields.items() if v})
