#!/usr/bin/env python3
"""agentop — manage AI agent sessions (Claude, Codex, Gemini, Antigravity) from the command line.

No dashboard server required. All commands work directly via tmux and
the local filesystem.

COMMANDS
--------
  list [--json]
    Show all running agent sessions detected on this machine.
    Columns: session, tool, status, pid, runtime, mem, tokens, cwd.
    --json outputs the full session objects as a JSON array, which
    includes token breakdown, git info, tmux details, and bridge URL.

  history [--json] [--tool <tool>] [--limit N]
    Show saved/historical sessions that can be resumed.
    Excludes sessions that are currently active.
    Columns: tool, title, last active, cwd.

  start <session_name> --tool <tool> [--cwd <path>]
    Launch a new agent session inside a managed tmux session.
    session_name must be less than 32 characters.
    The tmux session is named  {session_name}-{pid}  automatically.
    Supported tools: claude, codex, gemini, antigravity
    After starting, attach with:  tmux attach-session -t <session>

  stop <session>
    Gracefully stop a managed session: sends /exit to the agent,
    waits up to 3 s for it to exit, then kills the tmux session.
    Only sessions started by agentop can be stopped.

EXAMPLES
--------
  # List all sessions
  agentop list

  # Machine-readable output (for scripts / AI)
  agentop list --json
  agentop list --json | python3 -c "
      import sys, json
      for s in json.load(sys.stdin):
          print(s['name'], s.get('claude_status'), s.get('cwd'))
  "

  # Start a session (cwd defaults to home directory)
  agentop start myapp --tool claude
  agentop start myapp --tool claude --cwd ~/projects/myapp

  # Stop a session
  agentop stop myapp-12345

EXIT CODES
----------
  0  Success
  1  Error (session not found, tool failed, import error, etc.)

SESSION FIELDS (--json)
-----------------------
  name            Unique session identifier (also the tmux session name)
  tool            Agent type: claude | codex | gemini
  status          Process status (always "running" for live sessions)
  claude_status   Claude-specific status from session file: busy | idle
  pid             Agent process PID
  runtime_seconds Seconds since the process started
  memory_mb       Resident memory in MB
  token_usage     Object with input_tokens, output_tokens,
                  cache_creation_input_tokens, cache_read_input_tokens
                  (Claude only, read from ~/.claude/projects/)
  cwd             Working directory of the agent process
  ai_title        AI-generated conversation title (from the agent)
  managed         true if started by agentop; only managed sessions can
                  be stopped or sent prompts
  tmux            Object: {session, window, pane}
  git             Object: {repo_root, branch, dirty, latest_commit}
  bridge_url      claude.ai remote-control URL (Claude only)

NOTES
-----
  * Requires psutil and tmux.
  * The agentop package must be importable; run from the repo root or
    install the package.
  * Token usage is read by scanning ~/.claude/projects/<slug>/<id>.jsonl
    and deduplicating by message ID — reflects actual API calls made.
"""

import argparse
import json
import os
import sys


def _ops():
    try:
        from agentop import ops

        return ops
    except ImportError as exc:
        print(f"Error: cannot import agentop package: {exc}", file=sys.stderr)
        print("Run agentop from the repo root directory.", file=sys.stderr)
        sys.exit(1)


# ---------------------------------------------------------------------------
# Formatting helpers (presentation only)
# ---------------------------------------------------------------------------


def _fmt_runtime(sec):
    if not sec:
        return "—"
    sec = int(sec)
    if sec < 60:
        return f"{sec}s"
    if sec < 3600:
        return f"{sec // 60}m"
    if sec < 86400:
        return f"{sec // 3600}h {(sec % 3600) // 60}m"
    return f"{sec // 86400}d {(sec % 86400) // 3600}h"


def _fmt_tokens(usage):
    if not usage:
        return "—"
    total = sum(
        usage.get(k, 0)
        for k in (
            "input_tokens",
            "output_tokens",
            "cache_creation_input_tokens",
            "cache_read_input_tokens",
        )
    )
    if not total:
        return "—"
    if total < 1_000:
        return str(total)
    if total < 1_000_000:
        return f"{total / 1_000:.0f}K"
    return f"{total / 1_000_000:.1f}M"


