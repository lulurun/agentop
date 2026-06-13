"""Shared business-logic layer used by both the web API and the CLI."""

from __future__ import annotations

from agentop import registry, scanner
from agentop.agents import AGENTS, get_agent


def get_sessions() -> list[dict]:
    """Return all live agent sessions enriched with managed-session registry data."""
    reg = registry.load()
    reg_sessions = reg.get("sessions", {})
    managed_names = {k for k, v in reg_sessions.items() if v.get("managed")}
    sessions = scanner.build_sessions(managed_names)
    for s in sessions:
        reg_data = reg_sessions.get(s["name"], {})
        if reg_data.get("description"):
            s["description"] = reg_data["description"]
    return sessions


def start(tool: str, cwd: str, short_name: str = "", params: dict | None = None) -> dict:
    """Launch a new managed agent session.

    Returns {"ok": True, "name": …, "pid": …}
         or {"ok": False, "error": …}.
    """
    agent = get_agent(tool)
    if not agent:
        return {"ok": False, "error": f"Unknown tool: {tool}"}
    result = agent.start_session(cwd, short_name, params)
    if result.get("ok"):
        registry.upsert_session(result["name"], {"managed": True})
    return result


def stop(name: str, sessions: list[dict] | None = None) -> dict:
    """Gracefully stop a managed session.

    Returns {"ok": True, "sent_exit": …, "tmux_killed": …}
         or {"ok": False, "error": …}.
    """
    if sessions is None:
        sessions = get_sessions()
    s = next((s for s in sessions if s["name"] == name), None)
    if s is None:
        return {"ok": False, "error": "Session not found"}
    if not s.get("managed"):
        return {"ok": False, "error": "Not a managed session"}
    tmux_name = (s.get("tmux") or {}).get("session") or name
    result = scanner.stop_session(s.get("cwd", ""), tmux_name)
    return {"ok": True, **result}


def get_saved_sessions(
    tool: str | None = None,
    limit: int = 50,
    _live_sessions: list[dict] | None = None,
) -> list[dict]:
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

    agents = [get_agent(tool)] if tool else AGENTS
    results = []
    for agent in agents:
        if agent is None:
            continue
        try:
            per_agent = limit if tool else max(limit // len(AGENTS), 10)
            sessions = agent.get_saved_sessions(limit=per_agent)
            for s in sessions:
                if s.get("session_id") in active_session_ids:
                    continue
                if (s.get("tool"), s.get("cwd", "")) in active_keys:
                    continue
                s["resume_cmd"] = agent.get_resume_cmd(s["session_id"]) or ""
                results.append(s)
        except Exception as exc:
            print(f"[agentop] {agent.name} get_saved_sessions error: {exc}")
            continue
    results.sort(key=lambda x: x["last_active"], reverse=True)
    return results[:limit]


def resume_session(tool: str, session_id: str, cwd: str, short_name: str = "") -> dict:
    """Resume a saved agent session in a new tmux window."""
    agent = get_agent(tool)
    if not agent:
        return {"ok": False, "error": f"Unknown tool: {tool}"}
    result = agent.resume_session(session_id, cwd, short_name)
    if result.get("ok"):
        registry.upsert_session(result["name"], {"managed": True})
    return result


def delete_saved_session(tool: str, session_id: str) -> dict:
    """Permanently delete a saved session from its source storage."""
    agent = get_agent(tool)
    if not agent:
        return {"ok": False, "error": f"Unknown tool: {tool}"}
    try:
        agent.delete_session(session_id)
        return {"ok": True}
    except (FileNotFoundError, NotImplementedError) as e:
        return {"ok": False, "error": str(e)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def set_description(name: str, description: str) -> dict:
    """Set or clear the user-defined description for a managed session."""
    ok = registry.set_description(name, description)
    if not ok:
        return {"ok": False, "error": "Session not found in registry"}
    return {"ok": True}


def send(name: str, text: str, sessions: list[dict] | None = None) -> dict:
    """Send a prompt to a managed session via tmux.

    Returns {"ok": True} or {"ok": False, "error": …}.
    """
    if sessions is None:
        sessions = get_sessions()
    s = next((s for s in sessions if s["name"] == name), None)
    if s is None:
        return {"ok": False, "error": "Session not found"}
    if not s.get("managed"):
        return {"ok": False, "error": "Not a managed session"}
    tmux_name = (s.get("tmux") or {}).get("session")
    if not tmux_name:
        return {"ok": False, "error": "No tmux session found"}
    return scanner.send_to_session(tmux_name, text)
