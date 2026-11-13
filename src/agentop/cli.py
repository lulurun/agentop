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

# No paths at import, so this is safe before `serve` moves the folder.
from agentop import agentclis


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
        if result.get("name"):
            print(f"The tmux session was kept: tmux attach-session -t {result['name']}", file=sys.stderr)
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
    if result.get("forced"):
        print("  The agent didn't exit in time, so its tmux session was killed.")
    elif result.get("tmux_killed"):
        print("  Tmux session killed.")
    print("Done.")


# ---------------------------------------------------------------------------
# Service commands
# ---------------------------------------------------------------------------


def cmd_serve(args):
    from agentop import paths

    # Only the service moves an older folder: it starts after the old service has
    # stopped. Before the imports, since some modules work out their paths as they load.
    paths.migrate()
    from agentop.service import app, config

    app.serve(
        config.load(host=args.host, port=args.port, name=args.name, tls_cert=args.tls_cert, tls_key=args.tls_key)
    )


def cmd_service_token(args):
    from agentop.service import identity

    if args.rotate:
        print(identity.rotate_token().token)
        print(
            "Rotated. The service uses it at once, and the old token stops working. "
            "Every saved connection needs the new one.",
            file=sys.stderr,
        )
    else:
        print(identity.load_or_create().token)


def cmd_open(args):
    import webbrowser

    from agentop.service import config, identity

    settings = config.load()
    scheme = "https" if settings.tls else "http"
    host = "127.0.0.1" if settings.host in ("0.0.0.0", "::") else settings.host
    url = f"{scheme}://{host}:{settings.port}/app/#token={identity.load_or_create().token}"
    if not webbrowser.open(url):
        print(url)


def _service_health(settings):
    import json
    import urllib.error
    import urllib.request

    scheme = "https" if settings.tls else "http"
    host = "127.0.0.1" if settings.host in ("0.0.0.0", "::") else settings.host
    try:
        with urllib.request.urlopen(f"{scheme}://{host}:{settings.port}/api/v2/health", timeout=3) as response:
            return json.load(response)
    except (OSError, ValueError):
        return None


def _service_api(settings, method, path):
    """Ask the service on this machine, with its own token. Returns None if it didn't answer."""
    import json
    import ssl
    import urllib.error
    import urllib.request

    from agentop.service import identity

    scheme = "https" if settings.tls else "http"
    host = "127.0.0.1" if settings.host in ("0.0.0.0", "::") else settings.host
    request = urllib.request.Request(
        f"{scheme}://{host}:{settings.port}/api/v2{path}",
        method=method,
        headers={"Authorization": f"Bearer {identity.load_or_create().token}"},
    )
    # Its own certificate may be self-signed, and this is the service on this machine.
    context = ssl._create_unverified_context() if settings.tls else None
    try:
        with urllib.request.urlopen(request, timeout=10, context=context) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.load(exc)
        except ValueError:
            return exc.code, {}
    except (OSError, ValueError):
        return None


_MANAGERS = {"systemd": "Run by its systemd unit.", "self": "Started with agentop serve."}
_NOT_ANSWERING = "Start it with agentop serve, or with the system service that runs it."


def _admin(settings, method, path):
    """Ask the running service. Exits with an error if it doesn't answer or refuses."""
    answer = _service_api(settings, method, path)
    if answer is None:
        print(f"Error: nothing is answering. {_NOT_ANSWERING}", file=sys.stderr)
        sys.exit(1)
    status, body = answer
    if status >= 400:
        print(f"Error: {body.get('detail', status)}", file=sys.stderr)
        sys.exit(1)
    return body


def cmd_service_status(args):
    from agentop.service import config

    settings = config.load()
    answer = _service_api(settings, "GET", "/service")
    if answer is None:
        print(f"Nothing is answering on {settings.host}:{settings.port}. {_NOT_ANSWERING}")
        return
    status, info = answer
    if status >= 400:
        print(f"Something answered on {settings.host}:{settings.port}, but refused: {info.get('detail', status)}")
        return
    print(f"{info['name']}: running on {settings.host}:{settings.port}, version {info['version']}")
    print(_MANAGERS.get(info["manager"], "It can't stop, restart, or update itself."))


def cmd_service_control(args):
    from agentop.service import config

    # The service knows who runs it, systemd or agentop serve, and acts on itself.
    _admin(config.load(), "POST", f"/admin/{args.service_command}")