def _shorten(path):
    if not path:
        return "—"
    home = os.path.expanduser("~")
    start = len(home)
    return "~" + path[start:] if path.startswith(home) else path


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def cmd_list(args):
    sessions = _ops().get_sessions()

    if args.json:
        print(json.dumps(sessions, indent=2, default=str))
        return

    if not sessions:
        print("No agent sessions detected.")
        return

    col = "{:<36} {:<8} {:<8} {:<8} {:<9} {:<8} {:<7} {}"
    print(col.format("SESSION", "TOOL", "STATUS", "PID", "RUNTIME", "MEM", "TOKENS", "CWD"))
    print("─" * 110)
    for s in sessions:
        status = s.get("claude_status") or s.get("status") or "—"
        pid = str(s.get("pid") or "—")
        runtime = _fmt_runtime(s.get("runtime_seconds"))
        mem = f"{s['memory_mb']}MB" if s.get("memory_mb") else "—"
        tokens = _fmt_tokens(s.get("token_usage"))
        cwd = _shorten(s.get("cwd"))
        name = s["name"] if s.get("managed") else f"[external] {s['name']}"
        print(col.format(name, s.get("tool", "?"), status, pid, runtime, mem, tokens, cwd))
        if s.get("ai_title"):
            print(f"  └─ {s['ai_title']}")


def cmd_start(args):
    cwd = os.path.expanduser(args.cwd)
    print(f"Starting {args.tool} session '{args.session_name}' in {cwd} …")
    result = _ops().start(args.tool, cwd, args.session_name)
    if not result.get("ok"):
        print(f"Error: {result.get('error', 'Failed to start session')}", file=sys.stderr)
        sys.exit(1)
    name = result["name"]
    print(f"Started:  {name}")
    if result.get("pid"):
        print(f"  PID:    {result['pid']}")
    print(f"  Tool:   {args.tool}")
    print(f"  CWD:    {cwd}")
    print(f"  Attach: tmux attach-session -t {name}")


def cmd_history(args):
    from datetime import datetime

    sessions = _ops().get_saved_sessions(tool=args.tool or None, limit=args.limit)

    if args.json:
        print(json.dumps(sessions, indent=2, default=str))
        return

    if not sessions:
        print("No saved sessions found.")
        return

    col = "{:<12} {:<46} {:<18} {}"
    print(col.format("TOOL", "TITLE", "LAST ACTIVE", "CWD"))
    print("─" * 110)
    for s in sessions:
        tool = s.get("tool", "?")
        raw_title = s.get("title") or ""
        title = raw_title[:45] + ("…" if len(raw_title) > 45 else "") if raw_title else "—"
        ts = s.get("last_active", 0)
        last = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M") if ts else "—"
        cwd = _shorten(s.get("cwd", ""))
        print(col.format(tool, title, last, cwd))


def cmd_stop(args):
    result = _ops().stop(args.name)
    if not result.get("ok"):
        print(f"Error: {result['error']}", file=sys.stderr)
        if "not found" in result["error"].lower():
            print("Run 'agentop list' to see available sessions.", file=sys.stderr)
        sys.exit(1)
    print(f"Stopping '{args.name}' …")
    if result.get("sent_exit"):
        print("  Sent /exit to agent.")
    if result.get("tmux_killed"):
        print("  Tmux session killed.")
    print("Done.")


# ---------------------------------------------------------------------------
# Dialogue commands
# ---------------------------------------------------------------------------

_DIALOGUE_TOOLS = ["claude", "codex", "gemini", "antigravity"]


def _dialogue_ops():
    try:
        from agentop.dialogue import ops
        return ops
    except ImportError as exc:
        print(f"Error: cannot import agentop.dialogue: {exc}", file=sys.stderr)
        sys.exit(1)


