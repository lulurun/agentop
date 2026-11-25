#!/usr/bin/env python3
"""agentop: the command line. Commands are described in docs/0_08_cli.md and by `agentop <command> --help`."""

import argparse
import json
import os
import sys

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
        name = f"{s['project']}/{s['display_name']}" if s.get("managed") else f"[external] {s['name']}"
        print(col.format(name, s.get("tool", "?"), status, pid, runtime, mem, tokens, cwd))
        if s.get("ai_title"):
            print(f"  └─ {s['ai_title']}")


def _own_project(args) -> str:
    """--project, or else the project of the session this runs in, or else the default one."""
    if args.project:
        return args.project
    here = _ops().current_session()
    return here[1] if here else "default"


def _tracked_coordinator(project: str) -> bool:
    """Whether this runs in a Projects project's Coordinator, which delegates through `agentop coordinator task start`."""
    from agentop import projects

    here = _ops().current_session()
    found = projects.get(project)
    return bool(here and here[1] == project and here[2] == projects.COORDINATOR and found and found.get("owner") == "projects")


def cmd_start(args):
    cwd = os.path.expanduser(args.cwd)
    project = _own_project(args)
    if _tracked_coordinator(project) and not args.untracked:
        print(
            "Error: a Coordinator delegates with agentop coordinator task start, which records the goal, criteria and "
            "artifact. Add --untracked only for a session that is not a task (it shows in agentop coordinator review)",
            file=sys.stderr,
        )
        sys.exit(1)
    if args.prompt == "-":
        args.prompt = sys.stdin.read()
    params = {}
    if args.model:
        try:
            params["model"] = agentclis.get_agent(args.tool).model_id(args.model)
        except ValueError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            sys.exit(1)
    print(f"Starting {args.tool} session '{args.session_name}' in {cwd}, project {project} …")
    from agentop.results import OpsError, StartFailed

    try:
        taken = [
            s for s in _ops().get_sessions(with_meta=False)
            if s.get("project") == project and s.get("display_name", "").lower() == args.session_name.lower()
        ]
        if taken:
            raise OpsError(f"A session called {args.session_name} is already in {project}. Pick another name")
        if args.no_remote_control:
            params["remote_control"] = False
        result = _ops().start(args.tool, cwd, args.session_name, params, project=project)
    except OpsError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        if isinstance(exc, StartFailed) and exc.name:
            print(f"The tmux session was kept: tmux attach-session -t {exc.name}", file=sys.stderr)
        sys.exit(1)
    name = result.name
    if args.untracked and _tracked_coordinator(project):
        from agentop.plugins.projects import pursuit

        pursuit.log(project, "start.untracked", session=args.session_name)
    if args.prompt:
        print("Typing in the first prompt once it is up …")
        try:
            _ops().first_prompt(name, args.prompt)
        except OpsError as exc:
            print(f"Error: {exc}", file=sys.stderr)
    print(f"Started:  {name}")
    print(f"  PID:    {result.pid}")
    print(f"  Tool:   {args.tool}")
    if "model" in params:
        print(f"  Model:  {params['model']}")
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
    from agentop.results import OpsError

    try:
        found = _ops().find_session(args.name, project=_own_project(args))
        result = _ops().stop(found["name"])
    except OpsError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        if "not found" in str(exc).lower():
            print("Run 'agentop list' to see available sessions.", file=sys.stderr)
        sys.exit(1)
    print(f"Stopping '{args.name}' …")
    if result.sent_exit:
        print("  Sent /exit to agent.")
    if result.forced:
        print("  The agent didn't exit in time, so its tmux session was killed.")
    elif result.tmux_killed:
        print("  Tmux session killed.")
    print("Done.")


