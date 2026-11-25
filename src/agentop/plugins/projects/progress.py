"""A read-only view of a project's track for the Progress Viewer: one direction's documents, tasks and notes.

Built from the track's Markdown and tasks.jsonl; .project/tasks.json, when readable, enriches the same task.
"""

from __future__ import annotations

import fcntl
import json
import math
import os
import re
import time
from contextlib import contextmanager
from pathlib import Path

from agentop import projects

from . import track

MAX_DOC_BYTES = 1_000_000
# One journal line or one EXECUTION entry; files are streamed, so records past any size stay readable.
MAX_RECORD_BYTES = 256_000
MAX_STREAM_BYTES = 64_000_000
MAX_SNAPSHOT_BYTES = 64_000_000
MAX_ROUNDS = 100
MAX_FIELD_CHARS = 100_000
STATUSES = ("open", "accepted", "failed")
EVENTS = ("open", "report", "reject", "accept", "fail")

_N = r"\d{1,9}"
_EXECUTION = re.compile(rf"^EXECUTION_M({_N})\.md$")
_TASK_HEAD = re.compile(rf"^(P{_N}_M{_N}A{_N}-{_N})  session `([^`]*)`  (\S+)(?: \((.*)\))?\s*$")
_TASK_ID = re.compile(rf"^P({_N})_(M{_N}A{_N})-{_N}$")
_LOOSE_ID = re.compile(rf"^t{_N}$")
_ACTION = re.compile(rf"^M{_N}A{_N}$")
_NOTE_HEAD = re.compile(rf"^(P({_N})_(M({_N})A{_N}) note ({_N}))  by: (\S+)\s*$")
_TIMES = re.compile(rf"^Opened (.+?)(?: · closed (.+?))? · rounds ({_N}) · (\S+)\s*$")
_OUT = re.compile(rf"^- round ({_N}): (BLOCKED: )?(.*)$")
_EVAL = re.compile(rf"^- round ({_N}) (rejected|accepted|failed): (.*)$")
_UNDECIDED = "- (not decided yet)"
# Where an entry starts: a task or note heading of any direction. Other '## ' lines are text inside a report.
_BOUNDARY = re.compile(rf"^## P{_N}_M{_N}A{_N}(?:-{_N}\s| note {_N}\s)")


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

    def name(self, path: Path) -> str:
        return str(path.relative_to(self.base)) if path.is_relative_to(self.base) else path.name

    def inside(self, path: Path) -> Path | None:
        try:
            resolved = path.resolve()
        except OSError:
            return None
        if resolved != self.base and self.base not in resolved.parents:
            self.issues.append(f"{self.name(path)} points outside the project; skipped")
            return None
        return resolved

    def _open(self, path: Path):
        resolved = self.inside(path)
        if resolved is None or not resolved.is_file():
            return None
        try:
            return open(resolved, "rb")
        except OSError:
            return None

    def text(self, path: Path, limit: int = MAX_DOC_BYTES) -> str | None:
        f = self._open(path)
        if f is None:
            return None
        with f:
            data = f.read(limit + 1)
        if len(data) > limit:
            self.issues.append(f"{self.name(path)} is over {limit} bytes; only its start is shown")
            data = data[:limit]
        return data.decode("utf-8", errors="replace")

    def lines(self, path: Path):
        """The file's lines, decoded; a line over MAX_RECORD_BYTES comes as None, and reading stops at MAX_STREAM_BYTES."""
        f = self._open(path)
        if f is None:
            return
        total = 0
        with f:
            while True:
                line = f.readline(MAX_RECORD_BYTES + 1)
                if not line:
                    return
                total += len(line)
                if len(line) > MAX_RECORD_BYTES and not line.endswith(b"\n"):
                    while (rest := f.readline(MAX_RECORD_BYTES)) and not rest.endswith(b"\n"):
                        total += len(rest)
                    total += len(rest)
                    yield None
                else:
                    yield line.rstrip(b"\r\n").decode("utf-8", errors="replace")
                if total > MAX_STREAM_BYTES:
                    self.issues.append(f"{self.name(path)} is over {MAX_STREAM_BYTES} bytes; the rest isn't read")
                    return


