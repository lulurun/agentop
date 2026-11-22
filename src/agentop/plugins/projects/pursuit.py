"""A project's pursuit record: the Coordinator's tasks, its stop reasons, and the event log review reads.

Kept in `<project>/.project/` (tasks.json, events.jsonl), written by agentop's commands only. See docs/0_13_projects.md, section 4.3.
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

MAX_ROUNDS = 3
STATE_FILE = "STATE.md"
STATE_SECTIONS = ("Objective", "Where things are", "Decisions", "Open questions for the owner", "Next")
REVIEWS_DIR = "reviews"
REFLECTION_PLACEHOLDER = "(The Coordinator writes this.)"
KINDS = ("done", "waiting", "owner", "blocked")

OBJECTIVE_PLACEHOLDER = '(What the project is for, and what "done" means.)'
STATE_TEMPLATE = """# State of {name}

The Coordinator keeps this current: it is what a fresh context reads first.

## Objective

(What the project is for, and what "done" means.)

## Where things are

(Accepted work with commits and paths, what is deployed, what is in progress.)

## Decisions

(Decided, by whom, and why.)

## Open questions for the owner

(Each with a recommendation and what happens if there is no answer.)

## Next

(The next actions, in order.)
"""


class PursuitError(ValueError):
    pass


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


def task_for(project: str, session: str) -> dict | None:
    """The session's open task, matched ignoring case."""
    wanted = session.lower()
    return next((t for t in open_tasks(project) if t["session"].lower() == wanted), None)


def awaiting_report(task: dict) -> bool:
    return task["status"] == "open" and task["rounds"][-1].get("reported_at") is None


def _update(project: str, session: str, change) -> dict:
    with _locked(project):
        tasks = load_tasks(project)
        wanted = session.lower()
        task = next((t for t in tasks if t["status"] == "open" and t["session"].lower() == wanted), None)
        if task is None:
            raise PursuitError(f"{session} has no open task. agentop task list shows them")
        change(task)
        _save_tasks(project, tasks)
        return task


def open_task(project: str, session: str, tool: str, cwd: str, goal: str, accept: list[str], artifact: str) -> dict:
    """Record a new task before its session starts. Raises PursuitError."""
    goal = goal.strip()
    accept = [a.strip() for a in accept if a.strip()]
    if not goal:
        raise PursuitError("A task needs --goal: the one outcome it delivers")
    if not accept:
        raise PursuitError("A task needs at least one --accept: a criterion you will check its result against")
    resolve_artifact(project, artifact)
    with _locked(project):
        tasks = load_tasks(project)
        if any(t["status"] == "open" and t["session"].lower() == session.lower() for t in tasks):
            raise PursuitError(f"{session} already has an open task. Accept, reject, or fail it first")
        task = {
            "id": f"t{len(tasks) + 1}",
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

    task = _update(project, session, change)
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

    task = _update(project, session, change)
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
                f"{task['id']} has used all {MAX_ROUNDS} rounds. Close it with agentop task fail {task['session']} --note …, "
                "then replan or take the decision to the owner"
            )
        task["rounds"][-1]["feedback"] = feedback.strip()
        task["rounds"].append({"sent_at": time.time()})

    task = _update(project, session, change)
    log(project, "task.reject", task=task["id"], session=task["session"], round=len(task["rounds"]) - 1)
    return task


def fail(project: str, session: str, note: str) -> dict:
    if not note.strip():
        raise PursuitError("Failing needs --note: what is missing, and what you will do instead")

    def change(task):
        task.update(status="failed", closed_at=time.time(), note=note.strip())

    task = _update(project, session, change)
    log(project, "task.fail", task=task["id"], session=task["session"], rounds=len(task["rounds"]))
    return task


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


def write_state_template(folder: Path, name: str) -> None:
    path = folder / STATE_FILE
    if not path.exists():
        path.write_text(STATE_TEMPLATE.format(name=name))


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
    problems = state_problems(project)
    if problems:
        raise PursuitError("; ".join(problems))
    pending = [t for t in open_tasks(project) if awaiting_report(t)]
    reported = [t for t in open_tasks(project) if not awaiting_report(t)]
    if reported:
        names = ", ".join(t["session"] for t in reported)
        raise PursuitError(f"{names} reported and wait for you: accept, reject, or fail first (agentop task show <name>)")
    if kind == "waiting" and not pending:
        raise PursuitError("Nothing is pending: no open task awaits a report. Take the next action instead")
    if kind == "done":
        if pending:
            raise PursuitError(f"Tasks are still open: {', '.join(t['session'] for t in pending)}. Use 'waiting', or close them")
        closed = [e["at"] for e in events(project) if e["kind"] in ("task.accept", "task.fail")]
        reviews = [e["at"] for e in events(project) if e["kind"] == "review"]
        if closed and (not reviews or max(reviews) < max(closed)):
            raise PursuitError("Tasks were closed since the last review. Run agentop review, write your reflection, then yield")
        review = latest_review(project)
        if review is not None and REFLECTION_PLACEHOLDER in review.read_text():
            raise PursuitError(f"Write your reflection in {review} before yielding done")
    if kind == "waiting":
        fields["sessions"] = ", ".join(t["session"] for t in pending)
    return log(project, "yield", type=kind, **{k: v for k, v in fields.items() if v})
