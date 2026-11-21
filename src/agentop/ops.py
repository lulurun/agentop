"""Shared business-logic layer used by both the web API and the CLI."""

from __future__ import annotations

import logging
import os
import re
import time

from agentop import agentclis, projects, registry, scanner
from agentop.agent_instance import AgentInstance
from agentop.agentclis import get_agent
from agentop.agentclis.base import AgentCli
from agentop.results import (
    OpsError,
    SavedSession,
    Sent,
    StartFailed,
    StartResult,
    StopResult,
)
from agentop.tmux import PasteError

# Goes into a tmux session name, where "." and ":" separate target parts.
LOG = logging.getLogger(__name__)

_SHORT_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,31}")


def is_valid_short_name(name: str) -> bool:
    return bool(_SHORT_NAME.fullmatch(name))


def get_sessions(with_meta: bool = True) -> list[dict]:
    """Return all live agent sessions enriched with managed-session registry data."""
    reg = registry.load()
    reg_sessions = reg.get("sessions", {})
    managed_names = {k for k, v in reg_sessions.items() if v.get("managed")}
    sessions = scanner.build_sessions(managed_names, with_meta=with_meta)
    for s in sessions:
        reg_data = reg_sessions.get(s["name"], {})
        if reg_data.get("description"):
            s["description"] = reg_data["description"]
        if s.get("managed"):
            s["project"] = reg_data.get("project") or projects.DEFAULT
            s["display_name"] = reg_data.get("display_name") or _bare(s["name"])
            s["readonly"] = bool(reg_data.get("readonly"))
    return sessions


def current_session() -> tuple[str, str, str] | None:
    """The agentop session this process runs in, as (tmux name, project, name), or None outside one."""
    from agentop.tmux import Session

    tmux_name = Session.current()
    if not tmux_name:
        return None
    record = registry.load()["sessions"].get(tmux_name)
    if not record or not record.get("managed"):
        return None
    return tmux_name, record.get("project") or projects.DEFAULT, record.get("display_name") or _bare(tmux_name)


def _agent(tool: str) -> AgentCli:
    agent = get_agent(tool)
    if agent is None:
        raise OpsError(f"Unknown tool: {tool}")
    return agent


def accepts_prompts(row: dict) -> bool:
    """Whether a session can be sent prompts: agentop started it, and its plugin didn't make it read-only (a dialogue's agents)."""
    return bool(row.get("managed")) and not row.get("readonly")


def _place(agent: AgentCli, name: str | None, short_name: str, project: str, readonly: bool, counted: bool) -> str:
    """Record the session's project and flags before it starts, so it is never listed without them."""
    if not projects.exists(project) and project != projects.DEFAULT:
        raise OpsError(f"No project called {project}")
    name = name or agent.new_session_name(short_name)
    flags = {"readonly": True if readonly else None, "counted": False if not counted else None}
    registry.upsert_session(name, {"project": project, **flags})
    return name


def _with_folder(params: dict | None, cwd: str, project: str) -> dict:
    """A session working outside its project's folder gets the folder as another directory to work in."""
    params = dict(params or {})
    folder = os.path.realpath(projects.folder(project))
    working = os.path.realpath(cwd)
    if os.path.commonpath((working, folder)) != folder:
        params["add_dir"] = folder
    return params


def first_prompt(name: str, prompt: str, project: str | None = None, display_name: str | None = None) -> None:
    """Give a session that has just started its first prompt, once its tool takes input. Raises OpsError.

    A session other than the Coordinator is told about its project first (coordinator.member_header).
    """
    from agentop import coordinator

    record = registry.load()["sessions"].get(name) or {}
    project = project or record.get("project") or projects.DEFAULT
    display_name = display_name or record.get("display_name") or _bare(name)
    agent = _agent(record.get("tool") or "")
    found = projects.get(project)
    if found and display_name != projects.COORDINATOR:
        prompt = f"{coordinator.member_header(found, display_name)}\n\n{prompt.strip()}"
    # Before the tool is ready, a paste is dropped without a word.
    time.sleep(agent.boot_seconds)
    try:
        agent.deliver(name, prompt)
    except PasteError as exc:
        raise OpsError(f"The first prompt didn't reach {display_name}: {exc}") from exc


def _record_start(tool: str, cwd: str, short_name: str, run) -> StartResult:
    """Run a start, and register its outcome. A failure with a tmux session is kept, to be seen and closed.

    The record is what marks a session as agentop's: the tool's own session ID
    when known at launch, and else the agent's pid and start time, from which
    the tool's own records give the ID later, even after it exits
    (history.resolve_agent_ids).
    """
    record = {"managed": True, "tool": tool, "cwd": cwd, "created_at": time.time(), "step": None}
    try:
        result: StartResult = run()
    except StartFailed as exc:
        if exc.name:
            fields = {"display_name": short_name or exc.name, "status": "failed", "error": str(exc)}
            registry.upsert_session(exc.name, {**record, **fields})
        raise
    fields = {
        "display_name": short_name or result.name,
        "status": "running",
        "error": None,
        "agent_session_id": result.agent_session_id,
        "agent_pid": result.pid,
        "agent_started": result.agent_started,
    }
    registry.upsert_session(result.name, {**record, **fields})
    return result


def start(
    tool: str,
    cwd: str,
    short_name: str = "",
    params: dict | None = None,
    name: str | None = None,
    on_progress=None,
    project: str = projects.DEFAULT,
    readonly: bool = False,
    counted: bool = True,
) -> StartResult:
    """Launch a new managed agent session. Raises OpsError, or StartFailed with the tmux session's name if one was left behind.

    `readonly` and `counted` are the starting plugin's (SessionModel): a dialogue's agents are read-only and not counted.
    """
    agent = _agent(tool)
    name = _place(agent, name, short_name, project, readonly, counted)
    params = _with_folder(params, cwd, project)
    return _record_start(tool, cwd, short_name, lambda: agent.start_session(cwd, short_name, params, name=name, on_progress=on_progress))


