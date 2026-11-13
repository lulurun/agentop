"""The Dialogues plugin's command: `agentop dialogue start | list | stop | remove`.

Loaded by the CLI (agentop/plugin_cli.py) without the plugin's __init__.py. It
works with the service down: dialogues are folders, and the orchestrator is a
process of its own.
"""

from __future__ import annotations

import argparse
import os
import sys

from agentop import agentclis


def register_cli(cli) -> None:
    parser = cli.command(
        "dialogue",
        lambda args: run(args, parser),
        help="Run a structured two-agent dialogue",
        description=(
            "Start a dialogue between two AI agents on a shared topic.\n"
            "The orchestrator relays messages between them until they reach a conclusion."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="dialogue_command", metavar="<subcommand>")

    start = sub.add_parser("start", help="Start a new dialogue between two agents")
    start.add_argument("--brief", required=True, metavar="FILE", help="Markdown file describing the topic or goal")
    tools = agentclis.names()
    start.add_argument("--agent-a", dest="agent_a", default=agentclis.DEFAULT, choices=tools, help="Agent for session A (default: claude)")
    start.add_argument("--agent-b", dest="agent_b", default=agentclis.DEFAULT, choices=tools, help="Agent for session B (default: claude)")
    start.add_argument(
        "--max-turns", dest="max_turns", type=int, default=0, help="Maximum relay turns (default: the scenario's, or 20)"
    )
    start.add_argument("--scenario", default="", metavar="FILE", help="Scenario TOML file (default: implementation-requirements)")

    sub.add_parser("list", help="List all dialogues and their status")

    stop = sub.add_parser("stop", help="Stop a running dialogue orchestrator and close both agent sessions")
    stop.add_argument("id", help="Dialogue ID")

    remove = sub.add_parser("remove", help="Remove dialogue(s)")
    remove.add_argument("--id", metavar="ID", default="", help="Remove a specific dialogue by ID (any status)")
    remove.add_argument(
        "--status", choices=["stopped", "completed"], default="stopped", help="Remove all dialogues with this status (default: stopped)"
    )
    remove.add_argument("--yes", action="store_true", help="Skip confirmation prompt (for scripting)")


def run(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    command = getattr(args, "dialogue_command", None)
    if not command:
        parser.print_help()
        return
    {"start": start, "list": list_, "stop": stop, "remove": remove}[command](args)


def _fail(message: str) -> None:
    print(f"Error: {message}", file=sys.stderr)
    sys.exit(1)


def start(args) -> None:
    from . import ops

    brief_path = os.path.abspath(os.path.expanduser(args.brief))
    if not os.path.isfile(brief_path):
        _fail(f"brief file not found: {brief_path!r}")
    scenario_file = ""
    if args.scenario:
        scenario_file = os.path.abspath(os.path.expanduser(args.scenario))
        if not os.path.isfile(scenario_file):
            _fail(f"scenario file not found: {scenario_file!r}")

    print("Starting dialogue:")
    print(f"  Agent A: {args.agent_a}")
    print(f"  Agent B: {args.agent_b}")
    print(f"  Max turns: {args.max_turns}")

    result = ops.start_dialogue(
        agent_a=args.agent_a,
        agent_b=args.agent_b,
        max_turns=args.max_turns,
        brief_file=brief_path,
        scenario_file=scenario_file,
    )
    if not result.get("ok"):
        _fail(result["error"])
    did = result["id"]
    sa, sb = result["session_a"], result["session_b"]
    watch_cmd = (
        f"tmux new-window -n 'dia-{did}' "
        f"\"watch -n1 -t 'tmux capture-pane -t {sa} -p -S -40'\" \; "
        f"split-window -h "
        f"\"watch -n1 -t 'tmux capture-pane -t {sb} -p -S -40'\""
    )
    print(f"\nDialogue started: {did}")
    print(f"  Working dir: {result['cwd']}")
    print(f"  Session A: {sa}")
    print(f"  Session B: {sb}")
    print("\n  Watch side-by-side:")
    print(f"    {watch_cmd}")
    print(f"\n  Tail log:  tail -f {result['log_path']}")
    print(f"\n  Stop:  agentop dialogue stop {did}")


def list_(args) -> None:
    from . import ops

    dialogues = ops.list_dialogues()
    if not dialogues:
        print("No dialogues found.")
        return
    id_w, status_w, agent_w, sess_w = 40, 10, 10, 24
    print(
        f"{'ID':<{id_w}}  {'STATUS':<{status_w}}  {'AGENT A':<{agent_w}}  {'AGENT B':<{agent_w}}  "
        f"{'SESSION A':<{sess_w}}  {'SESSION B':<{sess_w}}"
    )
    print("-" * 100)
    for d in dialogues:
        sa = d["session_a"] + (" [open]" if d["session_a_alive"] else "")
        sb = d["session_b"] + (" [open]" if d["session_b_alive"] else "")
        print(
            f"{d['id']:<{id_w}}  {d['status']:<{status_w}}  "
            f"{d['agent_a']:<{agent_w}}  {d['agent_b']:<{agent_w}}  {sa:<{sess_w}}  {sb:<{sess_w}}"
        )


def stop(args) -> None:
    from . import ops

    result = ops.stop_dialogue(args.id)
    if not result.get("ok"):
        _fail(result["error"])
    print(f"Dialogue {args.id} stopped. Sessions closed.")


def remove(args) -> None:
    from agentop.tmux import Session

    from . import ops

    if args.id:
        result = ops.remove_dialogue(args.id)
        if not result.get("ok"):
            _fail(result["error"])
        print(f"Removed {args.id}.")
        return
    candidates = ops.list_dialogues_by_status(args.status)
    if not candidates:
        print(f"No dialogues with status '{args.status}'.")
        return
    print(f"Will remove ({args.status}):")
    for meta in candidates:
        open_note = " [sessions open]" if Session.has(meta.session_a) or Session.has(meta.session_b) else ""
        print(f"  {meta._dir().name}  {meta.agent_a} vs {meta.agent_b}{open_note}")
    if not args.yes:
        try:
            answer = input(f"\nRemove {len(candidates)} dialogue(s)? [y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\nAborted.")
            return
        if answer != "y":
            print("Aborted.")
            return
    for did in ops.remove_dialogues_by_status(args.status)["removed"]:
        print(f"Removed {did}")
