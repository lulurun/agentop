"""A project's track: its directions, their plans, and what each task did, in `<project>/track/<N>/`.

Written by agentop's commands only; generations older than the current one are never written. See docs/0_13_projects.md, section 4.3.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

from agentop import projects
from agentop.jsonfile import write_json_atomic

TRACK_DIR = "track"
ACTION_STATUSES = ("planned", "in progress", "waiting on owner", "blocked", "done", "dropped")
CLOSED = ("done", "dropped")
DIRECTION_PLACEHOLDER = '(What the project pursues now, and what "done" means.)'
FIRST_MILESTONE = "Agree the direction with the owner"
STATE_FILE = "STATE.md"

DIRECTION_TEMPLATE = """# Direction {n}
Date: {date}
Decided with: {who}

## Direction

{direction}

## Why it changed

{why}

## Carried forward

{carried}
"""

_MILESTONE = re.compile(r"^## M(\d+)\s+(.*?)\s*\[([^\]]*)\]\s*$")
_ACTION = re.compile(r"^- M(\d+)A(\d+)\s+(.*?)\s*\[([^\]]*)\](.*)$")
_REF = re.compile(r"^(?:P(\d+)_)?M(\d+)(?:A(\d+))?$")


class TrackError(ValueError):
    pass


def _today() -> str:
    return time.strftime("%Y-%m-%d")


def _when(at: float | None) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(at)) if at else "—"


def root(project: str) -> Path:
    return projects.folder(project) / TRACK_DIR


def generation(project: str) -> int:
    """The current direction's number: the highest numbered folder in track/, 0 when there is none."""
    folder = root(project)
    found = [int(p.name) for p in folder.iterdir() if p.is_dir() and p.name.isdigit()] if folder.is_dir() else []
    return max(found, default=0)


def exists(project: str) -> bool:
    return generation(project) > 0


def gen_dir(project: str, n: int) -> Path:
    return root(project) / str(n)


def _writable(project: str, n: int) -> Path:
    current = generation(project)
    if n != current:
        raise TrackError(f"track/{n} belongs to an older direction (the current one is {current}); it is never written again")
    return gen_dir(project, n)


def _sole_write(path: Path, text: str) -> None:
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(text)
    tmp.replace(path)


# --- counter and task log ---


def _counter_path(project: str) -> Path:
    return root(project) / "counter.json"


def load_counter(project: str) -> dict:
    try:
        data = json.loads(_counter_path(project).read_text())
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    data.setdefault("tasks", {})
    data.setdefault("total", 0)
    data.setdefault("plan", {"milestones": 0, "actions": {}})
    data["direction"] = generation(project)
    return data


def _save_counter(project: str, counter: dict) -> None:
    write_json_atomic(_counter_path(project), counter)


def next_task_id(project: str, action: str | None) -> tuple[str, int]:
    """A new task's ID, P{N}_M{m}A{a}-{k}, or t{total} without an action; counted in counter.json. Call under the lock."""
    counter = load_counter(project)
    counter["total"] += 1
    if action is None:
        _save_counter(project, counter)
        return f"t{counter['total']}", counter["direction"]
    full = f"P{counter['direction']}_{action}"
    counter["tasks"][full] = counter["tasks"].get(full, 0) + 1
    _save_counter(project, counter)
    return f"{full}-{counter['tasks'][full]}", counter["direction"]


def _carry_id(project: str, action: str) -> str:
    counter = load_counter(project)
    full = f"P{counter['direction']}_{action}"
    counter["tasks"][full] = counter["tasks"].get(full, 0) + 1
    _save_counter(project, counter)
    return f"{full}-{counter['tasks'][full]}"


def _log_path(project: str) -> Path:
    return root(project) / "tasks.jsonl"


def log_task(project: str, task: dict, event: str, **extra) -> None:
    line = {
        "id": task["id"],
        "action": task.get("action"),
        "direction": task.get("direction"),
        "session": task["session"],
        "at": time.time(),
        "event": event,
        "status": task["status"],
        "round": len(task["rounds"]),
        **extra,
    }
    root(project).mkdir(parents=True, exist_ok=True)
    with open(_log_path(project), "a") as f:
        f.write(json.dumps(line, ensure_ascii=False) + "\n")


def task_log(project: str) -> list[dict]:
    try:
        lines = _log_path(project).read_text().splitlines()
    except OSError:
        return []
    found = []
    for line in lines:
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict) and event.get("id"):
            found.append(event)
    return found


