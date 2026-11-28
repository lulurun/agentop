"""Each plan action's result files for the Progress Viewer: results/ entries with its exact P{N}_M{m}A{a}_ prefix,
and the artifacts its tasks name, which older tasks put anywhere in the project folder."""

from __future__ import annotations

import os
import re
from pathlib import Path

from agentop import projects

from . import track

RESULTS_DIR = "results"
MAX_SCAN = 5000
MAX_PER_ACTION = 200
# No leading zeros, so P7_M1A1_ is never P7_M1A01_, and the digits end at the "_": A1 never matches A10.
_PREFIX = re.compile(r"^P([1-9]\d{0,8})_M([1-9]\d{0,8})A([1-9]\d{0,8})_.")


def prefix_key(name: str) -> tuple[int, str] | None:
    """(direction, M{m}A{a}) of a result name with a canonical prefix, else None."""
    found = _PREFIX.match(name)
    return (int(found[1]), f"M{int(found[2])}A{int(found[3])}") if found else None


def _entry(base: Path, path: Path, task: str | None, kind: str) -> dict:
    """One result: where it is, and why it can't be opened, if it can't."""
    try:
        resolved = path.resolve()
    except OSError:
        resolved = None
    if path.is_relative_to(base):
        rel = path.relative_to(base).as_posix()
    elif resolved is not None and resolved.is_relative_to(base):
        rel = resolved.relative_to(base).as_posix()
    else:
        rel = str(path)
    row = {"name": path.name, "path": rel, "task": task, "kind": kind, "size": None, "problem": None}
    if resolved is None or (resolved != base and base not in resolved.parents) or projects.META_DIR in resolved.relative_to(base).parts:
        row["problem"] = "outside"
    elif not os.path.lexists(path) or not resolved.exists():
        row["problem"] = "missing"
    elif resolved.is_dir():
        row["problem"] = "folder"
    elif not resolved.is_file():
        row["problem"] = "unsupported"
    else:
        try:
            row["size"] = resolved.stat().st_size
        except OSError:
            row["problem"] = "missing"
        else:
            row["problem"] = "empty" if row["size"] == 0 else None
    return row


def _artifact_path(base: Path, artifact: str) -> Path | None:
    if not artifact or not isinstance(artifact, str) or "\0" in artifact:
        return None
    path = Path(artifact).expanduser()
    # Not resolved here: a link must be reported as leading outside, not followed to its target's name.
    return Path(os.path.normpath(path if path.is_absolute() else base / path))


def by_action(project: str, n: int, plan: str | None, rows: list[dict]) -> dict:
    """Every action of direction n's plan with its results, plus actions only tasks name; `truncated` when results/ is too big."""
    base = projects.folder(project).resolve()
    actions: dict[str, dict] = {}
    if plan:
        for milestone in track.parse_plan(plan)["milestones"]:
            for a in milestone["actions"]:
                ref = f"M{a['m']}A{a['a']}"
                actions.setdefault(ref, {"ref": ref, "title": a["title"], "status": a["status"], "results": []})

    def action(ref: str) -> dict:
        return actions.setdefault(ref, {"ref": ref, "title": None, "status": None, "results": []})

    seen: set[str] = set()
    for row in sorted(rows, key=lambda r: r["id"]):
        artifact = (row.get("input") or {}).get("artifact")
        if not row.get("action") or not artifact:
            continue
        path = _artifact_path(base, artifact)
        if path is None:
            continue
        entry = _entry(base, path, row["id"], "artifact")
        if entry["path"] not in seen:
            seen.add(entry["path"])
            action(row["action"])["results"].append(entry)

    truncated = False
    folder = base / RESULTS_DIR
    supporting: dict[str, list[dict]] = {}
    if folder.is_dir() and not folder.is_symlink():
        try:
            with os.scandir(folder) as it:
                for count, item in enumerate(it):
                    if count >= MAX_SCAN:
                        truncated = True
                        break
                    key = prefix_key(item.name)
                    if key is None or key[0] != n:
                        continue
                    path = folder / item.name
                    if path.relative_to(base).as_posix() in seen:
                        continue
                    supporting.setdefault(key[1], []).append(_entry(base, path, None, "supporting"))
        except OSError:
            pass
    for ref, entries in supporting.items():
        action(ref)["results"].extend(sorted(entries, key=lambda e: e["name"].lower()))

    for a in actions.values():
        if len(a["results"]) > MAX_PER_ACTION:
            a["results"], a["more"] = a["results"][:MAX_PER_ACTION], len(a["results"]) - MAX_PER_ACTION
    return {"actions": list(actions.values()), "results_truncated": truncated}
