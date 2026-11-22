"""The Projects plugin's commands: `agentop task`, `agentop yield` and `agentop review`. See docs/0_13_projects.md, section 4.3."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

from agentop import agentclis

from . import pursuit


def register_cli(cli) -> None:
    task = cli.command(
        "task",
        lambda args: run_task(args, task),
        help="A Coordinator's delegated tasks: start, report, accept, reject, fail, list, show",
        description="Delegate bounded work from a project's Coordinator, with a goal, criteria, an artifact and at most "
        f"{pursuit.MAX_ROUNDS} rounds.",
    )
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--project", help="The project (default: this session's)")
    sub = task.add_subparsers(dest="task_command", metavar="<subcommand>")

    start = sub.add_parser("start", parents=[common], help="Start a session on a new task (Coordinator)")
    start.add_argument("name", help="The session's name in the project")
    start.add_argument("--tool", required=True, choices=agentclis.names())
    start.add_argument("--cwd", required=True, help="Its working directory, usually <project>/code/<repo>")
    start.add_argument("--model", help='Claude takes "Sonnet 5.5"; codex and antigravity, none')
    start.add_argument("--goal", required=True, help="The one outcome it delivers")
    start.add_argument("--accept", action="append", default=[], required=True, metavar="CRITERION",
                       help="A criterion you will check the result against; repeat for each")
    start.add_argument("--artifact", required=True, help="The file in the project folder its result goes to")
    start.add_argument("--prompt", help="More of the brief (context, constraints); '-' reads it from stdin")

    report = sub.add_parser("report", parents=[common], help="Report this session's result to the Coordinator (the task's session)")
    report.add_argument("summary", nargs="?", help="What you did and the evidence; '-' or none reads stdin")
    report.add_argument("--blocked", action="store_true", help="You can't finish: say what stops you")

    accept = sub.add_parser("accept", parents=[common], help="Accept a reported result and stop its session (Coordinator)")
    accept.add_argument("name")
    accept.add_argument("--checked", required=True, help="What you verified yourself, against which criteria")
    accept.add_argument("--keep", action="store_true", help="Keep the session running")

    reject = sub.add_parser("reject", parents=[common], help="Send a result back with feedback, for another round (Coordinator)")
    reject.add_argument("name")
    reject.add_argument("--feedback", required=True, help="Which criterion failed and what to change; '-' reads stdin")

    fail = sub.add_parser("fail", parents=[common], help="Close a task as not done and stop its session (Coordinator)")
    fail.add_argument("name")
    fail.add_argument("--note", required=True, help="What is missing, and what you will do instead")
    fail.add_argument("--keep", action="store_true", help="Keep the session running")

    listing = sub.add_parser("list", parents=[common], help="The project's tasks (default: open ones)")
    listing.add_argument("--all", action="store_true")
    listing.add_argument("--json", action="store_true")

    show = sub.add_parser("show", parents=[common], help="One session's open task, with its rounds")
    show.add_argument("name")

    turn = cli.command(
        "yield",
        lambda args: run_yield(args),
        help="End a Coordinator's turn with its reason: done, waiting, owner or blocked",
        description="Record why the Coordinator stops now. Each reason is checked; agentop nudges a turn that ends without one.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "  done     --evidence: what shows the objective or request is met (needs a fresh review)\n"
            "  waiting  tasks are out and their reports are awaited\n"
            "  owner    --question --recommend --default [--meanwhile]: only the owner can decide\n"
            "  blocked  --need --tried: authority, access or an outside change is required"
        ),
    )
    turn.add_argument("kind", choices=pursuit.KINDS)
    turn.add_argument("--project", help="The project (default: this session's)")
    for flag, text in (
        ("evidence", "done: what shows it is met"),
        ("question", "owner: the decision you need"),
        ("recommend", "owner: your recommended answer, and why"),
        ("default", "owner: what you will do if there is no answer"),
        ("meanwhile", "owner: what you keep doing that the question doesn't block"),
        ("need", "blocked: the authority, access or change you need"),
        ("tried", "blocked: what you tried"),
        ("note", "anything else to record"),
    ):
        turn.add_argument(f"--{flag}", default="", help=text)

    review = cli.command(
        "review",
        lambda args: run_review(args),
        help="Review the Coordinator's pursuit, and write a report for it and the owner",
        description="Measure the Coordinator from the project's event log since the last review, and write reviews/<time>.md.",
    )
    review.add_argument("--project", help="The project (default: this session's)")
    review.add_argument("--all", action="store_true", help="Since the project began, not the last review")
    review.add_argument("--no-write", action="store_true", help="Only print it; record no review")


def _fail(message: str) -> None:
    print(f"Error: {message}", file=sys.stderr)
    sys.exit(1)


def _ops():
    from agentop import ops

    return ops


def _here() -> tuple[str, str, str] | None:
    return _ops().current_session()


def _project(args) -> str:
    from agentop import projects

    name = args.project or (_here() or (None, None, None))[1]
    if not name:
        _fail("run this inside a project's session, or give --project")
    found = projects.get(name)
    if found is None or found.get("owner") != "projects":
        _fail(f"{name} is not a Projects project")
    return name


def _read(text: str | None) -> str:
    return sys.stdin.read() if text in (None, "-") else text


def _send(target: str, text: str, project: str) -> bool:
    from agentop.cli import deliver
    from agentop.results import OpsError

    try:
        deliver(target, text, project)
        return True
    except OpsError as exc:
        print(f"Warning: it didn't reach {target}: {exc}", file=sys.stderr)
        return False


def _stop(name: str, project: str) -> None:
    from agentop.results import OpsError

    try:
        found = _ops().find_session(name, project=project)
        _ops().stop(found["name"])
        print(f"Stopped {name}.")
    except OpsError as exc:
        print(f"Warning: {name} was not stopped: {exc}", file=sys.stderr)


def brief(task: dict, project: str, extra: str) -> str:
    """The task's first prompt, after the project header ops.first_prompt adds."""
    artifact = pursuit.resolve_artifact(project, task["artifact"])
    criteria = "\n".join(f"- {c}" for c in task["accept"])
    text = (
        f"[agentop task {task['id']}] The Coordinator owns the project's goal and checks your result. You own this task.\n\n"
        f"Goal: {task['goal']}\n\nAcceptance criteria:\n{criteria}\n\n"
        f"Artifact: write your result to {artifact}. For code, it says what you changed (commits), how you verified it, "
        "and what is left.\n\n"
        "When you are done, run: agentop task report \"<what you did, and the evidence it meets each criterion>\". "
        "It checks the artifact exists and tells the Coordinator. If you can't finish or need a decision, run "
        "agentop task report --blocked \"<what stops you>\". Never stop without reporting."
    )
    return f"{text}\n\n{extra.strip()}" if extra and extra.strip() else text