def cmd_service_update(args):
    import time

    from agentop.service import config, update

    # The service starts it, since only the service knows how to restart itself.
    previous = update.read_status()
    _admin(config.load(), "POST", "/admin/update")

    shown = None
    while True:
        time.sleep(1)
        run = update.read_status()
        if not run or run == previous:
            continue
        if run["state"] == "running":
            if run["step"] != shown:
                print(f"{run['step']}...")
                shown = run["step"]
            continue
        if run["state"] == "done":
            print(run["message"] or f"Updated to {run['to_commit']}.")
            return
        print(f"Error: {run['message']}", file=sys.stderr)
        sys.exit(1)


def cmd_service_connect_info(args):
    from agentop.service import config, identity

    settings = config.load()
    scheme = "https" if settings.tls else "http"
    address = args.url.rstrip("/") if args.url else f"{scheme}://{args.host or settings.host}:{settings.port}"
    # The token is in the fragment, which a browser never sends to a server.
    print(f"{address}#token={identity.load_or_create().token}")


# ---------------------------------------------------------------------------
# Dialogue commands
# ---------------------------------------------------------------------------

_DIALOGUE_TOOLS = agentclis.names()


def _dialogue_ops():
    try:
        from agentop.dialogue import ops

        return ops
    except ImportError as exc:
        print(f"Error: cannot import agentop.dialogue: {exc}", file=sys.stderr)
        sys.exit(1)