def task_states(project: str) -> dict[str, dict]:
    """Each task's last line in tasks.jsonl, in the order tasks first appear; a carried task's old ID is `carried`."""
    states: dict[str, dict] = {}
    for event in task_log(project):
        if event.get("from") in states:
            states[event["from"]] = {**states[event["from"]], "status": "carried", "event": "carry", "to": event["id"]}
        states[event["id"]] = event
    return states


# --- direction ---


def direction_path(project: str, n: int | None = None) -> Path:
    return gen_dir(project, n or generation(project)) / "DIRECTION.md"


def _section(text: str, heading: str) -> str:
    found = re.search(rf"^##\s+{re.escape(heading)}\s*$(.*?)(?=^##\s|\Z)", text, re.M | re.S)
    return found.group(1).strip() if found else ""


def direction_text(project: str, n: int | None = None) -> str:
    """The `## Direction` section of DIRECTION.md, or "" while it is missing or the placeholder."""
    try:
        text = direction_path(project, n).read_text()
    except OSError:
        return ""
    body = _section(text, "Direction")
    return "" if not body or body == DIRECTION_PLACEHOLDER else body


def _direction_file(n: int, text: str) -> str:
    text = text.strip()
    head = f"# Direction {n}\nDate: {_today()}\n"
    if re.search(r"^##\s+Direction\s*$", text, re.M):
        lines = text.splitlines()
        who = next((line for line in lines if line.startswith("Decided with:")), None)
        if who:
            lines.remove(who)
            head += who + "\n"
        return head + "\n" + "\n".join(lines).strip() + "\n"
    return DIRECTION_TEMPLATE.format(
        n=n, date=_today(), who="(who, and the conversation in one line)", direction=text,
        why="(Empty for direction 1.)" if n == 1 else "(What changed from the last direction, and why.)",
        carried="(Repos and branches, deployed state, the owner's standing constraints, lessons.)",
    )


# --- plan ---


def plan_path(project: str, n: int | None = None) -> Path:
    return gen_dir(project, n or generation(project)) / "PLAN.md"


def parse_plan(text: str) -> dict:
    plan = {"head": [], "milestones": []}
    milestone = action = None
    for line in text.splitlines():
        found = _MILESTONE.match(line)
        if found:
            milestone = {"m": int(found[1]), "title": found[2], "status": found[3].strip(), "body": [], "actions": []}
            plan["milestones"].append(milestone)
            action = None
            continue
        found = _ACTION.match(line) if milestone is not None else None
        if found:
            rest = found[5].strip()
            action = {
                "m": int(found[1]), "a": int(found[2]), "title": found[3], "status": found[4].strip(),
                "links": rest[1:].strip() if rest.startswith("→") else "",
                "tail": "" if rest.startswith("→") else rest,
                "notes": [],
            }
            milestone["actions"].append(action)
        elif action is not None and line.startswith("  ") and line.strip():
            action["notes"].append(line)
        elif milestone is not None:
            action = None
            milestone["body"].append(line)
        else:
            plan["head"].append(line)
    return plan


def _strip(lines: list[str]) -> list[str]:
    text = "\n".join(lines).strip("\n")
    return text.split("\n") if text.strip() else []


def action_line(action: dict) -> str:
    rest = f" → {action['links']}" if action["links"] else (f" {action['tail']}" if action["tail"] else "")
    return f"- M{action['m']}A{action['a']} {action['title']}  [{action['status']}]{rest}"


def milestone_line(milestone: dict) -> str:
    return f"## M{milestone['m']} {milestone['title']}  [{milestone['status']}]"


def render_plan(plan: dict, done_short: bool = False) -> str:
    out = _strip(plan["head"])
    for milestone in plan["milestones"]:
        out += ["", milestone_line(milestone)]
        body = _strip(milestone["body"])
        if done_short and milestone["status"] in CLOSED:
            out += [line for line in body if line.startswith("Summary:")][:1]
            continue
        out += body
        for action in milestone["actions"]:
            out += [action_line(action), *action["notes"]]
    return "\n".join(out).strip() + "\n"


def load_plan(project: str, n: int | None = None) -> dict:
    try:
        return parse_plan(plan_path(project, n).read_text())
    except OSError:
        raise TrackError(
            "There is no track/ yet: run agentop coordinator direction new --text - to write the direction and plan"
        ) from None