def task_start(args, project: str) -> None:
    ops = _ops()
    if not ops.is_valid_short_name(args.name):
        _fail("a session name is 1-32 letters, digits, '-' or '_', starting with a letter or digit")
    cwd = os.path.expanduser(args.cwd)
    params = {}
    if args.model:
        try:
            params["model"] = agentclis.get_agent(args.tool).model_id(args.model)
        except ValueError as exc:
            _fail(str(exc))
    taken = [
        s for s in ops.get_sessions(with_meta=False)
        if s.get("project") == project and s.get("display_name", "").lower() == args.name.lower()
    ]
    if taken:
        _fail(f"a session called {args.name} is already in {project}. Pick another name, or reject/accept its task")
    extra = _read(args.prompt) if args.prompt is not None else ""
    try:
        task = pursuit.open_task(project, args.name, args.tool, cwd, args.goal, args.accept, args.artifact)
    except pursuit.PursuitError as exc:
        _fail(str(exc))
    from agentop.results import OpsError

    try:
        result = ops.start(args.tool, cwd, args.name, params, project=project)
        ops.first_prompt(result.name, brief(task, project, extra))
    except OpsError as exc:
        pursuit.fail(project, args.name, f"The session didn't start: {exc}")
        _fail(str(exc))
    print(f"Task {task['id']}: {args.name} ({args.tool}) started in {cwd}; round 1 of {pursuit.MAX_ROUNDS}.")
    print(f"Its result goes to {task['artifact']}. It reports with agentop task report; you'll get a message.")


def task_report(args, project: str) -> None:
    here = _here()
    if not here or here[1] != project:
        _fail("agentop task report runs in the session that owns the task")
    name = here[2]
    try:
        task = pursuit.report(project, name, _read(args.summary), args.blocked)
    except pursuit.PursuitError as exc:
        _fail(str(exc))
    from agentop import projects

    rounds = len(task["rounds"])
    current = task["rounds"][-1]
    criteria = "; ".join(task["accept"])
    state = "BLOCKED" if args.blocked else "reported"
    notice = (
        f"[agentop task {task['id']}] {name} {state} round {rounds} of {pursuit.MAX_ROUNDS}: {current['summary']}\n"
        f"Artifact: {task['artifact']} ({'present' if current['artifact_present'] else 'missing'}). "
        f"Check it yourself against: {criteria}. Then agentop task accept {name} --checked \"…\", "
        f"agentop task reject {name} --feedback \"…\", or agentop task fail {name} --note \"…\"."
    )
    sent = _send(projects.COORDINATOR, notice, project)
    print(f"Reported round {rounds} of {task['id']}." + (" The Coordinator has been told." if sent else ""))
    print("Wait for the Coordinator's answer; don't start other work.")


