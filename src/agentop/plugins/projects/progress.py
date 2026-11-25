"""A read-only view of a project's track for the Progress Viewer: one direction's documents, tasks and notes.

Built from the track's Markdown and tasks.jsonl; .project/tasks.json, when readable, enriches the same task.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import time
from contextlib import contextmanager
from pathlib import Path

from agentop import projects

from . import track

MAX_FILE_BYTES = 1_000_000
_EXECUTION = re.compile(r"^EXECUTION_M(\d+)\.md$")
_TASK_HEAD = re.compile(r"^(P\d+_M\d+A\d+-\d+)  session `([^`]*)`  (\S+)(?: \((.*)\))?\s*$")
_TASK_ID = re.compile(r"^P(\d+)_(M(\d+)A\d+)-\d+$")
_NOTE_HEAD = re.compile(r"^(P\d+_(M(\d+)A\d+) note (\d+))  by: (\S+)\s*$")
_TIMES = re.compile(r"^Opened (.+?)(?: · closed (.+?))? · rounds (\d+) · (\S+)\s*$")
_OUT = re.compile(r"^- round (\d+): (BLOCKED: )?(.*)$")
_EVAL = re.compile(r"^- round (\d+) (rejected|accepted|failed): (.*)$")


@contextmanager
def _shared_lock(project: str):
    """The pursuit lock, shared, so a reader never sees a task between its tasks.json and track writes."""
    path = projects.meta_dir(project) / "pursuit.lock"
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        yield
        return
    try:
        fcntl.flock(fd, fcntl.LOCK_SH)
        yield
    finally:
        os.close(fd)


class _Reader:
    def __init__(self, project: str):
        self.base = projects.folder(project).resolve()
        self.issues: list[str] = []

    def inside(self, path: Path) -> Path | None:
        try:
            resolved = path.resolve()
        except OSError:
            return None
        if resolved != self.base and self.base not in resolved.parents:
            self.issues.append(f"{path.relative_to(self.base) if path.is_relative_to(self.base) else path.name} points outside the project; skipped")
            return None
        return resolved

    def text(self, path: Path) -> str | None:
        resolved = self.inside(path)
        if resolved is None or not resolved.is_file():
            return None
        try:
            with open(resolved, "rb") as f:
                data = f.read(MAX_FILE_BYTES + 1)
        except OSError:
            return None
        if len(data) > MAX_FILE_BYTES:
            self.issues.append(f"{path.name} is over {MAX_FILE_BYTES} bytes; only its start is shown")
            data = data[:MAX_FILE_BYTES]
        return data.decode("utf-8", errors="replace")


def _minute(text: str | None) -> float | None:
    try:
        return time.mktime(time.strptime((text or "").strip(), "%Y-%m-%d %H:%M"))
    except ValueError:
        return None


def _split_entries(text: str) -> list[tuple[str, str]]:
    """(heading line without '## ', whole entry) for each '## ' entry of an EXECUTION file."""
    entries, current = [], None
    for line in text.splitlines():
        if line.startswith("## "):
            current = [line]
            entries.append(current)
        elif current is not None:
            current.append(line)
    return [(lines[0][3:], "\n".join(lines).strip()) for lines in entries]


def parse_task_entry(heading: str, raw: str) -> dict | None:
    """The fields of a writer-shaped task entry; `ambiguous` when its sections can't all be read."""
    head = _TASK_HEAD.match(heading)
    if not head:
        return None
    lines = raw.splitlines()[1:]
    entry = {"id": head[1], "session": head[2], "tool": head[3], "model": head[4], "raw": raw, "ambiguous": False,
             "opened_at": None, "closed_minute": None, "rounds_count": None, "md_status": None,
             "input": None, "rounds": [], "checked": None, "note": None}
    times = _TIMES.match(lines[0]) if lines else None
    if times:
        entry.update(opened_at=_minute(times[1]), closed_minute=times[2], rounds_count=int(times[3]), md_status=times[4])
    else:
        entry["ambiguous"] = True
    try:
        out_at, eval_at = lines.index("Output:"), lines.index("Evaluation:")
    except ValueError:
        entry["ambiguous"] = True
        return entry
    entry["input"] = _parse_input(lines[1:out_at])
    if entry["input"] is None:
        entry["ambiguous"] = True
    rounds: dict[int, dict] = {}
    last = None
    for line in lines[out_at + 1:eval_at]:
        found = _OUT.match(line)
        if found:
            last = rounds.setdefault(int(found[1]), {"round": int(found[1])})
            last.update(summary=found[3], blocked=bool(found[2]))
        elif last is not None and line.strip():
            last["summary"] += "\n" + line
    last, last_key = None, None
    for line in lines[eval_at + 1:]:
        found = _EVAL.match(line)
        if found:
            n, kind, text = int(found[1]), found[2], found[3]
            key = {"rejected": "feedback", "accepted": "checked", "failed": "note"}[kind]
            if key == "feedback":
                last = rounds.setdefault(n, {"round": n})
            else:
                last = entry
            last[key], last_key = text, key
        elif last is not None and line.strip():
            last[last_key] += "\n" + line
    count = max([entry["rounds_count"] or 0, *rounds])
    entry["rounds"] = [rounds.get(n, {"round": n}) for n in range(1, count + 1)]
    return entry