def save_plan(project: str, plan: dict, n: int | None = None) -> None:
    n = n or generation(project)
    _sole_write(_writable(project, n) / "PLAN.md", render_plan(plan))


def _plan_file(n: int, milestones: str = "") -> str:
    return f"# Plan {n} (from Direction {n})\nDate: {_today()}\n" + (f"\n{milestones.strip()}\n" if milestones.strip() else "")


def parse_ref(project: str, ref: str, want_action: bool) -> tuple[int, int | None]:
    """M2 or M2A3 (P{N}_ prefix allowed for the current N) as (m, a). Raises TrackError."""
    found = _REF.match(ref.strip())
    if not found or (want_action and found[3] is None) or (not want_action and found[3] is not None):
        raise TrackError(f"{ref} is not {'an action (M2A3)' if want_action else 'a milestone (M2)'}")
    if found[1] and int(found[1]) != generation(project):
        raise TrackError(
            f"{ref} belongs to direction {found[1]}, and track/{found[1]} is never written again. "
            f"The current direction is {generation(project)}"
        )
    return int(found[2]), int(found[3]) if found[3] else None


def find_milestone(plan: dict, m: int) -> dict:
    found = next((x for x in plan["milestones"] if x["m"] == m), None)
    if found is None:
        raise TrackError(f"The plan has no M{m}. agentop coordinator plan show lists it")
    return found


def find_action(plan: dict, m: int, a: int) -> dict:
    for milestone in plan["milestones"]:
        for action in milestone["actions"]:
            if (action["m"], action["a"]) == (m, a):
                return action
    raise TrackError(f"The plan has no M{m}A{a}. agentop coordinator plan show lists it")


def milestone_of(plan: dict, action: dict) -> dict:
    return next(x for x in plan["milestones"] if action in x["actions"])


def current_milestone(plan: dict) -> dict | None:
    """The first milestone in progress, else the first not done or dropped, else the last, in plan order."""
    for wanted in (("in progress",), ("planned",)):
        found = next((m for m in plan["milestones"] if m["status"] in wanted), None)
        if found is not None:
            return found
    return plan["milestones"][-1] if plan["milestones"] else None


def add_milestone(project: str, title: str, before: str | None = None) -> dict:
    title = _one_line(title, "A milestone needs a title")
    plan = load_plan(project)
    counter = load_counter(project)
    m = max([counter["plan"]["milestones"], *(x["m"] for x in plan["milestones"])]) + 1
    milestone = {"m": m, "title": title, "status": "planned", "body": [], "actions": []}
    at = len(plan["milestones"])
    if before:
        at = plan["milestones"].index(find_milestone(plan, parse_ref(project, before, False)[0]))
    plan["milestones"].insert(at, milestone)
    save_plan(project, plan)
    counter["plan"]["milestones"] = m
    _save_counter(project, counter)
    return milestone


def add_action(project: str, milestone_ref: str, title: str, before: str | None = None, status: str = "planned",
               notes: list[str] | None = None) -> dict:
    title = _one_line(title, "An action needs a title")
    plan = load_plan(project)
    milestone = find_milestone(plan, parse_ref(project, milestone_ref, False)[0])
    counter = load_counter(project)
    key = f"M{milestone['m']}"
    a = max([counter["plan"]["actions"].get(key, 0), *(x["a"] for x in milestone["actions"] if x["m"] == milestone["m"])]) + 1
    action = {"m": milestone["m"], "a": a, "title": title, "status": status, "links": "", "tail": "", "notes": notes or []}
    at = len(milestone["actions"])
    if before:
        m, b = parse_ref(project, before, True)
        target = find_action(plan, m, b)
        if target not in milestone["actions"]:
            raise TrackError(f"{before} is not in M{milestone['m']}")
        at = milestone["actions"].index(target)
    milestone["actions"].insert(at, action)
    if status not in ("planned", *CLOSED) and milestone["status"] == "planned":
        milestone["status"] = "in progress"
    save_plan(project, plan)
    counter["plan"]["actions"][key] = a
    _save_counter(project, counter)
    return action


def _one_line(text: str, missing: str) -> str:
    text = " ".join((text or "").split()).replace("[", "(").replace("]", ")")
    if not text:
        raise TrackError(missing)
    return text


def action_ref(action: dict) -> str:
    return f"M{action['m']}A{action['a']}"