def _minute(text) -> float | None:
    if not isinstance(text, str):
        return None
    try:
        return time.mktime(time.strptime(text.strip(), "%Y-%m-%d %H:%M"))
    except (ValueError, OverflowError):
        return None


def _number(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        value = float(value)
    except OverflowError:
        return None
    return value if math.isfinite(value) and value >= 0 else None


def _count(value) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and 0 < value <= MAX_ROUNDS else None


def _text(value) -> str | None:
    return value[:MAX_FIELD_CHARS] if isinstance(value, str) and value else None


def _generation(value) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def _id_in(tid, n: int) -> bool:
    """A task ID that belongs to direction n: P{n}_M{m}A{a}-{k}, or t{k} (its direction is recorded beside it)."""
    if not isinstance(tid, str):
        return False
    found = _TASK_ID.match(tid)
    return int(found[1]) == n if found else bool(_LOOSE_ID.match(tid))


# --- Markdown ---


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
    if times and _count(int(times[3])):
        entry.update(opened_at=_minute(times[1]), closed_minute=times[2], rounds_count=int(times[3]), md_status=times[4])
    else:
        entry["ambiguous"] = True
    try:
        out_at = lines.index("Output:")
    except ValueError:
        entry["ambiguous"] = True
        return entry
    marks = _evaluation_marks(lines, out_at)
    if not marks:
        entry["ambiguous"] = True
        return entry
    eval_at, end = marks[0], _stale_tail(lines, marks)
    entry["input"] = _parse_input(lines[1:out_at])
    if entry["input"] is None:
        entry["ambiguous"] = True
    rounds: dict[int, dict] = {}
    last = None
    for line in lines[out_at + 1:eval_at]:
        found = _OUT.match(line)
        if found and _count(int(found[1])):
            last = rounds.setdefault(int(found[1]), {"round": int(found[1])})
            last.update(summary=found[3], blocked=bool(found[2]))
        elif found:
            entry["ambiguous"], last = True, None
        elif last is not None:
            last["summary"] += "\n" + line
    last, last_key = None, None
    for line in lines[eval_at + 1:end]:
        found = _EVAL.match(line)
        if found and _count(int(found[1])):
            n, kind, text = int(found[1]), found[2], found[3]
            key = {"rejected": "feedback", "accepted": "checked", "failed": "note"}[kind]
            last = rounds.setdefault(n, {"round": n}) if key == "feedback" else entry
            last[key], last_key = text, key
        elif found:
            entry["ambiguous"], last = True, None
        elif last is not None:
            last[last_key] += "\n" + line
    for key, holder in [("checked", entry), ("note", entry), *(("summary", r) for r in rounds.values()),
                        *(("feedback", r) for r in rounds.values())]:
        if isinstance(holder.get(key), str):
            holder[key] = holder[key].strip()
    count = max([entry["rounds_count"] or 0, *rounds])
    entry["rounds"] = [rounds.get(n, {"round": n}) for n in range(1, count + 1)]
    return entry


def _evaluation_marks(lines: list[str], out_at: int) -> list[int]:
    """The lines that open an Evaluation section: 'Evaluation:' after a blank line, followed by a writer's bullet."""
    return [i for i in range(out_at + 1, len(lines) - 1)
            if lines[i] == "Evaluation:" and lines[i - 1] == ""
            and (_EVAL.match(lines[i + 1]) or lines[i + 1] == _UNDECIDED)]


def _stale_tail(lines: list[str], marks: list[int]) -> int:
    """Where the entry's own text ends.

    track._replace_entry rewrites an entry only up to its first '## ' line, so an entry whose text holds one keeps
    the rest of each older version after the new one. Each such rest starts with that same line and what follows it.
    """
    first = next((i for i, line in enumerate(lines) if line.startswith("## ")), None)
    if first is None:
        return len(lines)
    lead = lines[first:first + 3]
    for i in range(max(marks[0] + 2, first + 1), len(lines)):
        if lines[i - 1] == "" and lines[i:i + len(lead)] == lead:
            return i - 1
    return len(lines)


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
    return {"id": head[1], "generation": int(head[2]), "action": head[3], "milestone": int(head[4]),
            "number": int(head[5]), "by": head[6], "recorded": date, "recorded_at": _minute(date),
            "body": "\n".join(lines[1:]).strip()}


def _entries(reader: _Reader, path: Path):
    """(heading, raw entry) for each '## ' entry, streamed; an entry over MAX_RECORD_BYTES comes with raw None."""
    heading, body, size, over = None, [], 0, False
    for line in reader.lines(path):
        if line is not None and _BOUNDARY.match(line):
            if heading is not None:
                yield heading, None if over else "\n".join(body).strip()
            heading, body, size, over = line[3:], [line], len(line), False
            continue
        if heading is None:
            continue
        size += MAX_RECORD_BYTES + 1 if line is None else len(line) + 1
        if size > MAX_RECORD_BYTES:
            over, body = True, []
        elif not over:
            body.append(line)
    if heading is not None:
        yield heading, None if over else "\n".join(body).strip()


def _bare_entry(tid: str, raw: str | None) -> dict:
    return {"id": tid, "raw": raw, "ambiguous": raw is not None, "rounds": [], "input": None, "md_status": None,
            "closed_minute": None, "rounds_count": None, "opened_at": None, "session": None, "tool": None,
            "model": None, "checked": None, "note": None}


def _execution(reader: _Reader, path: Path, n: int, entries: dict, notes: list) -> None:
    where = reader.name(path)
    for heading, raw in _entries(reader, path):
        tid = heading.split()[0] if heading.split() else ""
        note_head = _NOTE_HEAD.match(heading)
        if (note_head and int(note_head[2]) != n) or (_TASK_ID.match(tid) and not _id_in(tid, n)):
            reader.issues.append(f"{where}: '{heading[:60]}' belongs to another direction; skipped")
            continue
        if raw is None:
            reader.issues.append(f"{where}: '{heading[:60]}' is over {MAX_RECORD_BYTES} bytes; its text isn't shown")
            if _TASK_ID.match(tid):
                entries.setdefault(tid, _bare_entry(tid, None))
            continue
        if note_head:
            notes.append(_parse_note(heading, raw, n))
            continue
        entry = parse_task_entry(heading, raw)
        if entry is not None:
            entries[entry["id"]] = entry
        elif _TASK_ID.match(tid):
            entries.setdefault(tid, _bare_entry(tid, raw))
        else:
            reader.issues.append(f"{where}: an entry '## {heading[:60]}' isn't a task or a note; skipped")


# --- tasks.jsonl and tasks.json ---


def _clean_event(event) -> tuple[dict | None, bool]:
    """The event's fields the reader uses, checked; (None, _) when it can't be joined, and whether a field was dropped."""
    if not isinstance(event, dict):
        return None, False
    n = _generation(event.get("direction"))
    if n is None or not _id_in(event.get("id"), n):
        return None, False
    fields = {
        "id": event["id"], "direction": n,
        "event": event.get("event") if event.get("event") in EVENTS else None,
        "status": event.get("status") if event.get("status") in STATUSES else None,
        "at": _number(event.get("at")),
        "round": _count(event.get("round")),
        "action": event.get("action") if isinstance(event.get("action"), str) and _ACTION.match(event["action"]) else None,
        "session": _text(event.get("session")),
    }
    dropped = any(event.get(k) is not None and fields[k] is None for k in ("event", "status", "at", "round", "action", "session"))
    return fields, dropped


def _journal(reader: _Reader, project: str, n: int) -> dict[str, list[dict]]:
    found: dict[str, list[dict]] = {}
    bad = oversized = dropped = 0
    for line in reader.lines(track.root(project) / "tasks.jsonl"):
        if line is None:
            oversized += 1
            continue
        if not line.strip():
            continue
        try:
            event, lost = _clean_event(json.loads(line))
        except (ValueError, RecursionError):
            event, lost = None, False
        if event is None:
            bad += 1
            continue
        dropped += lost
        if event["direction"] == n:
            found.setdefault(event["id"], []).append(event)
    for count, what in ((bad, "unreadable"), (oversized, f"over {MAX_RECORD_BYTES} bytes")):
        if count:
            reader.issues.append(f"track/tasks.jsonl has {count} line{'s' if count > 1 else ''} {what}; skipped")
    if dropped:
        reader.issues.append(f"track/tasks.jsonl has {dropped} line{'s' if dropped > 1 else ''} with invalid fields; those fields are ignored")
    return found


def _snapshot(reader: _Reader, project: str) -> list[dict] | None:
    """tasks.json's tasks, or None when it is missing or unreadable."""
    path = projects.meta_dir(project) / "tasks.json"
    f = reader._open(path)
    if f is None:
        return None
    with f:
        data = f.read(MAX_SNAPSHOT_BYTES + 1)
    try:
        if len(data) > MAX_SNAPSHOT_BYTES:
            raise ValueError
        tasks = json.loads(data)["tasks"]
        if not isinstance(tasks, list):
            raise TypeError
    except (ValueError, KeyError, TypeError, RecursionError):
        reader.issues.append(".project/tasks.json is unreadable; tasks are shown from the track alone")
        return None
    return [t for t in tasks if isinstance(t, dict)]


def _from_snapshot(task: dict) -> dict:
    raw_rounds = task.get("rounds") if isinstance(task.get("rounds"), list) else []
    rounds = []
    for i, r in enumerate(raw_rounds[:MAX_ROUNDS], 1):
        r = r if isinstance(r, dict) else {}
        rounds.append({"round": i, "sent_at": _number(r.get("sent_at")), "reported_at": _number(r.get("reported_at")),
                       "summary": _text(r.get("summary")),
                       "blocked": r.get("blocked") if isinstance(r.get("blocked"), bool) else None,
                       "artifact_present": r.get("artifact_present") if isinstance(r.get("artifact_present"), bool) else None,
                       "feedback": _text(r.get("feedback"))})
    accept = task.get("accept")
    accept = [a for a in accept if isinstance(a, str)][:MAX_ROUNDS * 10] if isinstance(accept, list) else []
    return {
        "session": _text(task.get("session")), "tool": _text(task.get("tool")), "model": _text(task.get("model")),
        "action": task.get("action") if isinstance(task.get("action"), str) and _ACTION.match(task["action"]) else None,
        "status": task.get("status") if task.get("status") in STATUSES else None,
        "opened_at": _number(task.get("opened_at")), "closed_at": _number(task.get("closed_at")),
        "goal": _text(task.get("goal")), "accept": accept or None, "artifact": _text(task.get("artifact")),
        "brief": _text(task.get("brief")), "rounds": rounds,
        "checked": _text(task.get("checked")), "note": _text(task.get("note")),
    }


# --- rows ---


def _first(*values):
    return next((v for v in values if v is not None), None)


def _merge_input(stored: dict, entry: dict | None) -> dict | None:
    md = (entry or {}).get("input") or {}
    goal, artifact = _first(stored.get("goal"), md.get("goal")), _first(stored.get("artifact"), md.get("artifact"))
    accept, brief = _first(stored.get("accept"), md.get("accept")), _first(stored.get("brief"), md.get("brief"))
    if goal is None and artifact is None and not accept:
        return None
    return {"goal": goal or "", "accept": accept or [], "artifact": artifact or "", "brief": brief}


def _merge_rounds(stored: list[dict], md: list[dict], count: int) -> list[dict]:
    rounds = []
    for i in range(1, count + 1):
        s = stored[i - 1] if i <= len(stored) else {}
        m = md[i - 1] if i <= len(md) else {}
        rounds.append({
            "round": i, "sent_at": s.get("sent_at"), "reported_at": s.get("reported_at"),
            "summary": _first(s.get("summary"), m.get("summary")),
            "blocked": bool(_first(s.get("blocked"), m.get("blocked"))),
            "artifact_present": s.get("artifact_present"),
            "feedback": _first(s.get("feedback"), m.get("feedback")),
        })
    return rounds


def _task_row(tid: str, n: int, entry: dict | None, events: list[dict], raw_stored: dict | None) -> dict:
    sources = [name for name, have in (("markdown", entry), ("journal", events), ("tasks.json", raw_stored)) if have]
    stored = _from_snapshot(raw_stored) if raw_stored else {}
    md = entry or {}
    last = events[-1] if events else {}
    closings = [e for e in events if e["event"] in ("accept", "fail")]
    closing = closings[-1] if closings else None
    closed_event_at = next((e["at"] for e in reversed(closings) if e["at"] is not None), None)
    opening = next((e for e in events if e["event"] == "open"), None)
    md_status = {"reported": "open"}.get(md.get("md_status"), md.get("md_status"))
    md_status = md_status if md_status in STATUSES else None

    status = _first(last.get("status"), stored.get("status"), md_status)
    if closing and closing["status"] in ("accepted", "failed"):
        status = closing["status"]
    closed_at, approximate = _first(closed_event_at, stored.get("closed_at")), False
    if closed_at is None and status not in (None, "open"):
        closed_at = _minute(md.get("closed_minute"))
        approximate = closed_at is not None
    if status == "open":
        closed_at, approximate = None, False

    journal_rounds = max((e["round"] or 0 for e in events), default=0)
    count = min(MAX_ROUNDS, max(len(stored.get("rounds", [])), len(md.get("rounds", [])), journal_rounds, md.get("rounds_count") or 0))
    rounds = _merge_rounds(stored.get("rounds", []), md.get("rounds", []), count)
    current = rounds[-1] if rounds else {}
    reported = bool(current.get("summary") or current.get("reported_at")) or last.get("event") == "report" or (
        md.get("md_status") == "reported" and not events)
    if status == "open":
        state = "awaiting review" if reported else "awaiting report"
    else:
        state = status or "unknown"

    action = _first(last.get("action"), (opening or {}).get("action"), stored.get("action"))
    if action is None and _TASK_ID.match(tid):
        action = _TASK_ID.match(tid)[2]
    opened_at = _first(stored.get("opened_at"), (opening or {}).get("at"))
    return {
        "id": tid, "generation": n, "action": action, "unlinked": action is None,
        "session": _first(stored.get("session"), md.get("session"), last.get("session")),
        "tool": _first(stored.get("tool"), md.get("tool")), "model": _first(stored.get("model"), md.get("model")),
        "status": status, "state": state, "blocked": bool(current.get("blocked")) and status == "open" and reported,
        "opened_at": _first(opened_at, md.get("opened_at")),
        "opened_approx": opened_at is None and md.get("opened_at") is not None,
        "closed_at": closed_at, "closed_approx": approximate,
        "input": _merge_input(stored, entry), "rounds": rounds,
        "checked": _first(stored.get("checked"), md.get("checked")), "note": _first(stored.get("note"), md.get("note")),
        "sources": sources,
        "raw": md.get("raw") if md.get("ambiguous") else None,
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
                   if re.fullmatch(_N, p.name) and int(p.name) > 0 and p.is_dir() and reader.inside(p) is not None),
                  reverse=True)


