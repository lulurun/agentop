"""Shared business-logic layer used by both the web API and the CLI."""

from __future__ import annotations

from agentop import registry, scanner


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


def start(tool: str, cwd: str, short_name: str = "") -> dict:
    """Launch a new managed agent session.

    Returns {"ok": True, "name": …, "pid": …}
         or {"ok": False, "error": …}.
    """
    result = scanner.start_session_with_tool(tool, cwd, short_name)
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


def get_saved_sessions(tool: str | None = None, limit: int = 50) -> list[dict]:
    """Return saved/historical sessions across all (or one) agent tool."""
    from agentop.agents import AGENTS, get_agent
    agents = [get_agent(tool)] if tool else AGENTS
    results = []
    for agent in agents:
        if agent is None:
            continue
        try:
            per_agent = limit if tool else max(limit // len(AGENTS), 10)
            sessions = agent.get_saved_sessions(limit=per_agent)
            for s in sessions:
                s["resume_cmd"] = agent.get_resume_cmd(s["session_id"]) or ""
            results.extend(sessions)
        except Exception:
            continue
    results.sort(key=lambda x: x["last_active"], reverse=True)
    return results[:limit]


def resume_session(tool: str, session_id: str, cwd: str, short_name: str = "") -> dict:
    """Resume a saved agent session in a new tmux window."""
    from agentop.agents import get_agent
    agent = get_agent(tool)
    if not agent:
        return {"ok": False, "error": f"Unknown tool: {tool}"}
    result = agent.resume_session(session_id, cwd, short_name)
    if result.get("ok"):
        registry.upsert_session(result["name"], {"managed": True})
    return result


def set_description(name: str, description: str) -> dict:
    """Set or clear the user-defined description for a managed session."""
    data = registry.load()
    if name not in data.get("sessions", {}):
        return {"ok": False, "error": "Session not found in registry"}
    if description:
        data["sessions"][name]["description"] = description
    else:
        data["sessions"][name].pop("description", None)
    registry.save(data)
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