def set_action(project: str, ref: str, status: str, reason: str = "", note: str = "") -> dict:
    """Set an action's status. `done` needs an execution entry or a note; `dropped` a reason. Raises TrackError."""
    status = status.strip().lower()
    if status not in ACTION_STATUSES or status == "waiting on owner":
        raise TrackError(
            "The status is one of planned, in progress, blocked, done, dropped (a waiting-on-owner action is "
            "made with agentop coordinator plan ask)"
        )
    m, a = parse_ref(project, ref, True)
    plan = load_plan(project)
    action = find_action(plan, m, a)
    if status == "dropped":
        status = f"dropped ({_one_line(reason, 'Dropping needs --reason: why the action is no longer needed')})"
    if note.strip():
        add_note(project, ref, note)
    if status == "done" and not has_entry(project, action_ref(action)):
        raise TrackError(
            f"M{m}A{a} has no execution entry yet: give --note \"what was done\", or record it with "
            f"agentop coordinator note M{m}A{a} \"…\" first"
        )
    action["status"] = status
    milestone = milestone_of(plan, action)
    if status not in ("planned", *CLOSED) and milestone["status"] == "planned":
        milestone["status"] = "in progress"
    save_plan(project, plan)
    return action


def _norm(text: str) -> str:
    return " ".join(text.lower().split()).rstrip("?. ")


def ask(project: str, ref: str | None, question: str, recommend: str, default: str) -> tuple[dict, bool]:
    """A waiting-on-owner action carrying the question; the same open question is refreshed, not added twice.

    ref is an action to turn into the ask, a milestone to add it to, or None for the current one. Returns (action, refreshed).
    """
    question = _one_line(question, "An ask needs --question")
    recommend = _one_line(recommend, "An ask needs --recommend")
    default = _one_line(default, "An ask needs --default")
    plan = load_plan(project)
    same = next(
        (x for ms in plan["milestones"] for x in ms["actions"]
         if x["status"] == "waiting on owner" and _note(x, "Question") and _norm(_note(x, "Question")) == _norm(question)),
        None,
    )
    milestone = None
    if ref is not None and "A" in ref.upper().rpartition("_")[2]:
        m, a = parse_ref(project, ref, True)
        target = find_action(plan, m, a)
        if same is not None and same is not target:
            raise TrackError(f"{action_ref(same)} already asks that: refresh it with plan ask {action_ref(same)}")
        same = target
    elif ref is not None:
        milestone = find_milestone(plan, parse_ref(project, ref, False)[0])
    if same is None:
        milestone = milestone or next((x for x in plan["milestones"] if x["status"] not in CLOSED), None)
        if milestone is None:
            milestone = add_milestone(project, "Questions for the owner")
        return add_action(project, f"M{milestone['m']}", question, status="waiting on owner",
                          notes=_ask_notes(question, recommend, default, 1)), False
    asked = asked_times(same) + 1 if same["status"] == "waiting on owner" and _note(same, "Question") else 1
    same["notes"] = [n for n in same["notes"] if _note_key(n) not in ("Question", "Recommend", "Default", "Asked")]
    same["notes"] = _ask_notes(question, recommend, default, asked) + same["notes"]
    same["status"] = "waiting on owner"
    milestone = milestone_of(plan, same)
    if milestone["status"] == "planned":
        milestone["status"] = "in progress"
    save_plan(project, plan)
    return same, asked > 1


def _ask_notes(question: str, recommend: str, default: str, times: int) -> list[str]:
    asked = f"  Asked: {_today()}" + (f" ({times}×)" if times > 1 else "")
    return [f"  Question: {question}", f"  Recommend: {recommend}", f"  Default: {default}", asked]


def _note_key(line: str) -> str:
    return line.strip().partition(":")[0]


def _note(action: dict, key: str) -> str:
    return next((n.strip().partition(":")[2].strip() for n in action["notes"] if _note_key(n) == key), "")


def asked_times(action: dict) -> int:
    found = re.search(r"\((\d+)×\)", _note(action, "Asked"))
    return int(found[1]) if found else 1


