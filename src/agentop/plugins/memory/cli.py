"""The Memory plugin's command: `agentop recall`, search past conversations.

Loaded by the CLI (agentop/plugin_cli.py) without the plugin's __init__.py, and
it reads the plugin's database directly, so it works with the service down.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import time
from datetime import datetime
from pathlib import Path

from agentop import agentclis

from .messages import Messages
from .summaries import Summaries


def register_cli(cli) -> None:
    parser = cli.command(
        "recall",
        lambda args: recall(args, cli.data_dir),
        help="Search past conversations (prompts and replies) on this machine",
        description=(
            "Search the Memory plugin's record of every conversation with claude, codex and\n"
            "antigravity on this machine. A message matches when it holds every word; put a\n"
            "phrase in quotes. Words match anywhere, so part of a name or a URL works.\n\n"
            "  agentop recall atama link\n"
            "  agentop recall --session <session> --around <#id>"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("query", nargs="*", help="Words to search for")
    parser.add_argument("--tool", choices=agentclis.names(), help="Only this tool's sessions")
    parser.add_argument("--project", help="Only sessions in this working directory")
    parser.add_argument("--since", help="Only messages since 30d, 2w, 12h, or a date")
    parser.add_argument("--limit", type=int, default=10, help="Most results to show (default 10)")
    parser.add_argument("--session", help="Read this session's conversation instead of searching")
    parser.add_argument("--around", type=int, help="With --session: only the messages near this #id")
    parser.add_argument("--radius", type=int, default=6, help="With --around: messages on each side (default 6)")
    parser.add_argument("--json", action="store_true", help="Print JSON")


def _shorten(path: str) -> str:
    if not path:
        return "—"
    home = os.path.expanduser("~")
    return "~" + path[len(home) :] if path.startswith(home) else path


def _since(value: str) -> float:
    """`30d`, `2w`, `12h`, or a date (`2026-10-01`), as seconds since the epoch."""
    found = re.fullmatch(r"(\d+)([hdw])", value.strip())
    if found:
        hours = {"h": 1, "d": 24, "w": 24 * 7}[found.group(2)] * int(found.group(1))
        return time.time() - hours * 3600
    try:
        return datetime.fromisoformat(value).timestamp()
    except ValueError:
        raise SystemExit(f"Error: --since takes 30d, 2w, 12h, or a date like 2026-10-01, not {value!r}") from None


def recall(args: argparse.Namespace, folder: Path) -> None:
    """Search the record of past conversations. Reads the database directly, no service needed."""
    table = Messages(folder / "messages.sqlite", folder / "archive")
    summaries = Summaries(table)
    if not table.path.exists():
        print("Nothing recorded yet. The Memory plugin builds its record once the service has run with it enabled.")
        return

    def when(at):
        return datetime.fromtimestamp(at).strftime("%Y-%m-%d %H:%M") if at else "—"

    if args.session:
        rows = table.conversation(args.tool or None, args.session, args.around, args.radius)
        summary = summaries.get(args.tool or None, args.session)
        if args.json:
            print(json.dumps({"summary": summary, "messages": rows}, indent=2, ensure_ascii=False))
            return
        if not rows:
            print(f"No messages recorded for session {args.session}.")
            return
        if summary and args.around is None:
            print(f"=== {summary['title']}")
            print(summary["summary"])
            print()
        for row in rows:
            who = "You" if row["role"] == "user" else "Agent"
            mark = "  <<" if row["id"] == args.around else ""
            print(f"--- #{row['id']}  {when(row['at'])}  {who}{mark}")
            print(row["text"])
            print()
        return

    query = " ".join(args.query)
    if not query.strip():
        raise SystemExit("Error: give words to search for, or --session <id> to read a conversation")
    since = _since(args.since) if args.since else None
    hits = table.search(query, args.limit, args.tool or None, args.project or None, since)
    sessions = summaries.search(query, 5, args.tool or None, args.project or None, since)
    if args.json:
        print(json.dumps({"sessions": sessions, "messages": hits}, indent=2, ensure_ascii=False))
        return
    if sessions:
        print("Sessions about this, from their summaries:")
        print()
        for s in sessions:
            print(f"{when(s['last_at'])}  {s['tool']}  {_shorten(s['project'] or '')}  session {s['session']}")
            print(f"  {s['title']}")
            print(f"  {s['snippet']}")
            if s["matched"]:
                print(f"  Keywords: {', '.join(s['matched'])}")
            print()
        print("Read one: agentop recall --session <session>")
        print()
    if not hits:
        print(f"No messages with all of: {query}")
        return
    if sessions:
        print("Messages:")
        print()
    for hit in hits:
        print(
            f"#{hit['id']}  {when(hit['at'])}  {hit['tool']}  {_shorten(hit['project'] or '')}  session {hit['session']}"
        )
        if hit["prompt"]:
            print(f"  You asked: {hit['prompt']}")
        print(f"  {'You' if hit['role'] == 'user' else 'Agent'}: {hit['snippet']}")
        print()
    print("Read around one: agentop recall --session <session> --around <#id>")