def cmd_dialogue_start(args):
    cwd_a = os.path.expanduser(args.cwd_a)
    cwd_b = os.path.expanduser(args.cwd_b)
    brief_path = os.path.expanduser(args.brief)
    if not os.path.isfile(brief_path):
        print(f"Error: brief file not found: {brief_path!r}", file=sys.stderr)
        sys.exit(1)
    with open(brief_path) as f:
        topic = f.read().strip()
    if not topic:
        print(f"Error: brief file is empty: {brief_path!r}", file=sys.stderr)
        sys.exit(1)
    first_line = next((l.strip().lstrip("#").strip() for l in topic.splitlines() if l.strip()), brief_path)
    print(f"Starting dialogue: {first_line!r}")
    print(f"  Agent A: {args.agent_a}  cwd: {cwd_a}")
    print(f"  Agent B: {args.agent_b}  cwd: {cwd_b}")
    print(f"  Max turns: {args.max_turns}")
    scenario_file = ""
    if args.scenario:
        scenario_path = os.path.expanduser(args.scenario)
        if not os.path.isfile(scenario_path):
            print(f"Error: scenario file not found: {scenario_path!r}", file=sys.stderr)
            sys.exit(1)
        scenario_file = os.path.abspath(scenario_path)

    result = _dialogue_ops().start_dialogue(
        topic=topic,
        agent_a=args.agent_a,
        agent_b=args.agent_b,
        cwd_a=cwd_a,
        cwd_b=cwd_b,
        max_turns=args.max_turns,
        brief_file=brief_path,
        scenario_file=scenario_file,
    )
    if not result.get("ok"):
        print(f"Error: {result['error']}", file=sys.stderr)
        sys.exit(1)
    did = result["id"]
    sa, sb = result["session_a"], result["session_b"]
    watch_cmd = (
        f"tmux new-window -n 'dia-{did}' "
        f"\"watch -n1 -t 'tmux capture-pane -t {sa} -p -S -40'\" \\; "
        f"split-window -h "
        f"\"watch -n1 -t 'tmux capture-pane -t {sb} -p -S -40'\""
    )
    from agentop.dialogue.model import DIALOGUES_DIR
    log_path = DIALOGUES_DIR / did / "dialogue.log"
    print(f"\nDialogue started: {did}")
    print(f"  Session A: {sa}")
    print(f"  Session B: {sb}")
    print(f"\n  Watch side-by-side:")
    print(f"    {watch_cmd}")
    print(f"\n  Tail log:  tail -f {log_path}")
    print(f"\n  Stop:  agentop dialogue stop {did}")


def cmd_dialogue_list(args):
    dialogues = _dialogue_ops().list_dialogues()
    if not dialogues:
        print("No dialogues found.")
        return
    id_w, status_w, sess_w = 10, 10, 24
    print(f"{'ID':<{id_w}}  {'STATUS':<{status_w}}  {'SESSION A':<{sess_w}}  {'SESSION B':<{sess_w}}  TOPIC")
    print("-" * 110)
    for d in dialogues:
        first_line = next((l.strip().lstrip("#").strip() for l in d["topic"].splitlines() if l.strip()), "")
        topic = first_line[:48] + ("…" if len(first_line) > 48 else "")
        sa = d["session_a"] + (" [open]" if d["session_a_alive"] else "")
        sb = d["session_b"] + (" [open]" if d["session_b_alive"] else "")
        print(f"{d['id']:<{id_w}}  {d['status']:<{status_w}}  {sa:<{sess_w}}  {sb:<{sess_w}}  {topic}")


