"""Sessions' start from a prompt: the probe, the chooser, idle tidying, and their record (docs/0_04_start.md)."""

from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Callable, Optional

from agentop import agentclis, jsonfile, paths, projects, registry, tmux
from agentop.agentclis.base import TIERS, AgentCli

DEFAULTS = {"probe_tool": "antigravity", "probe_model": None, "tool_order": ["codex", "claude", "antigravity"], "idle_hours": 24}
KINDS = ("code", "writing", "research", "chat")
ALIASES = {"agy": "antigravity", "gemini": "antigravity", "claude code": "claude", "anthropic": "claude", "openai": "codex", "gpt": "codex"}
ROOTS = ("workspace", "code", "src", "projects", "repos")
RECENT_DAYS = 30
MAX_CANDIDATES = 40
PROMPT_CHARS = 6_000
PROBE_TIMEOUT = 40
# A session idle less than this is still in use, and never closed to make room.
MIN_IDLE_FOR_ROOM = 600
FULL = 90.0
CLEAR_LEAD = 20.0
UNKNOWN = 50.0

_record_lock = threading.Lock()


# ------------------------------------------------------------------ settings


def _settings_path() -> Path:
    return paths.state_dir() / "start.json"


def settings() -> dict:
    try:
        saved = json.loads(_settings_path().read_text())
    except (OSError, ValueError):
        saved = {}
    return {**DEFAULTS, **{k: v for k, v in (saved if isinstance(saved, dict) else {}).items() if k in DEFAULTS}}


def save_settings(changes: dict) -> dict:
    """Merge and save. Raises ValueError for a tool, model or number that isn't one."""
    merged = {**settings(), **{k: v for k, v in changes.items() if k in DEFAULTS}}
    agent = agentclis.get_agent(merged["probe_tool"])
    if agent is None or agent.ask_cmd("") is None:
        raise ValueError(f"{merged['probe_tool']} can't answer a one-shot prompt")
    if merged["probe_model"]:
        merged["probe_model"] = agent.model_id(merged["probe_model"])
    order = [t for t in merged["tool_order"] if agentclis.get_agent(t)]
    merged["tool_order"] = order + [n for n in agentclis.names() if n not in order]
    hours = merged["idle_hours"]
    if not isinstance(hours, (int, float)) or hours < 0:
        raise ValueError("idle_hours must be 0 (off) or more")
    jsonfile.write_json_atomic(_settings_path(), merged)
    return merged


# ------------------------------------------------------------------ the record


def _record_path() -> Path:
    return projects.meta_dir(projects.DEFAULT) / "starts.jsonl"


def record(event: dict) -> None:
    line = json.dumps({"at": time.time(), **event}, ensure_ascii=False)
    path = _record_path()
    with _record_lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as fh:
            fh.write(line + "\n")


def _events(limit_bytes: int = 512 * 1024) -> list[dict]:
    try:
        with _record_path().open("rb") as fh:
            fh.seek(0, os.SEEK_END)
            fh.seek(max(0, fh.tell() - limit_bytes))
            lines = fh.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return []
    events = []
    for line in lines:
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


def recent(limit: int = 30) -> list[dict]:
    """The sessions started from the buffer, newest first, each with why it was closed if tidying closed it."""
    launched: dict[str, dict] = {}
    closed: dict[str, str] = {}
    for event in _events():
        if event.get("event") == "launched" and event.get("session"):
            launched[event["session"]] = {
                "session": event["session"],
                "title": event.get("title") or event.get("name") or "",
                "tool": event.get("tool"),
                "model": event.get("model"),
                "cwd": event.get("cwd"),
                "at": event.get("at"),
            }
        elif event.get("event") == "closed" and event.get("session"):
            closed[event["session"]] = event.get("why") or ""
    rows = sorted(launched.values(), key=lambda r: r.get("at") or 0, reverse=True)[:limit]
    return [{**row, "closed_why": closed.get(row["session"])} for row in rows]


# ------------------------------------------------------------------ candidates