def stop(name: str, sessions: list[dict] | None = None) -> StopResult:
    """Gracefully stop a managed session. Raises OpsError."""
    if sessions is None:
        sessions = get_sessions()
    s = next((s for s in sessions if s["name"] == name), None)
    if s is None:
        raise OpsError(f"Session not found: {name}")
    if not s.get("managed"):
        raise OpsError(f"{name} is not a managed session")
    agent = _agent(s["tool"])
    tmux_name = (s.get("tmux") or {}).get("session") or name
    return AgentInstance(agent, tmux_name).stop()


def get_saved_sessions(
    tool: str | None = None,
    limit: int = 50,
    _live_sessions: list[dict] | None = None,
) -> list[SavedSession]:
    """Return saved/historical sessions across all (or one) agent tool, excluding active ones.

    Pass _live_sessions to avoid a redundant process scan when the caller already has them.
    """
    live = _live_sessions if _live_sessions is not None else get_sessions()
    # Prefer session_id matching (accurate); fall back to (tool, cwd) for agents
    # that don't expose session_id in live session metadata.
    active_session_ids = {s["session_id"] for s in live if s.get("session_id")}
    agents_with_id = {s["tool"] for s in live if s.get("session_id")}
    active_keys = {
        (s["tool"], s.get("cwd", ""))
        for s in live
        if s.get("tool") and s.get("cwd") and s["tool"] not in agents_with_id
    }

    agents = [get_agent(tool)] if tool else agentclis.AGENTS
    results = []
    for agent in agents:
        if agent is None:
            continue
        try:
            per_agent = limit if tool else max(limit // len(agentclis.AGENTS), 10)
            sessions = agent.get_saved_sessions(limit=per_agent)
            for s in sessions:
                if s.get("session_id") in active_session_ids:
                    continue
                if (s.get("tool"), s.get("cwd", "")) in active_keys:
                    continue
                s["resume_cmd"] = agent.get_resume_cmd(s["session_id"]) or ""
                results.append(s)
        except Exception:
            # One tool's unreadable store mustn't hide the others' sessions.
            LOG.exception("%s: listing saved sessions failed", agent.name)
            continue
    results.sort(key=lambda x: x["last_active"], reverse=True)
    return results[:limit]


def resume_session(
    tool: str,
    session_id: str,
    cwd: str,
    short_name: str = "",
    name: str | None = None,
    on_progress=None,
    project: str = projects.DEFAULT,
) -> StartResult:
    """Resume a saved agent session in a new tmux window. Raises as start does."""
    agent = _agent(tool)
    name = _place(agent, name, short_name, project, False, True)
    params = _with_folder(None, cwd, project)
    return _record_start(
        tool,
        cwd,
        short_name,
        lambda: agent.resume_session(session_id, cwd, short_name, name=name, on_progress=on_progress, params=params),
    )


def delete_saved_session(tool: str, session_id: str) -> None:
    """Permanently delete a saved session from its source storage. Raises OpsError."""
    try:
        _agent(tool).delete_session(session_id)
    except (OSError, NotImplementedError) as exc:
        raise OpsError(str(exc)) from exc


def _bare(name: str) -> str:
    """A session name without its trailing -<suffix>: Eiger-2953204 is Eiger."""
    head, sep, _ = name.rpartition("-")
    return head if sep and head else name


def find_session(target: str, sessions: list[dict] | None = None, project: str | None = None) -> dict:
    """The one live session called target, ignoring case. Raises OpsError, with the matches when several.

    A name is looked up in `project` (`other/dev` names another). A tmux session's
    own name is found in any project, and without the -<suffix> agentop adds in `project`.
    """
    if sessions is None:
        sessions = get_sessions(with_meta=False)
    named_project, bare_target = projects.split_target(target.strip().lstrip("#"))
    project = named_project or project
    wanted = bare_target.lower()
    in_project = [s for s in sessions if s.get("managed") and (project is None or s.get("project") == project)]
    found = [s for s in in_project if (s.get("display_name") or "").lower() == wanted]
    if not found and named_project is None:
        exact = [s for s in sessions if s["name"].lower() == wanted]
        pool = in_project if project else sessions
        found = exact or [s for s in pool if _bare(s["name"]).lower() == wanted]
    if len(found) == 1:
        return found[0]
    if not found:
        where = f" in {project}" if project else ""
        raise OpsError(f"I don't see an active session called '{target}'{where}")
    names = [s["name"] for s in found]
    raise OpsError(f"Several active sessions are called '{target}': {', '.join(names)}", matches=names)


def send(target: str, text: str, sessions: list[dict] | None = None, project: str | None = None) -> Sent:
    """Type a prompt into a live managed session and submit it, the way its tool takes a paste. Raises OpsError.

    target is matched by find_session, in `project`.
    """
    s = find_session(target, sessions, project)
    if not s.get("managed"):
        raise OpsError(f"{s['name']} was not started by agentop, so it can't be sent prompts")
    if not accepts_prompts(s):
        raise OpsError(f"{s['name']} is read-only, so it can't be sent prompts")
    agent = _agent(s["tool"])
    tmux_name = (s.get("tmux") or {}).get("session")
    if not tmux_name:
        raise OpsError(f"{s['name']} has no tmux session")
    if not text.strip():
        raise OpsError("The prompt is empty")
    try:
        agent.deliver(tmux_name, text)
    except PasteError as exc:
        raise OpsError(f"The prompt didn't reach {s['name']}: {exc}") from exc
    return Sent(s["name"], tmux_name)
