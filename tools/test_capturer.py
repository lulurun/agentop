#!/usr/bin/env python3
"""Developer tool: test the capturer against a live agent session.

Usage:
    python tools/test_capturer.py [PROMPT] [--session SESSION]

If --session is given, reuse an existing tmux session instead of starting a new one.
Otherwise, starts a fresh session via agentop.

Example:
    python tools/test_capturer.py "what is 2+2?"
    python tools/test_capturer.py "what is 2+2?" --session captest-1351624
    python tools/test_capturer.py "what is 2+2?" --capturer codex
"""

from __future__ import annotations

import argparse
import os
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from agentop.dialogue.capturer import Capturer
from agentop.tmux import CapturePane


def _start_session(tool: str, cwd: str) -> str:
    from agentop import ops as agent_ops
    result = agent_ops.start(tool, cwd, short_name="captest")
    if not result.get("ok"):
        print(f"ERROR starting session: {result.get('error')}")
        sys.exit(1)
    name = result["name"]
    print(f"Started session: {name}")
    print("Waiting 4s for agent to initialize...")
    time.sleep(4)
    return name


def _show_pane(label: str, lines: list[str], content_end: int, full: bool = False) -> None:
    print(f"\n=== {label} ({len(lines)} lines, content_end={content_end}) ===")
    if full:
        for i, line in enumerate(lines):
            marker = " <-- content_end" if i == content_end else ""
            print(f"  [{i:4d}] {line!r}{marker}")
    else:
        start = max(0, content_end - 5)
        print(f"  ... (lines 0-{start - 1} omitted)")
        for i in range(start, min(len(lines), content_end + 12)):
            marker = " <-- content_end" if i == content_end else ""
            print(f"  [{i:4d}] {lines[i]!r}{marker}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Test AgentCapturer against a live session.")
    parser.add_argument("prompt", nargs="?", default="Please write a short haiku about software testing.")
    parser.add_argument("--session", help="Reuse this tmux session (skip start)")
    parser.add_argument("--capturer", default="claude", help="Tool/capturer to test (default: claude)")
    parser.add_argument("--cwd", default=os.path.expanduser("~/workspace/agentop"))
    parser.add_argument("--full", action="store_true", help="Show full pane (not just context around content_end)")
    args = parser.parse_args()

    session = args.session or _start_session(args.capturer, args.cwd)
    stop = threading.Event()

    from agentop.agents import get_agent
    from agentop.dialogue.actor import Actor
    agent = get_agent(args.capturer)
    if agent:
        actor = agent.make_actor(id="test", session=session, name="Tester")
    else:
        actor = Actor(id="test", session=session, name="Tester")
    capturer = actor._capturer

    # --- snapshot before send ---
    snapshot_raw = CapturePane.scrollback(session)
    snap_lines = snapshot_raw.splitlines()
    _show_pane("PANE BEFORE SEND", snap_lines, capturer.content_end(snap_lines), args.full)

    # --- send prompt ---
    print(f"\n=== SENDING PROMPT ===\n  {args.prompt!r}")
    actor.attach(stop)
    actor.send(args.prompt)

    # --- wait for idle: show each screen change ---
    print(f"\n=== WAITING FOR IDLE (screen stable for {capturer.idle_seconds}s) ===")
    last_screen = CapturePane.screen(session)
    last_change = time.monotonic()
    deadline = time.monotonic() + 120
    result_content = None
    change_count = 0

    print(f"  [start] screen ({len(last_screen.splitlines())} lines)")
    while not stop.is_set() and time.monotonic() < deadline:
        time.sleep(2.0)
        screen = CapturePane.screen(session)
        if screen != last_screen:
            last_screen = screen
            last_change = time.monotonic()
            change_count += 1
            print(f"  [change #{change_count}] screen ({len(screen.splitlines())} lines)")
        elif last_screen is not None and time.monotonic() - last_change >= capturer.idle_seconds:
            result_content = CapturePane.scrollback(session)
            break

    if result_content is None:
        print("TIMEOUT — no response captured.")
        sys.exit(1)

    elapsed = time.monotonic() - last_change
    print(f"  [idle] screen unchanged for {capturer.idle_seconds}s ({change_count} changes observed)")

    cur_lines = result_content.splitlines()
    _show_pane("PANE AFTER IDLE (scrollback)", cur_lines, capturer.content_end(cur_lines), args.full)

    print("\n=== EXTRACTED RESPONSE ===")
    response = capturer.extract_response(snapshot_raw, result_content)
    print(response)
    print(f"\n(response length: {len(response)} chars)")


if __name__ == "__main__":
    main()