def candidates(home: Optional[Path] = None, now: Optional[float] = None) -> list[str]:
    """Folders a prompt may mean: recent sessions' folders, newest first, then the Git repositories under the usual roots."""
    home = home or Path.home()
    now = now or time.time()
    found: list[str] = []
    agentop_projects = str(paths.projects_dir())

    def add(path: str) -> None:
        if path and path not in found and not path.startswith(agentop_projects) and os.path.isdir(path):
            found.append(path)

    records = registry.load()["sessions"].values()
    for rec in sorted(records, key=lambda r: r.get("created_at") or 0, reverse=True):
        if (rec.get("created_at") or 0) >= now - RECENT_DAYS * 86400:
            add(rec.get("cwd") or "")
    for root in ROOTS:
        try:
            children = sorted((home / root).iterdir())
        except OSError:
            continue
        for child in children:
            if (child / ".git").exists():
                add(str(child))
    return found[:MAX_CANDIDATES]


# ------------------------------------------------------------------ the probe

SYSTEM = """You read the first prompt of a new coding-agent session and decide how to start it. You never do the task.
Answer with one JSON object and nothing else:
{"title": "<3 to 6 words saying what the task is>",
 "name": "<1 to 3 lowercase words joined by -, for the session's name>",
 "kind": "code" | "writing" | "research" | "chat",
 "tier": "light" | "standard" | "complex",
 "tool": "<a tool name, only if the prompt names a tool to use, else null>",
 "model": "<a model, only if the prompt names a model to use, else null>",
 "cwd": "<the folder to work in, or \\"none\\">",
 "cwd_sure": true | false,
 "reason": "<one sentence: why this tier and folder>"}
Tier: light for a quick question, a lookup, or a small mechanical edit; standard for ordinary work in one place;
complex for design, debugging something unclear, a change across many files, or anything that needs deep reasoning.
cwd: a folder the prompt names, or the one of <folders> the task is about (a repository named in the prompt, its
language or product). "none" when the task needs no files at all (a general question). cwd_sure is false when you
are guessing between folders; then pick the likeliest anyway.
Write title and reason in the language of the prompt."""


def _catalogue_text(installed: list[str]) -> str:
    lines = []
    for name in installed:
        agent = agentclis.get_agent(name)
        if agent and agent.models:
            lines.append(f"{name}: " + ", ".join(f"{m.label} ({m.tier})" for m in agent.models))
    return "\n".join(lines)


def build_prompt(prompt: str, folders: list[str], installed: list[str]) -> str:
    cut = prompt if len(prompt) <= PROMPT_CHARS else prompt[:PROMPT_CHARS] + " …"
    return (
        f"<tools>\n{_catalogue_text(installed)}\n</tools>\n\n"
        f"<folders>\n{chr(10).join(folders) or '(none)'}\n</folders>\n\n"
        f"<prompt>\n{cut}\n</prompt>"
    )


def slug(text: str) -> str:
    words = re.findall(r"[a-z0-9]+", text.lower())
    return "-".join(words)[:32].strip("-")


def fallback(prompt: str) -> dict:
    """What a start goes on with when the probe can't answer."""
    words = prompt.split()
    title = " ".join(words[:6]) + (" …" if len(words) > 6 else "")
    return {"title": title[:80], "name": slug(" ".join(words[:3])) or "session", "kind": "code", "tier": "standard",
            "tool": None, "model": None, "cwd": None, "cwd_sure": False, "reason": ""}


def _named(agent_name, model_name, installed: list[str]) -> tuple[Optional[str], Optional[str]]:
    """A tool and model the prompt named, if they are real: a model alone finds its tool."""
    tool = str(agent_name or "").strip().lower() or None
    tool = ALIASES.get(tool, tool) if tool else None
    if tool not in installed:
        tool = None
    model = None
    if model_name:
        for name in [tool] if tool else installed:
            agent = agentclis.get_agent(name)
            try:
                model = agent.model_id(str(model_name))
                tool = name
                break
            except ValueError:
                continue
    return tool, model