def read(project: str, wanted: int | None = None) -> dict:
    """The progress of one direction, the current one by default; a direction that isn't there falls back to it."""
    with _shared_lock(project):
        return _read(project, wanted)


def _placeholder(direction: str) -> bool:
    body = track._section(direction, "Direction")
    return not body or body == track.DIRECTION_PLACEHOLDER


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
    result["direction_placeholder"] = direction is not None and _placeholder(direction)

    entries: dict[str, dict] = {}
    files = []
    if reader.inside(folder) is not None:
        files = sorted((p for p in folder.iterdir() if _EXECUTION.match(p.name)), key=lambda p: int(_EXECUTION.match(p.name)[1]))
    for path in files:
        if reader.inside(path) is None or not path.is_file():
            continue
        result["missing"]["executions"] = False
        _execution(reader, path, n, entries, result["notes"])

    events = _journal(reader, project, n)
    snapshot: dict[str, dict] = {}
    for task in stored or []:
        if _generation(task.get("direction")) == n and _id_in(task.get("id"), n):
            snapshot[task["id"]] = task

    for tid in dict.fromkeys([*entries, *events, *snapshot]):
        try:
            row = _task_row(tid, n, entries.get(tid), events.get(tid, []), snapshot.get(tid))
        except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
            reader.issues.append(f"{tid}: its records couldn't be read together; skipped")
            continue
        (result["active"] if row["status"] == "open" else result["completed"]).append(row)
    result["active"].sort(key=_open_key)
    result["completed"].sort(key=_closed_key)
    result["notes"].sort(key=lambda note: (note["recorded_at"] is None, -(note["recorded_at"] or 0), -note["number"], note["id"]))
    return result