def answer(project: str, ref: str, text: str) -> dict:
    """Close a waiting-on-owner action with the owner's answer, and record it as an execution note by the owner."""
    text = " ".join((text or "").split())
    if not text:
        raise TrackError("An answer needs the owner's words")
    m, a = parse_ref(project, ref, True)
    plan = load_plan(project)
    action = find_action(plan, m, a)
    if action["status"] != "waiting on owner":
        raise TrackError(f"M{m}A{a} is not waiting on the owner (it is {action['status']})")
    action["notes"].append(f"  Answer ({_today()}): {text}")
    action["status"] = "done"
    save_plan(project, plan)
    add_note(project, ref, f"Asked: {_note(action, 'Question')}\nAnswer: {text}", by="owner")
    return action


def close_milestone(project: str, ref: str, summary: str) -> dict:
    summary = (summary or "").strip()
    if not summary:
        raise TrackError("Closing a milestone needs --summary: what was delivered, where it is, what to remember")
    m, _ = parse_ref(project, ref, False)
    plan = load_plan(project)
    milestone = find_milestone(plan, m)
    open_ = [action_ref(x) for x in milestone["actions"] if not x["status"].startswith(CLOSED)]
    if open_:
        raise TrackError(f"M{m} still has open actions: {', '.join(open_)}. Finish, or drop each with a reason, first")
    lines = summary.splitlines()
    milestone["body"] = [line for line in milestone["body"] if not line.startswith("Summary:")] + [f"Summary: {lines[0]}", *lines[1:]]
    milestone["status"] = "done"
    save_plan(project, plan)
    return milestone


# --- execution ---


def execution_path(project: str, n: int, m: int) -> Path:
    return gen_dir(project, n) / f"EXECUTION_M{m}.md"


def _execution_head(n: int, m: int) -> str:
    return f"# Execution of M{m} (Plan {n})\n\nWritten by agentop: one entry per task, and notes of work done without one.\n"


def _replace_entry(path: Path, heading_id: str, entry: str, n: int, m: int) -> None:
    text = path.read_text() if path.exists() else _execution_head(n, m)
    pattern = re.compile(rf"^## {re.escape(heading_id)}\s.*?(?=^## |\Z)", re.M | re.S)
    if pattern.search(text):
        text = pattern.sub(lambda _: entry.rstrip() + "\n\n", text, count=1)
    else:
        text = text.rstrip() + "\n\n" + entry.rstrip() + "\n"
    _sole_write(path, text.rstrip() + "\n")


def task_entry(task: dict) -> str:
    rounds = task["rounds"]
    tool = task["tool"] + (f" ({task['model']})" if task.get("model") else "")
    status = task["status"] if task["status"] != "open" else ("reported" if rounds[-1].get("reported_at") else "open")
    times = f"Opened {_when(task['opened_at'])}" + (f" · closed {_when(task['closed_at'])}" if task.get("closed_at") else "")
    lines = [f"## {task['id']}  session `{task['session']}`  {tool}", f"{times} · rounds {len(rounds)} · {status}"]
    if task.get("carried_from"):
        lines.append(f"Carried from {task['carried_from']}.")
    lines += ["", f"Input: {task['goal']}", *[f"- {c}" for c in task["accept"]], f"Artifact: {task['artifact']}"]
    if task.get("brief"):
        lines.append("Brief: " + " / ".join(task["brief"].strip().splitlines()[:3]))
    output = [f"- round {i}: {'BLOCKED: ' if r.get('blocked') else ''}{r['summary']}"
              for i, r in enumerate(rounds, 1) if r.get("summary")]
    lines += ["", "Output:", *(output or ["- (no report yet)"])]
    evaluation = [f"- round {i} rejected: {r['feedback']}" for i, r in enumerate(rounds, 1) if r.get("feedback")]
    if task["status"] == "accepted":
        evaluation.append(f"- round {len(rounds)} accepted: {task.get('checked', '')}")
    elif task["status"] in ("failed", "interrupted"):
        evaluation.append(f"- round {len(rounds)} {task['status']}: {task.get('note', '')}")
    lines += ["", "Evaluation:", *(evaluation or ["- (not decided yet)"])]
    return "\n".join(lines) + "\n"


def write_task_entry(project: str, task: dict) -> bool:
    """Write or rewrite the task's entry in its milestone's EXECUTION file; False for a task outside the current plan."""
    n = task.get("direction")
    if not task.get("action") or n != generation(project):
        return False
    m, _ = parse_ref(project, task["action"], True)
    _writable(project, n)
    _replace_entry(execution_path(project, n, m), task["id"], task_entry(task), n, m)
    return True


