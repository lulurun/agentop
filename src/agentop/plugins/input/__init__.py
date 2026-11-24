"""Input bar: prompts and keys typed in the app's bar, sent to a session's agent the way `agentop send` types them.

With reception on, a prompt is first checked against the session's current work (reception.py).
"""

from __future__ import annotations

import asyncio
import time

from fastapi import HTTPException
from pydantic import BaseModel, Field

from agentop.agentclis import get_agent
from agentop.agentclis.base import typing
from agentop.ops import accepts_prompts
from agentop.tmux import CapturePane, PasteError, Session

from . import reception

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
# After /clear, the next prompt waits for the screen to settle, so the tool has its new conversation open.
SETTLE_SECONDS = 1.2
CLEAR_TIMEOUT = 10.0


class Prompt(BaseModel):
    # Empty presses Enter alone.
    text: str = Field("", max_length=MAX_TEXT)
    # Clear the session's context first: the owner's answer to reception's suggestion.
    clear: bool = False
    # The reception check this prompt followed, to log the owner's choice against it.
    check: str | None = Field(None, max_length=40)


class Check(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_TEXT)


class Switch(BaseModel):
    enabled: bool


class Keys(BaseModel):
    keys: list[str] = Field(min_length=1, max_length=20)


def register(api):
    def target(session_id: str) -> tuple[dict, str]:
        row = next((s for s in api.sessions() if s["id"] == session_id), None)
        if row is None:
            raise HTTPException(404, "Session not found")
        if not accepts_prompts(row):
            raise HTTPException(409, "Only a session agentop started can be sent prompts")
        tmux_name = (row.get("tmux") or {}).get("session")
        if row.get("status") != "running" or not tmux_name:
            raise HTTPException(409, "The session isn't running")
        return row, tmux_name

    log_path = api.data_dir / "reception.jsonl"

    def switch(row: dict) -> dict:
        return {"enabled": reception.enabled(api.store.load(), row), "default": reception.default_on(row)}

    @api.router.get("/sessions/{session_id}/reception")
    async def get_reception(session_id: str):
        row, _ = target(session_id)
        return await asyncio.to_thread(switch, row)

    @api.router.patch("/sessions/{session_id}/reception")
    async def set_reception(session_id: str, body: Switch):
        row, _ = target(session_id)

        def change():
            api.store.save(reception.set_enabled(api.store.load(), row, body.enabled))
            return switch(row)

        return await asyncio.to_thread(change)

    @api.router.post("/sessions/{session_id}/check")
    async def check_prompt(session_id: str, body: Check):
        row, _ = target(session_id)

        def run():
            if not reception.enabled(api.store.load(), row):
                return {"id": None, "verdict": "pass", "reason": "reception is off", "by": None}
            result = reception.check(row, body.text, api.data_dir / "ask")
            if result["verdict"] != "pass" or result["reason"].startswith("the check failed"):
                reception.log(
                    log_path, kind="check", session=reception.key(row), check=result["id"], verdict=result["verdict"],
                    reason=result["reason"], work=result.get("work"), message=result.get("message"), transcript=row.get("session_id"), by=result["by"],
                    ms=result.get("ms"), chars=len(body.text),
                )
            return result

        return await asyncio.to_thread(run)

    def settle(tmux_name: str) -> None:
        deadline = time.monotonic() + CLEAR_TIMEOUT
        last, since = None, time.monotonic()
        while time.monotonic() < deadline:
            time.sleep(0.3)
            screen = CapturePane.screen(tmux_name)
            if screen != last:
                last, since = screen, time.monotonic()
            elif time.monotonic() - since >= SETTLE_SECONDS:
                return

    @api.router.post("/sessions/{session_id}/prompt")
    async def send_prompt(session_id: str, body: Prompt):
        row, tmux_name = target(session_id)
        agent = get_agent(row.get("tool") or "")
        if agent is None:
            raise HTTPException(409, f"Unknown tool: {row.get('tool')}")

        def send():
            if body.check:
                reception.log(log_path, kind="choice", session=reception.key(row), check=body.check, clear=body.clear)
            if body.clear:
                agent.deliver(tmux_name, "/clear")
                settle(tmux_name)
                agent.deliver(tmux_name, reception.resume_note(row) + body.text)
            elif body.text.strip():
                agent.deliver(tmux_name, reception.wake_note(row) + body.text)
            else:
                with typing(tmux_name):
                    Session.leave_mode(tmux_name)
                    Session.send_keys(tmux_name, "Enter")

        try:
            await asyncio.to_thread(send)
        except PasteError as exc:
            raise HTTPException(502, f"The prompt didn't reach the session: {exc}") from exc
        return {"ok": True}

    @api.router.post("/sessions/{session_id}/keys")
    async def send_keys(session_id: str, body: Keys):
        unknown = [k for k in body.keys if k not in KEYS]
        if unknown:
            raise HTTPException(422, f"Unknown key: {', '.join(unknown)}")
        _, tmux_name = target(session_id)

        def send():
            with typing(tmux_name):
                Session.leave_mode(tmux_name)
                Session.send_keys(tmux_name, *(KEYS[k] for k in body.keys))

        await asyncio.to_thread(send)
        return {"ok": True}
