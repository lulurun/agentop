#!/usr/bin/env python3
"""Developer tool: test Actor send/receive over multiple turns against a live session.

Sends prompts that include the protocol rule (as the orchestrator would),
then calls actor.receive() to verify the response is extracted correctly.

Usage:
    python tools/test_actor.py [--tool TOOL] [--turns N] [--session SESSION]

Example:
    python tools/test_actor.py --tool antigravity --turns 4
    python tools/test_actor.py --session myses-1234 --turns 3
"""

from __future__ import annotations

import argparse
import os
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from agentop.dialogue.actor import Actor, RECEIVE_COMPLETE, RECEIVE_PARSE_FAILURE
from agentop.dialogue.orchestrator import _format_rule, _new_nonce

_NAME = "Tester"

QUESTIONS = [
    "Write a short poem about the ocean.",
    "Shorten that poem to just two lines.",
    "Translate those two lines into Spanish.",
    "Now translate them into Japanese.",
]


def _start_session(tool: str, cwd: str) -> str:
    from agentop import ops as agent_ops
    result = agent_ops.start(tool, cwd, short_name="actortest")
    if not result.get("ok"):
        print(f"ERROR starting session: {result.get('error')}")
        sys.exit(1)
    name = result["name"]
    print(f"Started session: {name}")
    print("Waiting 6s for agent to initialize...")
    time.sleep(6)
    return name


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tool", default="antigravity")
    parser.add_argument("--turns", type=int, default=4)
    parser.add_argument("--session", help="Reuse existing session")
    parser.add_argument("--cwd", default=os.path.expanduser("~/workspace/agentop"))
    args = parser.parse_args()

    session = args.session or _start_session(args.tool, args.cwd)
    stop = threading.Event()
    from agentop.agents import get_agent
    agent = get_agent(args.tool)
    if agent:
        actor = agent.make_actor(id="test", session=session, name=_NAME)
    else:
        actor = Actor(id="test", session=session, name=_NAME)
    actor.attach(stop)

    for i in range(1, args.turns + 1):
        question = QUESTIONS[(i - 1) % len(QUESTIONS)]
        nonce = _new_nonce()
        print(f"\n{'='*60}")
        print(f"TURN {i} — sending: {question!r}  nonce={nonce}")

        msg = question + "\n\n" + _format_rule(_NAME, i, nonce)
        actor.send(msg)

        print(f"Waiting for response (expecting turn:{i} nonce:{nonce})...")
        t0 = time.monotonic()
        result = actor.receive(nonce)
        elapsed = time.monotonic() - t0

        print(f"elapsed: {elapsed:.1f}s  |  internal turn counter: {actor._turn}")

        if result is None:
            print("ERROR: receive() returned None (timeout or stop)")
            break

        body, status = result
        print(f"status: {status!r}")

        if status == RECEIVE_PARSE_FAILURE:
            print("WARNING: parse_failure — no valid delimiter block found")
        elif status == RECEIVE_COMPLETE:
            print("COMPLETE signal")
            break
        else:
            print(f"OK — extracted ({len(body)} chars):\n{body[:400]}")

    print(f"\nDone. Session: {session}")
    print(f"  Attach: tmux attach-session -t {session}")
    print(f"  Kill:   tmux kill-session -t {session}")


if __name__ == "__main__":
    main()