def _parse_input(lines: list[str]) -> dict | None:
    while lines and not lines[0].strip():
        lines = lines[1:]
    if not lines or not lines[0].startswith("Input: "):
        return None
    artifact_at = next((i for i in range(len(lines) - 1, -1, -1) if lines[i].startswith("Artifact: ")), None)
    if artifact_at is None:
        return None
    body = lines[:artifact_at]
    first_bullet = next((i for i, line in enumerate(body) if line.startswith("- ")), len(body))
    goal = "\n".join(body[:first_bullet])[len("Input: "):]
    accept: list[str] = []
    for line in body[first_bullet:]:
        if line.startswith("- "):
            accept.append(line[2:])
        elif accept:
            accept[-1] += "\n" + line
    brief = next((line[len("Brief: "):] for line in lines[artifact_at + 1:] if line.startswith("Brief: ")), None)
    return {"goal": goal, "accept": accept, "artifact": lines[artifact_at][len("Artifact: "):], "brief": brief}


def _parse_note(heading: str, raw: str, generation: int) -> dict | None:
    head = _NOTE_HEAD.match(heading)
    if not head:
        return None
    lines = raw.splitlines()[1:]
    date = lines[0].strip() if lines else ""
    return {"id": head[1], "generation": generation, "action": head[2], "milestone": int(head[3]),
            "number": int(head[4]), "by": head[5], "recorded": date, "recorded_at": _minute(date),
            "body": "\n".join(lines[1:]).strip()}


def _journal(reader: _Reader, project: str) -> list[dict]:
    text = reader.text(track.root(project) / "tasks.jsonl")
    if text is None:
        return []
    found, bad = [], 0
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except ValueError:
            bad += 1
            continue
        if isinstance(event, dict) and isinstance(event.get("id"), str):
            found.append(event)
        else:
            bad += 1
    if bad:
        reader.issues.append(f"track/tasks.jsonl has {bad} unreadable line{'s' if bad > 1 else ''}; skipped")
    return found


def _snapshot(reader: _Reader, project: str) -> list[dict] | None:
    """tasks.json's tasks, or None when it is missing or unreadable."""
    text = reader.text(projects.meta_dir(project) / "tasks.json")
    if text is None:
        return None
    try:
        data = json.loads(text)
        tasks = data["tasks"]
        assert isinstance(tasks, list)
    except (ValueError, KeyError, TypeError, AssertionError):
        reader.issues.append(".project/tasks.json is unreadable; tasks are shown from the track alone")
        return None
    return [t for t in tasks if isinstance(t, dict) and isinstance(t.get("id"), str)]


