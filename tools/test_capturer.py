#!/usr/bin/env python3
"""Developer tool: test the capturer against a live agent session.

Usage:
    python tools/test_capturer.py [PROMPT] [--session SESSION]

If --session is given, reuse an existing tmux session instead of starting a new one.
Otherwise, starts a fresh Claude Code session via agentop.

Example:
    python tools/test_capturer.py "what is 2+2?"
    python tools/test_capturer.py "what is 2+2?" --session captest-1351624
"""

from __future__ import annotations

import argparse
import os
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from agentop.dialogue.capturer import ClaudeCodeCapturer, _capture_pane


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


def _show_pane(label: str, lines: list[str], content_end: int) -> None:
    print(f"\n=== {label} ({len(lines)} lines, content_end={content_end}) ===")
    for i, line in enumerate(lines):
        marker = " <-- content_end" if i == content_end else ""
        print(f"  [{i:4d}] {line!r}{marker}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Test ClaudeCodeCapturer against a live session.")
    parser.add_argument("prompt", nargs="?", default="Please write a short haiku about software testing.")
    parser.add_argument("--session", help="Reuse this tmux session (skip start)")
    parser.add_argument("--cwd", default=os.path.expanduser("~/workspace/agentop"))
    parser.add_argument("--full", action="store_true", help="Show full pane (not just last 15 lines)")
    args = parser.parse_args()

    session = args.session or _start_session(args.cwd)
    capturer = ClaudeCodeCapturer()
    stop = threading.Event()

    # --- snapshot before send ---
    snapshot_raw = _capture_pane(session)
    snap_lines = snapshot_raw.splitlines()
    snap_end = capturer.content_end(snap_lines)

    def _show(label, lines, end):
        if args.full:
            _show_pane(label, lines, end)
        else:
            print(f"\n=== {label} ({len(lines)} lines, content_end={end}) ===")
            start = max(0, end - 5)
            print(f"  ... (lines 0-{start-1} omitted)")
            for i in range(start, min(len(lines), end + 12)):
                marker = " <-- content_end" if i == end else ""
                print(f"  [{i:4d}] {lines[i]!r}{marker}")

    _show("PANE BEFORE SEND", snap_lines, snap_end)

    # --- send prompt ---
    print(f"\n=== SENDING PROMPT ===\n  {args.prompt!r}")
    from agentop.dialogue.actor import Actor
    actor = Actor(id="test", session=session, capturer=capturer)
    actor.attach(stop)
    actor.send(args.prompt)

    # --- wait for idle with live indicator display ---
    print("\n=== WAITING FOR IDLE ===")
    last_indicator = ""
    result_content = None
    last_content = _capture_pane(session)
    last_indicator_val = capturer.indicator_line(last_content.splitlines())
    last_change = time.monotonic()
    deadline = time.monotonic() + 120

    while not stop.is_set() and time.monotonic() < deadline:
        time.sleep(_POLL_INTERVAL := 2.0)
        current = _capture_pane(session)
        indicator = capturer.indicator_line(current.splitlines())
        if indicator != last_indicator_val:
            print(f"  indicator: {indicator!r}")
            last_indicator_val = indicator
            last_content = current
            last_change = time.monotonic()
        elif time.monotonic() - last_change >= capturer.idle_seconds:
            result_content = current
            break

    if result_content is None:
        print("TIMEOUT — no response captured.")
        sys.exit(1)

    cur_lines = result_content.splitlines()
    cur_end = capturer.content_end(cur_lines)
    _show("PANE AFTER IDLE", cur_lines, cur_end)

    print("\n=== EXTRACTED RESPONSE ===")
    response = capturer.extract_response(snapshot_raw, result_content)
    print(response)
    print(f"\n(response length: {len(response)} chars)")


if __name__ == "__main__":
    main()