def task_accept(args, project: str) -> None:
    try:
        task = pursuit.accept(project, args.name, args.checked)
    except pursuit.PursuitError as exc:
        _fail(str(exc))
    print(f"Accepted {task['id']} after {len(task['rounds'])} round(s).")
    if not args.keep:
        _stop(task["session"], project)
    print(f"Next: record it in {pursuit.STATE_FILE}, then take the next action.")
    closed = [e for e in pursuit.events(project) if e["kind"] in ("task.accept", "task.fail")]
    reviews = [e["at"] for e in pursuit.events(project) if e["kind"] == "review"]
    since = sum(1 for e in closed if not reviews or e["at"] > max(reviews))
    if since >= 5:
        print(f"{since} tasks closed since the last review: run agentop review at this milestone.")


def task_reject(args, project: str) -> None:
    feedback = _read(args.feedback)
    try:
        task = pursuit.reject(project, args.name, feedback)
    except pursuit.PursuitError as exc:
        _fail(str(exc))
    rounds = len(task["rounds"])
    text = (
        f"[agentop task {task['id']}, round {rounds} of {pursuit.MAX_ROUNDS}] The Coordinator did not accept your result:\n"
        f"{feedback.strip()}\n\nFix it, update {task['artifact']}, then agentop task report \"…\" again."
    )
    _send(task["session"], text, project)
    print(f"Sent {args.name} round {rounds} of {pursuit.MAX_ROUNDS}.")


def task_fail(args, project: str) -> None:
    try:
        task = pursuit.fail(project, args.name, args.note)
    except pursuit.PursuitError as exc:
        _fail(str(exc))
    print(f"Closed {task['id']} as failed.")
    if not args.keep:
        _stop(task["session"], project)
    print(f"Next: record why in {pursuit.STATE_FILE}, then replan or take the decision to the owner.")


def _ago(at: float) -> str:
    minutes = int((time.time() - at) / 60)
    return f"{minutes} min ago" if minutes < 120 else f"{minutes // 60} h ago"


def task_list(args, project: str) -> None:
    tasks = pursuit.load_tasks(project) if args.all else pursuit.open_tasks(project)
    if args.json:
        print(json.dumps(tasks, indent=2, ensure_ascii=False))
        return
    if not tasks:
        print("No open tasks." if not args.all else "No tasks.")
        return
    for t in tasks:
        current = t["rounds"][-1]
        if t["status"] != "open":
            state = t["status"]
        elif current.get("reported_at"):
            state = "reported, waits for you" if not current.get("blocked") else "BLOCKED, waits for you"
        else:
            state = f"working, sent {_ago(current['sent_at'])}"
        print(f"{t['id']:<5} {t['session']:<24} round {len(t['rounds'])}/{pursuit.MAX_ROUNDS}  {state}")
        print(f"      {t['goal']}")


def task_show(args, project: str) -> None:
    task = pursuit.task_for(project, args.name)
    if task is None:
        _fail(f"{args.name} has no open task")
    print(f"{task['id']} {task['session']} ({task['tool']}) in {task['cwd']}")
    print(f"Goal: {task['goal']}")
    for c in task["accept"]:
        print(f"  - {c}")
    print(f"Artifact: {task['artifact']}")
    for n, r in enumerate(task["rounds"], 1):
        print(f"Round {n}: sent {_ago(r['sent_at'])}")
        if r.get("reported_at"):
            print(f"  {'Blocked' if r.get('blocked') else 'Reported'}: {r['summary']}")
        if r.get("feedback"):
            print(f"  Feedback: {r['feedback']}")


def run_task(args, parser) -> None:
    command = getattr(args, "task_command", None)
    if not command:
        parser.print_help()
        return
    project = _project(args)
    {
        "start": task_start,
        "report": task_report,
        "accept": task_accept,
        "reject": task_reject,
        "fail": task_fail,
        "list": task_list,
        "show": task_show,
    }[command](args, project)


def run_yield(args) -> None:
    from agentop import projects

    project = _project(args)
    here = _here()
    if here and here[2] != projects.COORDINATOR:
        _fail("only the Coordinator yields; a task's session reports with agentop task report")
    fields = {k: getattr(args, k) for k in ("evidence", "question", "recommend", "default", "meanwhile", "need", "tried", "note")}
    try:
        event = pursuit.yield_turn(project, args.kind, fields)
    except pursuit.PursuitError as exc:
        _fail(f"{exc}. If it can't be fixed now, the next action is the fix.")
    if args.kind == "waiting":
        print(f"Recorded: waiting on {event['sessions']}. End your turn; their reports will come to you.")
    elif args.kind == "owner":
        print("Recorded. Put the question, your recommendation and the default in your message to the owner, then end your turn.")
    else:
        print("Recorded. End your turn with a short message to the owner.")


def run_review(args) -> None:
    from . import review

    project = _project(args)
    text, path, _ = review.run(project, since=0.0 if args.all else None, write=not args.no_write)
    print(text)
    if path:
        print(f"\nWritten to {path}.")
        print("Now write your reflection in its last section, then tell the owner the path and the top findings.")