def add_note(project: str, ref: str, text: str, by: str = "coordinator") -> str:
    text = (text or "").strip()
    if not text:
        raise TrackError("A note needs the text: what was done, and where the result is")
    if by not in ("coordinator", "owner"):
        raise TrackError("--by is coordinator or owner")
    m, a = parse_ref(project, ref, True)
    find_action(load_plan(project), m, a)
    n = generation(project)
    path = _writable(project, n) / f"EXECUTION_M{m}.md"
    existing = path.read_text() if path.exists() else ""
    k = len(re.findall(rf"^## P{n}_M{m}A{a} note \d+ ", existing, re.M)) + 1
    heading = f"P{n}_M{m}A{a} note {k}"
    entry = f"## {heading}  by: {by}\n{_when(time.time())}\n\n{text}\n"
    _replace_entry(path, heading, entry, n, m)
    return heading


def has_entry(project: str, action: str, n: int | None = None) -> bool:
    """An accepted task of the action, or a note on it, in its EXECUTION file."""
    n = n or generation(project)
    m = int(re.match(r"M(\d+)", action)[1])
    try:
        text = execution_path(project, n, m).read_text()
    except OSError:
        return False
    full = re.escape(f"P{n}_{action}")
    if re.search(rf"^## {full} note ", text, re.M):
        return True
    return bool(re.search(rf"^## {full}-\d+\s.*\n.*· accepted$", text, re.M))


def refresh_action(project: str, task: dict, open_tasks: list[dict], event: str) -> None:
    """After a task event: the action's links, and its status (in progress, done, or back to planned)."""
    n = task.get("direction")
    if not task.get("action") or n != generation(project):
        return
    m, a = parse_ref(project, task["action"], True)
    plan = load_plan(project)
    try:
        action = find_action(plan, m, a)
    except TrackError:
        return
    prefix = f"P{n}_{task['action']}-"
    links = []
    for tid, state in task_states(project).items():
        if tid.startswith(prefix):
            suffix = "" if state["status"] in ("open", "accepted") else f" ({state['status']})"
            links.append(tid + suffix)
    action["links"] = ", ".join(links)
    others = [t for t in open_tasks if t is not task and t.get("action") == task["action"] and t.get("direction") == n]
    if event in ("open", "carry"):
        action["status"] = "in progress"
    elif event == "accept" and not others:
        action["status"] = "done"
    elif event in ("fail", "interrupt") and not others:
        action["status"] = "planned"
    milestone = milestone_of(plan, action)
    if action["status"] == "in progress" and milestone["status"] in ("planned", "done"):
        milestone["status"] = "in progress"
    save_plan(project, plan)


def on_task(project: str, task: dict, event: str, open_tasks: list[dict], **extra) -> None:
    """Record a task event: its line in tasks.jsonl, its EXECUTION entry and its action in PLAN.md. Call under the lock."""
    if not exists(project):
        return
    log_task(project, task, event, **extra)
    write_task_entry(project, task)
    refresh_action(project, task, open_tasks, event)


def check_action(project: str, action: str) -> str:
    """The action, M{m}A{a}, as written in the current plan, for a task to start on. Raises TrackError."""
    m, a = parse_ref(project, action, True)
    found = find_action(load_plan(project), m, a)
    if found["status"].startswith(CLOSED):
        raise TrackError(f"M{m}A{a} is {found['status']}. Set it back with plan action set, or pick an open action")
    return action_ref(found)


def carry(project: str, task: dict, to: str) -> str:
    """Re-home an open task under an action of the current plan; returns its new ID. Call under the lock."""
    action = check_action(project, to)
    n = generation(project)
    if task.get("action") and task.get("direction") == n:
        raise TrackError(f"{task['id']} is already in the current plan, under {task['action']}")
    old = task["id"]
    task.update(id=_carry_id(project, action), action=action, direction=n, carried_from=old)
    return old


# --- creating generations ---


def write_first(project: str) -> bool:
    """track/1 with a placeholder direction and an empty M1, for a project that has neither track/ nor STATE.md."""
    folder = projects.folder(project)
    if exists(project) or (folder / STATE_FILE).exists():
        return False
    target = gen_dir(project, 1)
    target.mkdir(parents=True, exist_ok=True)
    (target / "DIRECTION.md").write_text(_direction_file(1, DIRECTION_PLACEHOLDER))
    (target / "PLAN.md").write_text(_plan_file(1, f"## M1 {FIRST_MILESTONE}  [planned]"))
    _save_counter(project, load_counter(project))
    return True


