"""Shared business-logic layer used by both the web API and the CLI."""

from __future__ import annotations

from dashboard import registry, scanner


def get_sessions() -> list[dict]:
    """Return all live agent sessions enriched with registry descriptions."""
    reg = registry.load()
    descriptions = {
        k: v.get("description", "")
        for k, v in reg.get("sessions", {}).items()
        if v.get("description")
    }
    return scanner.build_sessions(descriptions)


def start(tool: str, cwd: str, description: str = "") -> dict:
    """Launch a new managed agent session.

    Returns {"ok": True, "name": …, "pid": …}
         or {"ok": False, "error": …}.
    """
    result = scanner.start_session_with_tool(tool, cwd)
    if result.get("ok") and description:
        registry.upsert_session(result["name"], {"description": description})
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


def set_description(name: str, text: str) -> None:
    """Save a description for a session."""
    registry.upsert_session(name, {"description": text})
