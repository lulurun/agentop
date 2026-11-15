"""History: the past sessions agentop started, to resume or delete.

A session's registry record keeps the agent tool's own session ID once the
service has seen it (`agent_session_id`). A past session is one whose record
has that ID, is no longer in the session list, and whose transcript the tool
still has and has written to within the history window. Older ones are not
worth reopening, so they are not listed.
"""

from __future__ import annotations

import re
import time
from datetime import datetime, timezone

from agentop.agentclis import get_agent

_PID_SUFFIX = re.compile(r"-\d+$")
# The date and time a resumed session's name ends with (resume_name).
_RESUMED_AT = re.compile(r"\s+\d{4}-\d{2}-\d{2} \d{2}:\d{2}$")
NAME_MAX = 64


def resolve_agent_ids(records: dict[str, dict], listed: set[str]) -> dict[str, dict]:
    """Agent session IDs for ended sessions that never had one recorded, such as a
    Codex session closed while the service was down, from the tool's own records.
    Returns the fields to add, by registry name.
    """
    updates = {}
    for name, record in records.items():
        if name in listed or record.get("agent_session_id") or not record.get("managed"):
            continue
        pid, started = record.get("agent_pid"), record.get("agent_started")
        agent = get_agent(record.get("tool") or "")
        if agent is None or not pid or not started:
            continue
        try:
            found = agent.session_id_after(pid, started)
        except Exception:
            found = None
        if found:
            updates[name] = {"agent_session_id": found}
    return updates


def last_active(tool: str, agent_session_id: str) -> float | None:
    """When the tool last wrote the session's transcript, or None when it has none."""
    agent = get_agent(tool)
    if agent is None:
        return None
    try:
        _base, found = agent.transcript_files(agent_session_id)
        times = [f.stat().st_mtime for f in found if f.is_file()]
    except OSError:
        return None
    return max(times) if times else None


def base_name(name: str, record: dict) -> str:
    """The session's name without the date a resume added: `fix-auth 2026-11-01 10:00` is `fix-auth`."""
    display = record.get("display_name") or _PID_SUFFIX.sub("", name)
    return _RESUMED_AT.sub("", display).strip() or display


def resume_name(item: dict) -> str:
    """`<its name> <when it was last used>`, the name a resumed session gets."""
    stamp = datetime.fromtimestamp(item["last_active"], timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M")
    return f"{item['name'][: NAME_MAX - len(stamp) - 1]} {stamp}"


def list_history(
    records: dict[str, dict],
    listed: set[str],
    live_agent_ids: set[str],
    window_seconds: float,
    now: float | None = None,
) -> list[dict]:
    """Past sessions agentop started, used within the window, newest first.

    `listed` are the registry names still in the session list, and
    `live_agent_ids` the agent session IDs running now: neither is history,
    nor is an agent session one of the listed sessions resumed.
    A session resumed more than once has several records; it is listed once.
    """
    now = time.time() if now is None else now
    in_use = set(live_agent_ids) | {records[n].get("agent_session_id") for n in listed if n in records}
    found: dict[tuple[str, str], dict] = {}
    for name, record in records.items():
        tool, agent_id = record.get("tool"), record.get("agent_session_id")
        if not (record.get("managed") and tool and agent_id) or record.get("owner"):
            continue
        if name in listed or agent_id in in_use:
            continue
        key = (tool, agent_id)
        previous = found.get(key)
        if previous and (previous["created_at"] or 0) >= (record.get("created_at") or 0):
            continue
        found[key] = {
            "id": name,
            "tool": tool,
            "session_id": agent_id,
            "name": base_name(name, record),
            "title": record.get("ai_title"),
            "description": record.get("description") or "",
            "cwd": record.get("cwd") or "",
            "created_at": record.get("created_at"),
            "ended_at": record.get("ended_at"),
        }
    items = []
    for item in found.values():
        updated = last_active(item["tool"], item["session_id"])
        if updated is None or now - updated > window_seconds:
            continue
        item["last_active"] = updated
        items.append(item)
    items.sort(key=lambda i: i["last_active"], reverse=True)
    return items