def parse(answer: str, folders: list[str], installed: list[str]) -> dict:
    """The probe's answer, checked: anything unusable becomes the fallback's. Raises ValueError with no JSON."""
    found = re.search(r"\{.*\}", answer, re.DOTALL)
    if not found:
        raise ValueError(f"no JSON in the answer: {answer[:200]!r}")
    data = json.loads(found.group(0))
    title = str(data.get("title") or "").strip()[:80]
    name = slug(str(data.get("name") or "")) or slug(title)
    tier = str(data.get("tier") or "").lower()
    kind = str(data.get("kind") or "").lower()
    tool, model = _named(data.get("tool"), data.get("model"), installed)
    cwd_given = str(data.get("cwd") or "").strip()
    cwd: Optional[str]
    if cwd_given.lower() in ("none", "null", ""):
        cwd = "none" if cwd_given.lower() == "none" else None
    else:
        cwd = os.path.abspath(os.path.expanduser(cwd_given))
        if not os.path.isdir(cwd):
            cwd = None
    return {
        "title": title,
        "name": name or "session",
        "kind": kind if kind in KINDS else "code",
        "tier": tier if tier in TIERS else "standard",
        "tool": tool,
        "model": model,
        "cwd": cwd,
        "cwd_sure": bool(data.get("cwd_sure")) and cwd is not None,
        "reason": str(data.get("reason") or "").strip()[:300],
    }


def probe_agent(installed: list[str]) -> tuple[Optional[AgentCli], Optional[str]]:
    """The settings' probe tool and model, or else the first installed tool with a one-shot command."""
    wanted = settings()
    names = [wanted["probe_tool"]] + [n for n in installed if n != wanted["probe_tool"]]
    for name in names:
        agent = agentclis.get_agent(name)
        if agent and name in installed and agent.ask_cmd("") is not None:
            return agent, (wanted["probe_model"] if name == wanted["probe_tool"] else None) or agent.ask_model
    return None, None


# ------------------------------------------------------------------ the chooser


def quotas(installed: list[str]) -> dict[str, Optional[list[dict]]]:
    found = {}
    for name in installed:
        agent = agentclis.get_agent(name)
        try:
            windows = agent.quota() if agent else None
        except Exception:
            windows = None
        found[name] = [vars(w) for w in windows] if windows else None
    return found


def _used(windows: Optional[list[dict]]) -> Optional[float]:
    return max(w["used_percent"] for w in windows) if windows else None