def deliver(target: str, text: str, project: str):
    """Send a prompt as `agentop send` does, through the service when it runs. Raises OpsError."""
    from agentop.results import OpsError, Sent
    from agentop.service import config

    # Through the service, where it queues with the Input bar's prompts; without one, nothing else is typing.
    answer = _service_api(config.load(), "POST", "/send", {"target": target, "text": text, "project": project}, timeout=60)
    if answer is None:
        return _ops().send(target, text, project=project)
    if answer[0] >= 400:
        detail = answer[1].get("detail")
        if isinstance(detail, dict):
            raise OpsError(detail.get("message", ""), matches=detail.get("matches") or [])
        raise OpsError(f"the service refused: {detail or answer[0]}")
    return Sent(answer[1]["name"], answer[1]["tmux"])


def cmd_send(args):
    text = sys.stdin.read() if args.prompt in (None, "-") else args.prompt
    from agentop.tmux import Session

    here = _ops().current_session()
    sender = f"{here[1]}/{here[2]}" if here else Session.current()
    if sender and not args.no_header:
        text = f"[agentop send from {sender}]\n{text.strip()}"
    from agentop import projects
    from agentop.results import OpsError

    project = _own_project(args)
    found = projects.get(project) if project and args.target == projects.COORDINATOR else None
    if found and found.get("owner") == "projects":
        from agentop.plugins.projects import pursuit

        text = pursuit.wake_note(project) + text
    try:
        sent = deliver(args.target, text, project)
    except OpsError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        if not exc.matches:
            print("Run 'agentop list' to see the active sessions.", file=sys.stderr)
        sys.exit(1)
    print(f"Sent to {sent.name}.")
    print(f"  Watch:  tmux attach-session -t {sent.tmux}")


def cmd_project_list(args):
    from agentop import projects

    found = projects.list_projects()
    if args.json:
        print(json.dumps(found, indent=2))
        return
    live = _ops().get_sessions(with_meta=False)
    col = "{:<40} {:<10} {:<9} {}"
    print(col.format("PROJECT", "OWNER", "SESSIONS", "FOLDER"))
    for p in found:
        count = sum(1 for s in live if s.get("project") == p["name"])
        print(col.format(p["name"], p.get("owner") or "—", count, _shorten(p["folder"])))


def _projects_plugin_on() -> bool:
    from agentop import paths
    from agentop.plugin_manifest import ManifestError, is_enabled, plugin_folders, plugin_roots, read_flags, read_manifest

    for folder in plugin_folders(plugin_roots()):
        try:
            manifest = read_manifest(folder)
        except ManifestError:
            continue
        if manifest.id == "projects":
            return is_enabled(manifest, read_flags(paths.state_dir() / "plugins.json") or {})
    return False


def cmd_project_create(args):
    from agentop import agentclis, coordinator, projects
    from agentop.plugins.projects.texts import INSTRUCTIONS, WELCOME
    from agentop.results import OpsError

    if not _projects_plugin_on():
        print("Error: the Projects plugin is off. Turn it on in Status → Plugins", file=sys.stderr)
        sys.exit(1)
    from agentop.plugins.projects import track

    kept = projects.kept(args.name)
    try:
        found = projects.create(args.name, "projects", WELCOME, INSTRUCTIONS)
    except projects.ProjectError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    if kept:
        reopened = track.reopen(args.name, "the owner, with agentop project create")
        print(f"Project {args.name}, loaded from its kept folder{' and marked reopened' if reopened else ''}: {found['folder']}")
    else:
        print(f"Project {args.name}: {found['folder']}")
    print("Starting its Coordinator …")
    try:
        name = coordinator.start_now(found, [a.name for a in agentclis.installed(agentclis.AGENTS)])
    except OpsError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    if name is None:
        print("Error: no agent tool is installed", file=sys.stderr)
        sys.exit(1)
    print(f"  Attach: tmux attach-session -t {name}")


