#!/usr/bin/env python3
"""Developer tool: test the capturer against a live agent session.

Usage:
    python tools/test_capturer.py [PROMPT] [--session SESSION]

If --session is given, reuse an existing tmux session instead of starting a new one.
Otherwise, starts a fresh Claude Code session via agentop.

Example:
    python tools/test_capturer.py "what is 2+2?"
    python tools/test_capturer.py "what is 2+2?" --session my-session-name
"""

from __future__ import annotations

import argparse
import os
import sys
import threading
import time

# Ensure src/ is on the path when run from repo root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from agentop.dialogue.capturer import ClaudeCodeCapturer, _capture_pane
from agentop.dialogue.actor import Actor


def _start_session(cwd: str) -> str:
    from agentop import ops as agent_ops
    result = agent_ops.start("claude", cwd, short_name="captest")
    if not result.get("ok"):
        print(f"ERROR starting session: {result.get('error')}")
        sys.exit(1)
    name = result["name"]
    print(f"Started session: {name}")
    print("Waiting 4s for agent to initialize...")
    time.sleep(4)
    return name


def main() -> None:
    parser = argparse.ArgumentParser(description="Test ClaudeCodeCapturer against a live session.")
    parser.add_argument("prompt", nargs="?", default="Please write a short haiku about software testing.")
    parser.add_argument("--session", help="Reuse this tmux session (skip start)")
    parser.add_argument("--cwd", default=os.path.expanduser("~/workspace/agentop"),
                        help="Working dir for new session (default: ~/workspace/agentop)")
    args = parser.parse_args()

    session = args.session or _start_session(args.cwd)

    capturer = ClaudeCodeCapturer()
    stop = threading.Event()
    actor = Actor(id="test", session=session, capturer=capturer)
    actor.attach(stop)

    # --- show current pane state before sending ---
    print("\n=== PANE BEFORE SEND ===")
    before = _capture_pane(session)
    lines = before.splitlines()
    for i, line in enumerate(lines[-10:], start=max(0, len(lines) - 10)):
        print(f"  [{i:4d}] {line!r}")

    print(f"\n=== SENDING PROMPT ===\n  {args.prompt!r}")
    actor.send(args.prompt)

    # Poll and show the indicator line as we wait
    print("\n=== WAITING FOR IDLE (indicator line updates) ===")
    deadline = time.monotonic() + 120
    last_indicator = ""
    result_content = None

    snapshot = before
    import time as _time

    # Replicate wait loop with live indicator display
    import subprocess

    last_content = _capture_pane(session)
    last_indicator_val = capturer.indicator_line(last_content.splitlines())
    last_change = _time.monotonic()

    while not stop.is_set() and _time.monotonic() < deadline:
        _time.sleep(2.0)
        current = _capture_pane(session)
        indicator = capturer.indicator_line(current.splitlines())
        if indicator != last_indicator_val:
            print(f"  indicator: {indicator!r}")
            last_indicator_val = indicator
            last_content = current
            last_change = _time.monotonic()
        elif _time.monotonic() - last_change >= capturer.idle_seconds:
            result_content = current
            break

    if result_content is None:
        print("TIMEOUT or no response captured.")
        sys.exit(1)

    print("\n=== PANE AFTER IDLE ===")
    cur_lines = result_content.splitlines()
    print(f"  total lines: {len(cur_lines)}")
    print("  last 10 lines (raw):")
    for i, line in enumerate(cur_lines[-10:], start=max(0, len(cur_lines) - 10)):
        print(f"  [{i:4d}] {line!r}")

    print("\n=== EXTRACTED RESPONSE ===")
    response = capturer.extract_response(snapshot, result_content)
    print(response)
    print(f"\n(response length: {len(response)} chars)")


if __name__ == "__main__":
    main()
