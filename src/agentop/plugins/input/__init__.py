"""Input bar: prompts and keys typed in the app's bar, sent to a session's agent.

A prompt goes in the way `agentop send` types it, with the tool's own
`send_text` (a paste, bracketed for the tools that need it, then Enter), so a
prompt of several lines arrives whole. A key is one of a fixed set, sent with
`tmux send-keys`, for what a paste can't do: answer a menu, interrupt with Esc.
(The bar's ^B, tmux's prefix, doesn't come here: send-keys would hand it to the
agent, so the app types it through the terminal's own tmux client instead.)

Only a live session agentop started can be sent to, as with `agentop send`.
"""

from __future__ import annotations

import asyncio

from fastapi import HTTPException
from pydantic import BaseModel, Field

from agentop.agentclis import get_agent
from agentop.tmux import Session

# The bar's key buttons, and the tmux key each one sends.
KEYS = {
    "escape": "Escape",
    "enter": "Enter",
    "up": "Up",
    "down": "Down",
    "left": "Left",
    "right": "Right",
    "tab": "Tab",
    "shift-tab": "BTab",
    **{str(n): str(n) for n in range(10)},
}
MAX_TEXT = 100_000


class Prompt(BaseModel):
    # Empty presses Enter alone.
    text: str = Field("", max_length=MAX_TEXT)


class Keys(BaseModel):
    keys: list[str] = Field(min_length=1, max_length=20)


def register(api):
    def target(session_id: str) -> tuple[dict, str]:
        row = next((s for s in api.sessions() if s["id"] == session_id), None)
        if row is None:
            raise HTTPException(404, "Session not found")
        if not row.get("managed") or row.get("owner"):
            raise HTTPException(409, "Only a session agentop started can be sent prompts")
        tmux_name = (row.get("tmux") or {}).get("session")
        if row.get("status") != "running" or not tmux_name:
            raise HTTPException(409, "The session isn't running")
        return row, tmux_name

    @api.router.post("/sessions/{session_id}/prompt")
    async def send_prompt(session_id: str, body: Prompt):
        row, tmux_name = target(session_id)
        agent = get_agent(row.get("tool") or "")
        if agent is None:
            raise HTTPException(409, f"Unknown tool: {row.get('tool')}")

        def send():
            Session.leave_mode(tmux_name)
            if body.text.strip():
                agent.send_text(tmux_name, body.text)
            else:
                Session.send_keys(tmux_name, "Enter")

        await asyncio.to_thread(send)
        return {"ok": True}

    @api.router.post("/sessions/{session_id}/keys")
    async def send_keys(session_id: str, body: Keys):
        unknown = [k for k in body.keys if k not in KEYS]
        if unknown:
            raise HTTPException(422, f"Unknown key: {', '.join(unknown)}")
        _, tmux_name = target(session_id)

        def send():
            Session.leave_mode(tmux_name)
            Session.send_keys(tmux_name, *(KEYS[k] for k in body.keys))

        await asyncio.to_thread(send)
        return {"ok": True}