def choose(tier: str, installed: list[str], seen: dict[str, Optional[list[dict]]], order: list[str], named_tool=None, named_model=None) -> dict:
    """{tool, model, why}: the tier's model on the tool with the most quota left, the owner's order breaking near ties."""
    if named_tool and named_model:
        return {"tool": named_tool, "model": named_model, "why": "named in the prompt"}
    pool = [named_tool] if named_tool else [n for n in installed]
    options = []
    for name in pool:
        agent = agentclis.get_agent(name)
        model = next((m for m in (agent.models if agent else ()) if m.tier == tier), None)
        if agent and (model or named_tool):
            options.append((name, model.id if model else None))
    if not options:
        name = named_tool or (installed[0] if installed else None)
        return {"tool": name, "model": None, "why": "no model of that tier: the tool's default"}

    def rank(option):
        used = _used(seen.get(option[0]))
        known = UNKNOWN if used is None else used
        position = order.index(option[0]) if option[0] in order else len(order)
        # Bands: full tools last; otherwise use counts only in steps of CLEAR_LEAD.
        return (known >= FULL, int(known // CLEAR_LEAD), position)

    options.sort(key=rank)
    tool, model = options[0]

    def said(name):
        used = _used(seen.get(name))
        return f"{name} {'unknown' if used is None else f'at {used:.0f}%'}"

    if named_tool:
        why = f"{tier}: {tool} named in the prompt"
    elif len(options) == 1:
        why = f"{tier}: only {tool} has one"
    else:
        why = f"{tier}: " + ", ".join(said(n) for n, _ in options) + f", so {tool}"
    return {"tool": tool, "model": model, "why": why}


def probe(prompt: str, installed: list[str], ask: Optional[Callable[[str, str], str]] = None) -> dict:
    """The whole decision for one prompt: the probe's answer, then the chooser's tool and model. Never raises."""
    started = time.monotonic()
    folders = candidates()
    decision = {"id": uuid.uuid4().hex[:12], "by": None, "error": None}
    agent, model = (None, None) if ask else probe_agent(installed)
    if ask is None and agent is not None:
        decision["by"] = f"{agent.name} {model}"
        work = paths.state_dir() / "start-probe"

        def ask(text: str, system: str) -> str:
            return agent.ask(text, system, work, timeout=PROBE_TIMEOUT, model=model)

    answer = ""
    if ask is None:
        found, decision["error"] = fallback(prompt), "no tool can answer a one-shot prompt"
    else:
        try:
            answer = ask(build_prompt(prompt, folders, installed), SYSTEM)
            found = parse(answer, folders, installed)
        except (RuntimeError, ValueError) as exc:
            found, decision["error"] = fallback(prompt), str(exc)[:300]
    decision.update(found)
    decision["ms"] = int((time.monotonic() - started) * 1000)
    if decision["cwd"] == "none":
        decision["cwd"], decision["cwd_sure"] = str(projects.folder(projects.DEFAULT)), True
    seen = quotas(installed)
    picked = choose(decision["tier"], installed, seen, settings()["tool_order"], decision["tool"], decision["model"])
    decision.update(picked)
    decision["candidates"] = folders[:6]
    record({"event": "probe", "id": decision["id"], "prompt": prompt[:PROMPT_CHARS], "answer": answer[:2000],
            "decision": {k: decision[k] for k in ("title", "name", "kind", "tier", "tool", "model", "cwd", "cwd_sure", "reason")},
            "quota": seen, "why": decision["why"], "by": decision["by"], "error": decision["error"], "ms": decision["ms"],
            "candidates": folders})
    return decision


# ------------------------------------------------------------------ tidying


def last_activity() -> dict[str, int]:
    """{tmux session: epoch of its first window's last output}, the signal Waiting for input reads too."""
    try:
        r = tmux.run("list-windows", "-a", "-F", "#{session_name}\t#{window_index}\t#{window_activity}", timeout=3, text=True)
    except (OSError, ValueError):
        return {}
    if r.returncode != 0:
        return {}
    first: dict[str, tuple[int, int]] = {}
    for line in r.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) != 3 or not parts[1].isdigit() or not parts[2].isdigit():
            continue
        window, activity = int(parts[1]), int(parts[2])
        if parts[0] not in first or window < first[parts[0]][0]:
            first[parts[0]] = (window, activity)
    return {name: activity for name, (_, activity) in first.items()}


def tidy_candidates(rows: list[dict], activity: dict[str, int]) -> list[tuple[float, dict]]:
    """(last activity, row) of the default project's counted sessions that tidying may close, oldest first."""
    found = []
    for row in rows:
        if (row.get("project") or projects.DEFAULT) != projects.DEFAULT or not row.get("managed") or row.get("counted") is False:
            continue
        if row.get("status") not in ("running", "exited"):
            continue
        tmux_name = (row.get("tmux") or {}).get("session") or row["id"]
        last = activity.get(tmux_name) or row.get("created_at") or 0
        found.append((float(last), row))
    return sorted(found, key=lambda pair: pair[0])


def idle_to_close(rows: list[dict], activity: dict[str, int], hours: float, now: Optional[float] = None) -> list[dict]:
    if hours <= 0:
        return []
    now = now or time.time()
    return [row for last, row in tidy_candidates(rows, activity) if now - last >= hours * 3600]


def room_to_make(rows: list[dict], activity: dict[str, int], now: Optional[float] = None) -> Optional[dict]:
    """The session to close for a start at the limit: the one idle longest, if idle long enough."""
    now = now or time.time()
    for last, row in tidy_candidates(rows, activity):
        return row if now - last >= MIN_IDLE_FOR_ROOM else None
    return None