def cmd_project_close(args):
    from agentop import projects
    from agentop.plugins.projects import pursuit, track

    found = projects.get(args.name)
    if found is None or found.get("owner") != "projects":
        print(f"Error: no project called {args.name} (only the Projects plugin's are closed here)", file=sys.stderr)
        sys.exit(1)
    here = _ops().current_session()
    if not here or here[1] != args.name or here[2] != projects.COORDINATOR:
        print(f"Error: only {args.name}'s Coordinator closes it: ask it there (Close in the app does)", file=sys.stderr)
        sys.exit(1)
    tasks = pursuit.open_tasks(args.name)
    if tasks:
        print(f"Error: tasks are open: {track.task_names(tasks)}. Wait for their reports and accept or fail each "
              "as usual, then close", file=sys.stderr)
        sys.exit(1)
    warnings = pursuit.problems(args.name) if track.exists(args.name) else []
    if warnings:
        print("Error: the track is not up to date; fix these first (agentop coordinator status shows them):", file=sys.stderr)
        for w in warnings:
            print(f"  - {w}", file=sys.stderr)
        sys.exit(1)
    marked = track.mark(args.name, "closed", f"`{here[2]}` of {here[1]}", args.note or "")
    # Read before the entry goes: a closed project's sessions are no longer listed.
    live = _ops().get_sessions(with_meta=False)
    projects.close(args.name)
    print(f"Closed {args.name}: marked in {marked.relative_to(projects.folder(args.name))}, and gone from agentop project list.")
    print(f"Its folder, files and other sessions are kept as they are: {found['folder']}")
    print(f"  agentop project create {args.name} loads it again.")
    # Last, and from a process of its own: it ends the session this command runs in.
    print("Closing this session now.", flush=True)
    _ops().stop_detached(here[0], live)


def cmd_project_show(args):
    from agentop import projects, registry

    name = args.name or (_ops().current_session() or (None, "default"))[1]
    found = projects.get(name)
    if found is None:
        print(f"Error: no project called {name}", file=sys.stderr)
        sys.exit(1)
    live = {s["name"]: s for s in _ops().get_sessions(with_meta=False)}
    records = {k: v for k, v in registry.load()["sessions"].items() if v.get("project") == name}
    sessions = [
        {
            "name": r.get("display_name") or k,
            "tmux": k,
            "tool": r.get("tool"),
            "cwd": r.get("cwd") or "",
            "running": k in live,
            "readonly": bool(r.get("readonly")),
        }
        for k, r in records.items()
        if k in live or not r.get("ended_at")
    ]
    if args.json:
        print(json.dumps({**found, "sessions": sessions}, indent=2))
        return
    print(f"Project: {name} ({found.get('owner')})")
    print(f"Folder:  {found['folder']}")
    for s in sessions:
        state = "running" if s["running"] else "stopped"
        extra = ", read-only" if s["readonly"] else ""
        print(f"  {s['name']:<24} {s['tool'] or '?':<12} {state}{extra}  {_shorten(s['cwd'])}")


# ---------------------------------------------------------------------------
# Service commands
# ---------------------------------------------------------------------------


def cmd_serve(args):
    from agentop.service import app, config

    app.serve(
        config.load(
            host=args.host,
            port=args.port,
            name=args.name,
            tls_cert=args.tls_cert,
            tls_key=args.tls_key,
            update_from=args.update_from,
        )
    )


def cmd_service_token(args):
    from agentop.service import auth, identity

    if args.rotate:
        print(identity.rotate_token().token)
        if args.revoke_clients:
            count = auth.ClientStore(auth.clients_path()).revoke_all()
            print(f"Rotated, and signed out {count} client(s). Each needs the new token to sign in again.", file=sys.stderr)
        else:
            print(
                "Rotated. The old token can no longer add a client. Clients already signed in stay signed in; "
                "add --revoke-clients to sign them out too.",
                file=sys.stderr,
            )
    else:
        print(identity.load_or_create().token)


def _ago(seconds):
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h"
    return f"{seconds // 86400}d"


def cmd_service_clients(args):
    import time

    from agentop.service import auth

    store = auth.ClientStore(auth.clients_path())
    if args.revoke_all:
        print(f"Signed out {store.revoke_all()} client(s).")
        return
    if args.revoke:
        if not store.revoke(args.revoke):
            print(f"Error: no client {args.revoke}", file=sys.stderr)
            sys.exit(1)
        print(f"Signed out {args.revoke}. Its open terminals close within seconds.")
        return
    clients = store.list()
    if not clients:
        print("No clients are signed in.")
        return
    now = time.time()
    col = "{:<14} {:<32} {:>8} {:>10} {:>10}"
    print(col.format("ID", "NAME", "ADDED", "LAST SEEN", "EXPIRES IN"))
    for c in clients:
        print(col.format(c.id, c.name[:32], _ago(now - c.created_at), _ago(now - c.last_seen), _ago(c.expires_at - now)))