def cmd_dialogue_stop(args):
    result = _dialogue_ops().stop_dialogue(args.id, close=getattr(args, "close", False))
    if not result.get("ok"):
        print(f"Error: {result['error']}", file=sys.stderr)
        sys.exit(1)
    msg = f"Dialogue {args.id} stopped."
    if getattr(args, "close", False):
        msg += " Sessions closed."
    print(msg)


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        prog="agentop",
        description="Manage AI agent sessions (Claude, Codex, Gemini) via tmux. No server required.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="command", metavar="<command>")

    # list
    p_list = sub.add_parser(
        "list",
        help="Show all running agent sessions",
        description=(
            "List all running agent sessions detected on this machine.\n"
            "Scans live processes and enriches with token usage and git info.\n"
            "Use --json for full machine-readable output."
        ),
    )
    p_list.add_argument("--json", action="store_true", help="Output as JSON array")

    # start
    def _session_name_type(val):
        if len(val) >= 32:
            raise argparse.ArgumentTypeError("session name must be less than 32 characters")
        return val

    p_start = sub.add_parser(
        "start",
        help="Launch a new agent session in tmux",
        description=(
            "Start a new agent session in a managed tmux session.\n"
            "The tmux session is named {session_name}-{pid} automatically.\n"
            "After starting, attach with:  tmux attach-session -t <session>"
        ),
    )
    p_start.add_argument("session_name", type=_session_name_type, help="Session name (max 31 chars)")
    p_start.add_argument("--tool", required=True, choices=["claude", "codex", "gemini", "antigravity"])
    p_start.add_argument("--cwd", default=os.path.expanduser("~"), help="Working directory (default: home directory)")

    # history
    p_history = sub.add_parser(
        "history",
        help="Show saved/historical sessions",
        description=(
            "List saved sessions from Claude, Codex, Gemini, and Antigravity.\n"
            "Currently active sessions are excluded.\n"
            "Use --json for full machine-readable output."
        ),
    )
    p_history.add_argument("--json", action="store_true", help="Output as JSON array")
    p_history.add_argument("--tool", choices=["claude", "codex", "gemini", "antigravity"], help="Filter by tool")
    p_history.add_argument("--limit", type=int, default=50, help="Maximum number of results (default: 50)")

    # stop
    p_stop = sub.add_parser(
        "stop",
        help="Gracefully stop a managed session",
        description=(
            "Send /exit to the agent, wait 3 s, then kill the tmux session.\n"
            "Only sessions started with 'agentop start' can be stopped."
        ),
    )
    p_stop.add_argument("name", help="Session name (from 'agentop list')")

    # dialogue
    p_dialogue = sub.add_parser(
        "dialogue",
        help="Run a structured two-agent dialogue",
        description=(
            "Start a dialogue between two AI agents on a shared topic.\n"
            "The orchestrator relays messages between them until they reach a conclusion."
        ),
    )
    dsub = p_dialogue.add_subparsers(dest="dialogue_command", metavar="<subcommand>")

    p_dstart = dsub.add_parser("start", help="Start a new dialogue between two agents")
    p_dstart.add_argument("--brief", required=True, metavar="FILE",
                          help="Path to a Markdown file describing the topic/goal for the dialogue")
    p_dstart.add_argument("--agent-a", dest="agent_a", default="claude", choices=_DIALOGUE_TOOLS,
                          help="Agent for session A (default: claude)")
    p_dstart.add_argument("--agent-b", dest="agent_b", default="claude", choices=_DIALOGUE_TOOLS,
                          help="Agent for session B (default: claude)")
    p_dstart.add_argument("--cwd-a", dest="cwd_a", default=os.path.expanduser("~"),
                          help="Working directory for agent A (default: home)")
    p_dstart.add_argument("--cwd-b", dest="cwd_b", default=os.path.expanduser("~"),
                          help="Working directory for agent B (default: home)")
    p_dstart.add_argument("--max-turns", dest="max_turns", type=int, default=20,
                          help="Maximum relay turns before stopping (default: 20)")
    p_dstart.add_argument("--scenario", default="", metavar="FILE",
                          help="Path to a scenario TOML file (default: built-in pm-sde)")

    dsub.add_parser("list", help="List all dialogues and their status")

    p_dstop = dsub.add_parser("stop", help="Stop a running dialogue orchestrator")
    p_dstop.add_argument("id", help="Dialogue ID")
    p_dstop.add_argument("--close", action="store_true", help="Also kill both agent tmux sessions")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(0)

    if args.command == "dialogue":
        if not args.dialogue_command:
            p_dialogue.print_help()
            sys.exit(0)
        {
            "list": cmd_dialogue_list,
            "start": cmd_dialogue_start,
            "stop": cmd_dialogue_stop,
        }[args.dialogue_command](args)
        return

    {
        "list": cmd_list,
        "history": cmd_history,
        "start": cmd_start,
        "stop": cmd_stop,
    }[
        args.command
    ](args)


if __name__ == "__main__":
    main()