def new_direction(project: str, text: str, copy_plan: bool = False, open_tasks: list[dict] | None = None,
                  keep_open: bool = False) -> int:
    """Create track/N+1 (or fill in direction 1 while it is still the placeholder). Returns its number. Under the lock."""
    if not (text or "").strip():
        raise TrackError("A direction needs --text: what the project pursues now and what done means ('-' reads stdin)")
    n = generation(project)
    current = [t for t in open_tasks or [] if t.get("direction", n) == n or not t.get("action")]
    if current and not keep_open:
        raise TrackError(
            f"Tasks of direction {n} are open: {', '.join(t['id'] for t in current)}. Accept or fail them first, or "
            "pass --keep-open and then carry each into the new plan with agentop coordinator task carry <id> --to <action>"
        )
    if n and not direction_text(project, n) and not any(e.get("direction") == n for e in task_log(project)):
        _sole_write(direction_path(project, n), _direction_file(n, text))
        return n
    new = n + 1
    target = gen_dir(project, new)
    target.mkdir(parents=True)
    milestones = ""
    if copy_plan and n:
        old = load_plan(project, n)
        milestones = render_plan({"head": [], "milestones": old["milestones"]})
    (target / "DIRECTION.md").write_text(_direction_file(new, text))
    (target / "PLAN.md").write_text(_plan_file(new, milestones))
    counter = load_counter(project)
    counter["plan"] = {"milestones": 0, "actions": {}}
    _save_counter(project, counter)
    return new


# --- checks ---


def problems(project: str, open_tasks: list[dict], kind: str | None = None) -> list[str]:
    """What blocks a yield in a project with a track (spec section 5), each naming its fix."""
    n = generation(project)
    if not n:
        return ["There is no track/ yet: run agentop coordinator direction new --text - to write the direction"]
    found = []
    if kind != "owner" and not direction_text(project, n):
        found.append(
            f"Direction {n} has no direction text: write it under '## Direction' in track/{n}/DIRECTION.md, "
            "or run agentop coordinator direction new --text -"
        )
    for t in open_tasks:
        if not t.get("action"):
            found.append(f"Task {t['id']} ({t['session']}) has no action: agentop coordinator task carry {t['id']} --to <M…A…>")
        elif t.get("direction") != n:
            found.append(
                f"Task {t['id']} ({t['session']}) belongs to direction {t.get('direction')}, not carried: "
                f"agentop coordinator task carry {t['id']} --to <M…A…>"
            )
    try:
        plan = load_plan(project, n)
    except TrackError as exc:
        return [*found, str(exc)]
    for milestone in plan["milestones"]:
        for action in milestone["actions"]:
            if action["status"] == "done" and not has_entry(project, action_ref(action), n):
                ref = action_ref(action)
                found.append(
                    f"{ref} is done with no execution entry: agentop coordinator note {ref} \"what was done\", "
                    f"or set it back with agentop coordinator plan action set {ref} --status planned"
                )
        closed = all(a["status"].startswith(CLOSED) for a in milestone["actions"])
        summary = any(line.startswith("Summary:") and line[8:].strip() for line in milestone["body"])
        if milestone["actions"] and closed and not (milestone["status"] == "done" and summary):
            found.append(
                f"Every action of M{milestone['m']} is done or dropped: agentop coordinator milestone close "
                f"M{milestone['m']} --summary -"
            )
    return found


def started(project: str) -> bool:
    """Whether the track holds anything beyond the placeholder: a Coordinator starting on it resumes, not welcomes."""
    n = generation(project)
    if not n:
        return False
    if n > 1 or direction_text(project, n) or task_log(project):
        return True
    try:
        plan = load_plan(project, n)
    except TrackError:
        return False
    return any(m["actions"] for m in plan["milestones"]) or len(plan["milestones"]) > 1


def excerpt(project: str) -> str:
    """The direction and the current milestone with its open actions, for a cheap model's context."""
    parts = []
    if direction_text(project):
        parts.append(f"Direction: {direction_text(project)}")
    try:
        milestone = current_milestone(load_plan(project))
    except TrackError:
        milestone = None
    if milestone is not None:
        open_ = [action_line(a) for a in milestone["actions"] if not a["status"].startswith(CLOSED)]
        parts.append("\n".join([f"Current milestone: {milestone_line(milestone)[3:]}", *open_]))
    return "\n".join(parts)