def cmd_dialogue_start(args):
    brief_path = os.path.abspath(os.path.expanduser(args.brief))
    if not os.path.isfile(brief_path):
        print(f"Error: brief file not found: {brief_path!r}", file=sys.stderr)
        sys.exit(1)
    scenario_file = ""
    if args.scenario:
        scenario_path = os.path.abspath(os.path.expanduser(args.scenario))
        if not os.path.isfile(scenario_path):
            print(f"Error: scenario file not found: {scenario_path!r}", file=sys.stderr)
            sys.exit(1)
        scenario_file = scenario_path

    print("Starting dialogue:")
    print(f"  Agent A: {args.agent_a}")
    print(f"  Agent B: {args.agent_b}")
    print(f"  Max turns: {args.max_turns}")

    result = _dialogue_ops().start_dialogue(
        agent_a=args.agent_a,
        agent_b=args.agent_b,
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
    log_path = result["log_path"]
    print(f"\nDialogue started: {did}")
    print(f"  Working dir: {result['cwd']}")
    print(f"  Session A: {sa}")
    print(f"  Session B: {sb}")
    print("\n  Watch side-by-side:")
    print(f"    {watch_cmd}")
    print(f"\n  Tail log:  tail -f {log_path}")
    print(f"\n  Stop:  agentop dialogue stop {did}")


def cmd_dialogue_list(args):
    dialogues = _dialogue_ops().list_dialogues()
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


def cmd_dialogue_stop(args):
    result = _dialogue_ops().stop_dialogue(args.id)
    if not result.get("ok"):
        print(f"Error: {result['error']}", file=sys.stderr)
        sys.exit(1)
    print(f"Dialogue {args.id} stopped. Sessions closed.")


def cmd_dialogue_remove(args):
    ops = _dialogue_ops()
    if args.id:
        result = ops.remove_dialogue(args.id)
        if not result.get("ok"):
            print(f"Error: {result['error']}", file=sys.stderr)
            sys.exit(1)
        print(f"Removed {args.id}.")
    else:
        status = args.status
        candidates = ops.list_dialogues_by_status(status)
        if not candidates:
            print(f"No dialogues with status '{status}'.")
            return
        from agentop.tmux import Session
        print(f"Will remove ({status}):")
        for meta in candidates:
            open_note = " [sessions open]" if Session.has(meta.session_a) or Session.has(meta.session_b) else ""
            print(f"  {meta._dir().name}  {meta.agent_a} vs {meta.agent_b}{open_note}")
        if not getattr(args, "yes", False):
            try:
                answer = input(f"\nRemove {len(candidates)} dialogue(s)? [y/N] ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                print("\nAborted.")
                return
            if answer != "y":
                print("Aborted.")
                return
        result = ops.remove_dialogues_by_status(status)
        for did in result["removed"]:
            print(f"Removed {did}")


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
        if not _ops().is_valid_short_name(val):
            raise argparse.ArgumentTypeError(
                "session name must be 1-32 letters, digits, '-' or '_', starting with a letter or digit"
            )
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
    p_start.add_argument("--tool", required=True, choices=agentclis.names())
    p_start.add_argument("--cwd", default=os.path.expanduser("~"), help="Working directory (default: home directory)")

    # history
    p_history = sub.add_parser(
        "history",
        help="Show saved/historical sessions",
        description=(
            "List saved sessions from Claude, Codex, and Antigravity.\n"
            "Currently active sessions are excluded.\n"
            "Use --json for full machine-readable output."
        ),
    )
    p_history.add_argument("--json", action="store_true", help="Output as JSON array")
    p_history.add_argument("--tool", choices=agentclis.names(), help="Filter by tool")
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

    # serve
    p_serve = sub.add_parser("serve", help="Run the service in the foreground")
    p_serve.add_argument("--host", help="Address to listen on (default: 127.0.0.1)")
    p_serve.add_argument("--port", type=int, help="Port to listen on (default: 9775)")
    p_serve.add_argument("--name", help="Name shown to clients (default: hostname)")
    p_serve.add_argument("--tls-cert", help="TLS certificate file")
    p_serve.add_argument("--tls-key", help="TLS private key file")

    # open
    sub.add_parser("open", help="Open the web app in a browser, signed in")

    # service
    p_service = sub.add_parser("service", help="Control the service, and get its access details")
    service_sub = p_service.add_subparsers(dest="service_command", metavar="<subcommand>")
    service_sub.add_parser("status", help="Show whether the service is running")
    service_sub.add_parser("stop", help="Stop the service. Sessions keep running")
    service_sub.add_parser("restart", help="Restart the service. Sessions keep running")
    service_sub.add_parser("update", help="Update from git, and go back if the new version fails")
    p_token = service_sub.add_parser("token", help="Print the access token")
    p_token.add_argument("--rotate", action="store_true", help="Replace it; every saved connection then needs it")
    p_connect = service_sub.add_parser("connect-info", help="Print a string to paste into Add Service in the app")
    p_connect.add_argument("--host", help="Address other machines use to reach this service")
    p_connect.add_argument("--url", help="Full address when behind a proxy, e.g. http://203.0.113.9:7700/agentop")

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
    p_dstart.add_argument(
        "--brief",
        required=True,
        metavar="FILE",
        help="Path to a Markdown file describing the topic/goal for the dialogue",
    )
    p_dstart.add_argument(
        "--agent-a",
        dest="agent_a",
        default=agentclis.DEFAULT,
        choices=_DIALOGUE_TOOLS,
        help="Agent for session A (default: claude)",
    )
    p_dstart.add_argument(
        "--agent-b",
        dest="agent_b",
        default=agentclis.DEFAULT,
        choices=_DIALOGUE_TOOLS,
        help="Agent for session B (default: claude)",
    )
    p_dstart.add_argument(
        "--max-turns",
        dest="max_turns",
        type=int,
        default=0,
        help="Maximum relay turns (default: use scenario setting, fallback 20)",
    )
    p_dstart.add_argument(
        "--scenario", default="", metavar="FILE", help="Path to a scenario TOML file (default: built-in pm-sde)"
    )

    dsub.add_parser("list", help="List all dialogues and their status")

    p_dstop = dsub.add_parser("stop", help="Stop a running dialogue orchestrator and close both agent sessions")
    p_dstop.add_argument("id", help="Dialogue ID")

    p_dremove = dsub.add_parser("remove", help="Remove dialogue(s)")
    p_dremove.add_argument("--id", metavar="ID", default="", help="Remove a specific dialogue by ID (any status)")
    p_dremove.add_argument(
        "--status",
        choices=["stopped", "completed"],
        default="stopped",
        help="Remove all dialogues with this status (default: stopped)",
    )
    p_dremove.add_argument("--yes", action="store_true", help="Skip confirmation prompt (for scripting)")

    # Last, so a plugin can't take a core command's name.
    from agentop import plugin_cli

    plugin_commands = plugin_cli.add_commands(sub, reserved=set(sub.choices))

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(0)

    if args.command in plugin_commands:
        plugin_commands[args.command](args)
        return

    if args.command == "dialogue":
        if not args.dialogue_command:
            p_dialogue.print_help()
            sys.exit(0)
        {
            "list": cmd_dialogue_list,
            "start": cmd_dialogue_start,
            "stop": cmd_dialogue_stop,
            "remove": cmd_dialogue_remove,
        }[args.dialogue_command](args)
        return

    if args.command == "service":
        if not args.service_command:
            p_service.print_help()
            sys.exit(0)
        {
            "status": cmd_service_status,
            "stop": cmd_service_control,
            "restart": cmd_service_control,
            "update": cmd_service_update,
            "token": cmd_service_token,
            "connect-info": cmd_service_connect_info,
        }[args.service_command](args)
        return

    {
        "list": cmd_list,
        "history": cmd_history,
        "start": cmd_start,
        "stop": cmd_stop,
        "serve": cmd_serve,
        "open": cmd_open,
    }[
        args.command
    ](args)


if __name__ == "__main__":
    main()