def _number(value) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _from_snapshot(task: dict) -> dict:
    rounds = []
    for i, r in enumerate(task.get("rounds") or [], 1):
        r = r if isinstance(r, dict) else {}
        rounds.append({"round": i, "sent_at": _number(r.get("sent_at")), "reported_at": _number(r.get("reported_at")),
                       "summary": r.get("summary"), "blocked": bool(r.get("blocked")),
                       "artifact_present": r.get("artifact_present"), "feedback": r.get("feedback")})
    accept = task.get("accept")
    return {
        "session": task.get("session"), "tool": task.get("tool"), "model": task.get("model"),
        "status": task.get("status"), "opened_at": _number(task.get("opened_at")),
        "closed_at": _number(task.get("closed_at")),
        "input": {"goal": task.get("goal") or "", "accept": accept if isinstance(accept, list) else [],
                  "artifact": str(task.get("artifact") or ""), "brief": task.get("brief") or None},
        "rounds": rounds, "checked": task.get("checked"), "note": task.get("note"),
    }


def _task_row(tid: str, n: int, entry: dict | None, events: list[dict], stored: dict | None) -> dict:
    sources = [name for name, have in (("markdown", entry), ("journal", events), ("tasks.json", stored)) if have]
    base = _from_snapshot(stored) if stored else None
    last = events[-1] if events else {}
    closing = next((e for e in reversed(events) if e.get("event") in ("accept", "fail")), None)
    opening = next((e for e in events if e.get("event") == "open"), None)
    md_status = (entry or {}).get("md_status")

    status = last.get("status") or (base or {}).get("status") or (
        {"reported": "open"}.get(md_status, md_status) if md_status else None)
    closed_at, approximate = _number((closing or {}).get("at")), False
    if closed_at is None and base:
        closed_at = base["closed_at"]
    if closed_at is None and status not in (None, "open") and entry:
        closed_at = _minute(entry.get("closed_minute"))
        approximate = closed_at is not None

    if base:
        rounds, task_input, checked, note = base["rounds"], base["input"], base["checked"], base["note"]
    elif entry:
        rounds, task_input, checked, note = entry["rounds"], entry["input"], entry.get("checked"), entry.get("note")
    else:
        rounds, task_input, checked, note = [], None, None, None
    rounds_count = max(len(rounds), int(last.get("round") or 0), (entry or {}).get("rounds_count") or 0)
    if len(rounds) < rounds_count:
        rounds = rounds + [{"round": i} for i in range(len(rounds) + 1, rounds_count + 1)]
    current = rounds[-1] if rounds else {}
    reported = bool(current.get("summary") or current.get("reported_at")) or (
        not base and (last.get("event") == "report" or md_status == "reported"))
    if status == "open":
        state = "awaiting review" if reported else "awaiting report"
    elif status in ("accepted", "failed"):
        state = status
    else:
        state = status or "unknown"

    action = last.get("action") or (opening or {}).get("action") or (stored or {}).get("action")
    if action is None and entry:
        found = _TASK_ID.match(tid)
        action = found[2] if found else None
    def pick(key):
        return (base or {}).get(key) or (entry or {}).get(key) or last.get(key)

    return {
        "id": tid, "generation": n, "action": action, "unlinked": action is None,
        "session": pick("session"), "tool": pick("tool"), "model": pick("model"),
        "status": status, "state": state, "blocked": bool(current.get("blocked")) and status == "open" and reported,
        "opened_at": (base or {}).get("opened_at") or _number((opening or {}).get("at")) or (entry or {}).get("opened_at"),
        "opened_approx": not base and not opening and bool(entry and entry.get("opened_at")),
        "closed_at": closed_at, "closed_approx": approximate,
        "input": task_input, "rounds": rounds, "checked": checked, "note": note,
        "sources": sources,
        "raw": entry["raw"] if entry and entry["ambiguous"] and not base else None,
    }


