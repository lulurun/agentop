#!/usr/bin/env python3
"""Developer tool: test the capturer against a live agent session.

Usage:
    python tools/test_capturer.py [PROMPT] [--session SESSION]

If --session is given, reuse an existing tmux session instead of starting a new one.
Otherwise, starts a fresh session via agentop.

Example:
    python tools/test_capturer.py "what is 2+2?"
    python tools/test_capturer.py "what is 2+2?" --session captest-1351624
    python tools/test_capturer.py "what is 2+2?" --tool codex
"""

from __future__ import annotations

import argparse
import os
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

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


def main() -> None:
    parser = argparse.ArgumentParser(description="Test Capturer against a live session.")
    parser.add_argument("prompt", nargs="?", default="Please write a short haiku about software testing.")
    parser.add_argument("--session", help="Reuse this tmux session (skip start)")
    parser.add_argument("--tool", default="claude", help="Agent tool to start (default: claude)")
    parser.add_argument("--cwd", default=os.path.expanduser("~/workspace/agentop"))
    args = parser.parse_args()

    session = args.session or _start_session(args.tool, args.cwd)
    stop = threading.Event()

    from agentop.agent_instance import AgentInstance
    from agentop.agentclis import get_agent
    instance = AgentInstance(get_agent(args.tool), session, "Tester")
    actor = instance.actor
    instance.attach(stop)

    print(f"\n=== SENDING PROMPT ===\n  {args.prompt!r}")
    actor.send(args.prompt)

    print(f"\n=== WAITING FOR IDLE (screen stable for {actor.idle_seconds}s) ===")
    t0 = time.monotonic()
    raw = actor.receive()
    elapsed = time.monotonic() - t0

    if raw is None:
        print("TIMEOUT — no response captured.")
        sys.exit(1)

    lines = raw.splitlines()
    print(f"  idle detected after {elapsed:.1f}s — scrollback: {len(lines)} lines")
    print(f"\n=== LAST 30 LINES OF SCROLLBACK ===")
    for line in lines[-30:]:
        print(f"  {line!r}")


if __name__ == "__main__":
    main()