def cmd_open(args):
    import webbrowser

    from agentop.service import config, identity

    settings = config.load()
    scheme = "https" if settings.tls else "http"
    host = "127.0.0.1" if settings.host in ("0.0.0.0", "::") else settings.host
    url = f"{scheme}://{host}:{settings.port}/#token={identity.load_or_create().token}"
    if not webbrowser.open(url):
        print(url)


# The CLI signs in for one command, so its client expires almost at once.
CLI_CLIENT_TTL_SECONDS = 60


def _service_api(settings, method, path, body=None, timeout=10):
    """Ask the service on this machine. Returns None if it didn't answer, (503, …) if it stopped part way.

    The service token only adds a client, so the CLI adds a short-lived one
    with it first, and makes the request with that client's token.
    """
    import json
    import ssl
    import urllib.error
    import urllib.request

    from agentop.service import identity

    scheme = "https" if settings.tls else "http"
    host = "127.0.0.1" if settings.host in ("0.0.0.0", "::") else settings.host
    base = f"{scheme}://{host}:{settings.port}/api/v2"
    # Its own certificate may be self-signed, and this is the service on this machine.
    context = ssl._create_unverified_context() if settings.tls else None

    def call(method, path, token, body=None):
        data = None if body is None else json.dumps(body).encode()
        headers = {"Authorization": f"Bearer {token}"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(f"{base}{path}", data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as exc:
            try:
                return exc.code, json.load(exc)
            except ValueError:
                return exc.code, {}
        except (OSError, ValueError):
            return None

    added = call(
        "POST",
        "/auth/clients",
        identity.load_or_create().token,
        {"name": "agentop CLI", "ttl_seconds": CLI_CLIENT_TTL_SECONDS},
    )
    if added is None or added[0] >= 400:
        return added
    # It answered once, so the request may have been acted on: never None, which a caller may retry without it.
    return call(method, path, added[1]["token"], body) or (503, {"detail": "The service stopped answering"})


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
    # The package version is stale in a checkout (an editable install keeps the one it was installed at).
    commit = info.get("commit")
    version = f"{info.get('ref') or info['version']} ({commit})" if commit else info["version"]
    print(f"{info['name']}: running on {settings.host}:{settings.port}, version {version}")
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
# Argument parsing
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        prog="agentop",
        description="Manage AI agent sessions (Claude, Codex, Antigravity) in tmux.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Run 'agentop <command> --help' for a command's options. Exits 1 on an error.",
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
    p_start.add_argument("--project", help="The project to start it in (default: this session's, else default)")
    p_start.add_argument("--model", help='Its model by name or ID (default: the tool\'s own), such as "Opus 5.5" or "GPT-6.2-Sol"')
    p_start.add_argument("--prompt", help="Its first prompt, typed in once it is up, after a line about its project; '-' reads it from stdin")
    p_start.add_argument("--untracked", action="store_true", help="From a Projects Coordinator: start a session that is not a task")
    p_start.add_argument(
        "--no-remote-control",
        action="store_true",
        help="Do not expose a Claude session through claude.ai remote control",
    )

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
    p_stop.add_argument("name", help="Session name in this project, or <project>/<name>")
    p_stop.add_argument("--project", help="The project to look the name up in (default: this session's, else default)")

    # send
    p_send = sub.add_parser(
        "send",
        help="Type a prompt into another session and submit it",
        description=(
            "Type a prompt into a live managed session, as if the user typed it, and submit it.\n"
            "The target is a session's name in the sender's project, ignoring case, or <project>/<name>\n"
            "for another project's. Nothing comes back: switch to that session to follow it.\n"
            "Run from inside tmux, the prompt starts with a line naming the sending session."
        ),
    )
    p_send.add_argument("target", help="Session name in this project, e.g. dev, or <project>/<name> for another")
    p_send.add_argument("--project", help="The project to look the name up in (default: this session's, else default)")
    p_send.add_argument("prompt", nargs="?", help="The prompt; '-' or none reads it from stdin")
    p_send.add_argument("--no-header", action="store_true", help="Don't add the [agentop send from …] line")

    # project
    p_project = sub.add_parser("project", help="List projects, or show one's folder and sessions")
    project_sub = p_project.add_subparsers(dest="project_command", metavar="<subcommand>")
    p_plist = project_sub.add_parser("list", help="Every project, with its owner and folder")
    p_plist.add_argument("--json", action="store_true")
    p_pshow = project_sub.add_parser("show", help="A project's folder and sessions (default: this session's)")
    p_pshow.add_argument("name", nargs="?")
    p_pshow.add_argument("--json", action="store_true")
    p_pcreate = project_sub.add_parser(
        "create", help="Make a project of the Projects plugin's, or load a closed one's kept folder, and start its Coordinator"
    )
    p_pcreate.add_argument("name")
    p_pclose = project_sub.add_parser(
        "close", help="Run by a project's Coordinator once no task is open: mark it closed in track/, drop it from the list, end this session"
    )
    p_pclose.add_argument("name")
    p_pclose.add_argument("--note", help="One line for the mark, e.g. what it ended with")

    # serve
    p_serve = sub.add_parser("serve", help="Run the service in the foreground")
    p_serve.add_argument("--host", help="Address to listen on (default: 127.0.0.1)")
    p_serve.add_argument("--port", type=int, help="Port to listen on (default: 9775)")
    p_serve.add_argument("--name", help="Name shown to clients (default: hostname)")
    p_serve.add_argument("--tls-cert", help="TLS certificate file")
    p_serve.add_argument("--tls-key", help="TLS private key file")
    p_serve.add_argument(
        "--update-from",
        choices=["main", "release"],
        help="What Update follows: the main branch (default), or the newest vX.Y.Z release tag",
    )

    # open
    sub.add_parser("open", help="Open the web app in a browser, signed in")

    # service
    p_service = sub.add_parser("service", help="Control the service, and get its access details")
    service_sub = p_service.add_subparsers(dest="service_command", metavar="<subcommand>")
    service_sub.add_parser("status", help="Show whether the service is running")
    service_sub.add_parser("stop", help="Stop the service. Sessions keep running")
    service_sub.add_parser("restart", help="Restart the service. Sessions keep running")
    service_sub.add_parser("update", help="Update from git, and go back if the new version fails")
    p_token = service_sub.add_parser("token", help="Print the service token, which signs a client in")
    p_token.add_argument("--rotate", action="store_true", help="Replace it. Clients already signed in stay signed in")
    p_token.add_argument("--revoke-clients", action="store_true", help="With --rotate: also sign out every client")
    p_clients = service_sub.add_parser("clients", help="List the clients signed in, or sign one out")
    p_clients.add_argument("--revoke", metavar="ID", help="Sign out this client")
    p_clients.add_argument("--revoke-all", action="store_true", help="Sign out every client")
    p_connect = service_sub.add_parser("connect-info", help="Print a string to paste into Add Service in the app")
    p_connect.add_argument("--host", help="Address other machines use to reach this service")
    p_connect.add_argument("--url", help="Full address when behind a proxy, e.g. http://203.0.113.9:7700/agentop")

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

    if args.command == "project":
        if not args.project_command:
            p_project.print_help()
            sys.exit(0)
        {
            "list": cmd_project_list,
            "show": cmd_project_show,
            "create": cmd_project_create,
            "close": cmd_project_close,
        }[args.project_command](args)
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
            "clients": cmd_service_clients,
            "connect-info": cmd_service_connect_info,
        }[args.service_command](args)
        return

    {
        "list": cmd_list,
        "history": cmd_history,
        "start": cmd_start,
        "stop": cmd_stop,
        "send": cmd_send,
        "serve": cmd_serve,
        "open": cmd_open,
    }[
        args.command
    ](args)


if __name__ == "__main__":
    main()