def _closed_key(row: dict):
    return (row["closed_at"] is None, -(row["closed_at"] or 0), row["id"])


def _open_key(row: dict):
    return (row["opened_at"] is None, -(row["opened_at"] or 0), row["id"])


def generations(reader: _Reader, project: str) -> list[int]:
    folder = track.root(project)
    if reader.inside(folder) is None or not folder.is_dir():
        return []
    return sorted((int(p.name) for p in folder.iterdir()
                   if p.name.isdigit() and p.is_dir() and reader.inside(p) is not None), reverse=True)


def read(project: str, wanted: int | None = None) -> dict:
    """The progress of one direction, the current one by default; a direction that isn't there falls back to it."""
    with _shared_lock(project):
        return _read(project, wanted)


def _read(project: str, wanted: int | None) -> dict:
    reader = _Reader(project)
    found = generations(reader, project)
    current = found[0] if found else 0
    n = wanted if wanted in found else current
    result = {
        "project": project, "generations": found, "current": current, "generation": n,
        "fallback": wanted is not None and wanted != n,
        "direction": None, "direction_placeholder": False, "plan": None,
        "active": [], "completed": [], "notes": [], "issues": reader.issues,
        "missing": {"track": not found, "direction": True, "plan": True, "executions": True},
        "legacy": False,
    }
    stored = _snapshot(reader, project)
    if not found:
        result["legacy"] = bool(stored) or (projects.folder(project) / track.STATE_FILE).is_file()
        return result

    folder = track.gen_dir(project, n)
    direction = reader.text(folder / "DIRECTION.md")
    plan = reader.text(folder / "PLAN.md")
    result.update(direction=direction, plan=plan)
    result["missing"].update(direction=direction is None, plan=plan is None)
    result["direction_placeholder"] = direction is not None and not track.direction_text(project, n)

    entries: dict[str, dict] = {}
    files = []
    if reader.inside(folder) is not None:
        files = sorted((p for p in folder.iterdir() if _EXECUTION.match(p.name)), key=lambda p: int(_EXECUTION.match(p.name)[1]))
    for path in files:
        text = reader.text(path)
        if text is None:
            continue
        result["missing"]["executions"] = False
        for heading, raw in _split_entries(text):
            note = _parse_note(heading, raw, n)
            if note:
                result["notes"].append(note)
                continue
            entry = parse_task_entry(heading, raw)
            if entry is None:
                tid = heading.split()[0] if heading.split() else ""
                if _TASK_ID.match(tid):
                    entries.setdefault(tid, {"id": tid, "raw": raw, "ambiguous": True, "rounds": [], "input": None,
                                             "md_status": None, "closed_minute": None, "rounds_count": None,
                                             "opened_at": None, "session": None, "tool": None, "model": None})
                else:
                    reader.issues.append(f"{path.name}: an entry '## {heading[:60]}' isn't a task or a note; skipped")
                continue
            if not entry["id"].startswith(f"P{n}_"):
                reader.issues.append(f"{path.name}: {entry['id']} belongs to another direction; skipped")
                continue
            entries[entry["id"]] = entry

    events: dict[str, list[dict]] = {}
    for event in _journal(reader, project):
        if event.get("direction") == n:
            events.setdefault(event["id"], []).append(event)
    snapshot = {t["id"]: t for t in (stored or []) if t.get("direction") == n}

    for tid in dict.fromkeys([*entries, *events, *snapshot]):
        row = _task_row(tid, n, entries.get(tid), events.get(tid, []), snapshot.get(tid))
        (result["active"] if row["status"] == "open" else result["completed"]).append(row)
    result["active"].sort(key=_open_key)
    result["completed"].sort(key=_closed_key)
    result["notes"].sort(key=lambda note: (note["recorded_at"] is None, -(note["recorded_at"] or 0), -note["number"], note["id"]))
    return result
