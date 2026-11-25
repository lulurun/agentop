"""The Projects plugin's commands: `agentop coordinator …`, with `agentop coordinator task report` for a task's session.

See docs/0_13_projects.md, section 4.3.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

from agentop import agentclis

from . import checker, pursuit, track, wake


def _common() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--project", help="The project (default: this session's)")
    return common


def _task_parsers(sub, common) -> None:
    start = sub.add_parser("start", parents=[common], help="Start a session on a new task (Coordinator)")
    start.add_argument("name", help="The session's name in the project")
    start.add_argument("--action", help="The plan's action the task works on, M1A2 (needed once the project has a track)")
    start.add_argument("--tool", required=True, choices=agentclis.names())
    start.add_argument("--cwd", required=True, help="Its working directory, usually <project>/code/<repo>")
    start.add_argument("--model", help='Claude takes "Sonnet 5.5"; codex and antigravity, none')
    start.add_argument(
        "--no-remote-control",
        action="store_true",
        help="Do not expose a Claude session through claude.ai remote control",
    )
    start.add_argument("--goal", required=True, help="The one outcome it delivers")
    start.add_argument("--accept", action="append", default=[], required=True, metavar="CRITERION",
                       help="A criterion you will check the result against; repeat for each")
    start.add_argument("--artifact", required=True, help="The file in the project folder its result goes to")
    start.add_argument("--prompt", help="More of the brief (context, constraints); '-' reads it from stdin")

    report = sub.add_parser("report", parents=[common], help="Report this session's result to the Coordinator (the task's session)")
    report.add_argument("summary", nargs="?", help="What you did and the evidence; '-' or none reads stdin")
    report.add_argument("--blocked", action="store_true", help="You can't finish: say what stops you")

    accept = sub.add_parser("accept", parents=[common], help="Accept a reported result and stop its session (Coordinator)")
    accept.add_argument("name", help="The task's session, or its ID")
    accept.add_argument("--checked", required=True, help="What you verified yourself, against which criteria")
    accept.add_argument("--keep", action="store_true", help="Keep the session running")

    reject = sub.add_parser("reject", parents=[common], help="Send a result back with feedback, for another round (Coordinator)")
    reject.add_argument("name", help="The task's session, or its ID")
    reject.add_argument("--feedback", required=True, help="Which criterion failed and what to change; '-' reads stdin")

    fail = sub.add_parser("fail", parents=[common], help="Close a task as not done and stop its session (Coordinator)")
    fail.add_argument("name", help="The task's session, or its ID")
    fail.add_argument("--note", required=True, help="What is missing, and what you will do instead")
    fail.add_argument("--keep", action="store_true", help="Keep the session running")

    listing = sub.add_parser("list", parents=[common], help="The project's tasks (default: open ones)")
    listing.add_argument("--all", action="store_true")
    listing.add_argument("--json", action="store_true")

    show = sub.add_parser("show", parents=[common], help="One open task, with its rounds")
    show.add_argument("name", help="The task's session, or its ID")


def _yield_args(turn) -> None:
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
        ("next", "done, waiting (required): what you will do next and why; a cleared context starts from it"),
        ("note", "anything else to record"),
    ):
        turn.add_argument(f"--{flag}", default="", help=text)
    turn.add_argument(
        "--clear", action="store_true",
        help="clear the Coordinator's context once this turn has ended and it is safe, then carry on",
    )
    turn.add_argument("--keep", action="store_true", help="done, waiting, owner: keep the context at the next wake")


YIELD_HELP = dict(
    help="End a Coordinator's turn with its reason: done, waiting, owner or blocked",
    description="Record why the Coordinator stops now. Each reason is checked.",
    formatter_class=argparse.RawDescriptionHelpFormatter,
    epilog=(
        "  done     --evidence --next: what shows the direction or request is met (needs a fresh review)\n"
        "  waiting  --next: tasks are out and their reports are awaited\n"
        "  owner    --question --recommend --default [--meanwhile]: only the owner can decide; it becomes a plan ask\n"
        "  blocked  --need --tried: authority, access or an outside change is required"
    ),
)


def _review_args(review) -> None:
    review.add_argument("--project", help="The project (default: this session's)")
    review.add_argument("--all", action="store_true", help="Since the project began, not the last review")
    review.add_argument("--no-write", action="store_true", help="Only print it; record no review")


def register_cli(cli) -> None:
    top = cli.command(
        "coordinator",
        lambda args: run_coordinator(args, top),
        help="A Projects Coordinator's commands: direction, plan, milestone, note, task, yield, review, status",
        description="The Coordinator's record is track/<N>/ in the project folder: DIRECTION.md, PLAN.md and "
        "EXECUTION_M<m>.md. These commands write it; files of older directions are never written.",
    )
    common = _common()
    sub = top.add_subparsers(dest="coordinator_command", metavar="<subcommand>")

    closing = sub.add_parser("close", parents=[common], help="Prioritize closing: drain existing tasks, then project close")
    closing.add_argument("--note", default="", help="Why the owner asked to close")

    direction = sub.add_parser("direction", help="The project's direction: change, new, add, show")
    dsub = direction.add_subparsers(dest="direction_command", metavar="<subcommand>")
    change = dsub.add_parser("change", parents=[common], help="Record a new direction as the plan's first action, "
                             "pending until the open tasks close; then run direction new")
    change.add_argument("--text", required=True, help="The new direction, as the owner gave it; '-' reads stdin")
    new = dsub.add_parser("new", parents=[common], help="Finish the current plan and start direction N+1: "
                          "track/<N+1>/DIRECTION.md and PLAN.md; refused while a task is open")
    new.add_argument("--text", help="The direction: what the project pursues now, what done means; '-' reads stdin "
                     "(default: the pending direction change's text)")
    new.add_argument("--copy-plan", action="store_true", help="Start the new plan as a copy of the last one")
    add = dsub.add_parser("add", parents=[common], help="Append a dated addendum to the current DIRECTION.md: a "
                          "requirement that doesn't change the direction")
    add.add_argument("--text", help="The new requirement, as the owner gave it; '-' reads stdin")
    add.add_argument("--no-plan-change", action="store_true", help="The plan needs no change for it (without --text: "
                     "for the addendum that waits for a plan change)")
    add.add_argument("--direction", type=int, help="The direction to add to; only the current one is written")
    dsub.add_parser("show", parents=[common], help="The current DIRECTION.md, with its addenda")

    plan = sub.add_parser("plan", help="The current PLAN.md: show, milestone add, action add|set, ask, answer")
    psub = plan.add_subparsers(dest="plan_command", metavar="<subcommand>")
    show = psub.add_parser("show", parents=[common], help="Open milestones in full, closed ones as a line and summary")
    show.add_argument("--all", action="store_true", help="The whole file")
    milestone = psub.add_parser("milestone", help="add")
    msub = milestone.add_subparsers(dest="milestone_command", metavar="<subcommand>")
    madd = msub.add_parser("add", parents=[common], help="Add a milestone, at the end or before another")
    madd.add_argument("title")
    madd.add_argument("--before", metavar="M3")
    action = psub.add_parser("action", help="add, set")
    asub = action.add_subparsers(dest="action_command", metavar="<subcommand>")
    aadd = asub.add_parser("add", parents=[common], help="Add an action to a milestone")
    aadd.add_argument("milestone", metavar="M2")
    aadd.add_argument("title")
    aadd.add_argument("--before", metavar="M2A3")
    aset = asub.add_parser("set", parents=[common], help="Set an action's status")
    aset.add_argument("action", metavar="M2A3")
    aset.add_argument("--status", required=True, help="planned, in progress, blocked, done (needs an execution entry or --note), dropped (needs --reason)")
    aset.add_argument("--reason", default="", help="dropped: why")
    aset.add_argument("--note", default="", help="done: what was done, recorded as an execution note")
    plan_ask = psub.add_parser("ask", parents=[common], help="A waiting-on-owner action: the question, your recommendation and the default")
    plan_ask.add_argument("ref", nargs="?", metavar="M2A4|M2", help="The action to turn into the ask, or the milestone to add it to")
    plan_ask.add_argument("--question", required=True)
    plan_ask.add_argument("--recommend", required=True)
    plan_ask.add_argument("--default", required=True)
    plan_answer = psub.add_parser("answer", parents=[common], help="Close an ask with the owner's answer")
    plan_answer.add_argument("action", metavar="M2A4")
    plan_answer.add_argument("text", help="The owner's answer; '-' reads stdin")

    close = sub.add_parser("milestone", help="close")
    csub = close.add_subparsers(dest="milestone_command", metavar="<subcommand>")
    mclose = csub.add_parser("close", parents=[common], help="Close a milestone whose actions are all done or dropped")
    mclose.add_argument("milestone", metavar="M1")
    mclose.add_argument("--summary", required=True, help="3-5 lines: what was delivered, where it is, what to remember; '-' reads stdin")

    note = sub.add_parser("note", parents=[common], help="An execution entry for work done without a task")
    note.add_argument("action", metavar="M2A1")
    note.add_argument("text", help="What was done, and where the result is; '-' reads stdin")
    note.add_argument("--by", default="coordinator", choices=("coordinator", "owner"))

    task = sub.add_parser("task", help="Delegated tasks: start, report, accept, reject, fail, list, show",
                          description="Delegate bounded work, with a goal, criteria, an artifact and at most "
                          f"{pursuit.MAX_ROUNDS} rounds. The task's session reports with agentop coordinator task report.")
    _task_parsers(task.add_subparsers(dest="task_command", metavar="<subcommand>"), common)

    _yield_args(sub.add_parser("yield", **YIELD_HELP))
    _review_args(sub.add_parser(
        "review", help="Review the Coordinator's pursuit, and write a report for it and the owner",
        description="Measure the Coordinator from the project's event log since the last review, and write reviews/<time>.md.",
    ))
    sub.add_parser("status", parents=[common], help="Where things are: direction, plan, the current milestone's execution, open tasks")


def _fail(message: str) -> None:
    print(f"Error: {message}", file=sys.stderr)
    sys.exit(1)


def _ops():
    from agentop import ops

    return ops


def _here() -> tuple[str, str, str] | None:
    return _ops().current_session()


def _project(args, closed_too: bool = False) -> str:
    """The project the command is for; with closed_too, a closed one's kept folder with a track/ also does."""
    from agentop import projects

    name = args.project or (_here() or (None, None, None))[1]
    if not name:
        _fail("run this inside a project's session, or give --project")
    found = projects.get(name)
    if closed_too and found is None and projects.is_valid_name(name) and track.closed_state(name):
        return name
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


def _wake(project: str, text: str, source: str, task: str) -> bool:
    """Tell the Coordinator of a task event, clearing its context first when safe: through the service, else here."""
    from agentop import projects
    from agentop.cli import _service_api
    from agentop.results import OpsError
    from agentop.service import config
    from agentop.tmux import PasteError

    body = {"text": text, "source": source, "task": task}
    answer = _service_api(config.load(), "POST", f"/plugins/projects/projects/{project}/wake", body, timeout=90)
    if answer is not None and answer[0] < 400:
        return True
    if answer is not None and answer[0] != 404:
        print(f"Warning: it didn't reach the Coordinator: {answer[1].get('detail') or answer[0]}", file=sys.stderr)
        return False
    try:
        row = _ops().find_session(projects.COORDINATOR, project=project)
    except OpsError as exc:
        print(f"Warning: it didn't reach the Coordinator: {exc}", file=sys.stderr)
        return False
    pane = wake.pane_of(row) if row.get("managed") else None
    if pane is None:
        return _send(projects.COORDINATOR, pursuit.wake_note(project, source) + text, project)
    try:
        wake.deliver_event(project, pane, text, source, task)
    except PasteError as exc:
        print(f"Warning: it didn't reach the Coordinator: {exc}", file=sys.stderr)
        return False
    return True


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
        f"[task {task['id']}] The Coordinator owns the project's goal and checks your result. You own this task.\n\n"
        f"Goal: {task['goal']}\n\nAcceptance criteria:\n{criteria}\n\n"
        f"Artifact: write your result to {artifact}. For code, it says what you changed (commits), how you verified it, "
        "and what is left.\n\n"
        "When you are done, run: agentop coordinator task report \"<one paragraph: what you did, and the evidence it meets "
        "each criterion, pointing to the artifact for the detail>\". "
        "It checks the artifact exists and tells the Coordinator. If you can't finish or need a decision, run "
        "agentop coordinator task report --blocked \"<what stops you>\". Never stop without reporting."
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
    if args.no_remote_control:
        params["remote_control"] = False
    taken = [
        s for s in ops.get_sessions(with_meta=False)
        if s.get("project") == project and s.get("display_name", "").lower() == args.name.lower()
    ]
    if taken:
        _fail(f"a session called {args.name} is already in {project}. Pick another name, or reject/accept its task")
    action = getattr(args, "action", None)
    if action is None and track.exists(project):
        _fail("a task needs --action: the plan's action it works on (agentop coordinator plan show lists them; "
              "add one with agentop coordinator plan action add M1 \"…\")")
    extra = _read(args.prompt) if args.prompt is not None else ""
    try:
        task = pursuit.open_task(project, args.name, args.tool, cwd, args.goal, args.accept, args.artifact,
                                 action=action, model=args.model, brief=extra)
    except pursuit.PursuitError as exc:
        _fail(str(exc))
    from agentop.results import OpsError

    try:
        result = ops.start(args.tool, cwd, args.name, params, project=project)
        ops.first_prompt(result.name, brief(task, project, extra))
    except OpsError as exc:
        pursuit.fail(project, task["id"], f"The session didn't start: {exc}")
        _fail(str(exc))
    print(f"Task {task['id']}: {args.name} ({args.tool}) started in {cwd}; round 1 of {pursuit.MAX_ROUNDS}.")
    print(f"Its result goes to {task['artifact']}. It reports with agentop coordinator task report; you'll get a message.")


def task_report(args, project: str) -> None:
    here = _here()
    if not here or here[1] != project:
        _fail("agentop coordinator task report runs in the session that owns the task")
    name = here[2]
    try:
        task = pursuit.report(project, name, _read(args.summary), args.blocked)
    except pursuit.PursuitError as exc:
        _fail(str(exc))
    rounds = len(task["rounds"])
    current = task["rounds"][-1]
    criteria = "; ".join(task["accept"])
    state = "BLOCKED" if args.blocked else "reported"
    notice = (
        f"[task {task['id']}] {name} {state} round {rounds} of {pursuit.MAX_ROUNDS}: {current['summary']}\n"
        f"Artifact: {task['artifact']} ({'present' if current['artifact_present'] else 'missing'}). "
        f"Check it yourself against: {criteria}. Then agentop coordinator task accept {name} --checked \"…\", "
        f"agentop coordinator task reject {name} --feedback \"…\", or agentop coordinator task fail {name} --note \"…\"."
    )
    sent = _wake(project, notice, "task.blocked" if args.blocked else "task.report", task["id"])
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
    _recorded(project, task, "take the next action")
    closed = [e for e in pursuit.events(project) if e["kind"] in ("task.accept", "task.fail")]
    reviews = [e["at"] for e in pursuit.events(project) if e["kind"] == "review"]
    since = sum(1 for e in closed if not reviews or e["at"] > max(reviews))
    if since >= 5:
        print(f"{since} tasks closed since the last review: run agentop coordinator review at this milestone.")


def task_reject(args, project: str) -> None:
    feedback = _read(args.feedback)
    try:
        task = pursuit.reject(project, args.name, feedback)
    except pursuit.PursuitError as exc:
        _fail(str(exc))
    rounds = len(task["rounds"])
    text = (
        f"[task {task['id']}, round {rounds} of {pursuit.MAX_ROUNDS}] The Coordinator did not accept your result:\n"
        f"{feedback.strip()}\n\nFix it, update {task['artifact']}, then agentop coordinator task report \"…\" again."
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
    _recorded(project, task, "replan or take the decision to the owner")


def _ago(at: float) -> str:
    minutes = int((time.time() - at) / 60)
    return f"{minutes} min ago" if minutes < 120 else f"{minutes // 60} h ago"


def _feed_state(project: str, session: str) -> str:
    """A task session's state in the status feed and how long it has held, as `input 4m`, else `unknown`."""
    from agentop import registry
    from agentop.paths import state_dir
    from agentop.status_feed import fetch

    names = [name for name, r in registry.load().get("sessions", {}).items()
             if r.get("managed") and r.get("project") == project and (r.get("display_name") or "").lower() == session.lower()]
    for name in names:
        status = checker.feed_state(fetch(state_dir(), name, timeout=0.5))
        if status is not None:
            if status.state == "idle":
                return "idle"
            return f"{status.state} {checker.age(time.time() - status.since)}"
    return "unknown"


def task_list(args, project: str) -> None:
    tasks = pursuit.load_tasks(project) if args.all else pursuit.open_tasks(project)
    if args.json:
        for t in tasks:
            if t["status"] == "open":
                t["feed"] = _feed_state(project, t["session"])
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
        if t["status"] == "open":
            state += f", feed {_feed_state(project, t['session'])}"
        print(f"{t['id']:<14} {t['session']:<20} round {len(t['rounds'])}/{pursuit.MAX_ROUNDS}  {state}")
        print(f"{'':<15}{t['goal']}")


def task_show(args, project: str) -> None:
    task = pursuit.task_for(project, args.name)
    if task is None:
        _fail(f"{args.name} has no open task")
    print(f"{task['id']} {task['session']} ({task['tool']}) in {task['cwd']}" + (f", action {task['action']}" if task.get("action") else ""))
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
    project = _project(args, closed_too=command == "status")
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
        _fail("only the Coordinator yields; a task's session reports with agentop coordinator task report")
    fields = {k: getattr(args, k, "") for k in (
        "evidence", "question", "recommend", "default", "meanwhile", "need", "tried", "next", "note")}
    if getattr(args, "keep", False):
        fields["keep"] = "yes"
    try:
        event = pursuit.yield_turn(project, args.kind, fields)
    except pursuit.PursuitError as exc:
        _fail(f"{exc}. If it can't be fixed now, the next action is the fix.")
    if event.get("next"):
        print(f"Next is in {pursuit.next_path(project).relative_to(track.projects.folder(project))}.")
    if getattr(args, "clear", False):
        pursuit.log(project, "clear.pending", type=args.kind)
        print("Recorded. End your turn with a short message to the owner; once it has ended and it is safe, your "
              "context is cleared, and agentop tells you to carry on from your Next.")
    elif args.kind == "waiting":
        print(f"Recorded: waiting on {event['sessions']}. End your turn; their reports will come to you, "
              "each after a clear of your context when it is safe.")
    elif args.kind == "owner":
        if event.get("action"):
            again = f", asked {event['asked']}×" if event.get("asked") else ""
            print(f"Recorded as {event['action']}, waiting on owner, in the plan{again}. When the owner answers: "
                  f"agentop coordinator plan answer {event['action']} \"…\".")
        print("Put the question, your recommendation and the default in your message to the owner, then end your turn.")
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


def _recorded(project: str, task: dict, then: str) -> None:
    due = pursuit.change_due(project)
    if pursuit.close_request(project):
        then = "drain remaining tasks, then close the project"
    elif due:
        then = "the direction change; nothing else comes first"
    if task.get("action") and task.get("direction") == track.generation(project):
        m = task["action"].partition("A")[0][1:]
        status = track.find_action(track.load_plan(project), *track.parse_ref(project, task["action"], True))["status"]
        print(f"Recorded in track/{task['direction']}/EXECUTION_M{m}.md; {task['action']} is {status}. Next: {then}.")
    elif track.exists(project):
        print(f"Next: record it in the plan with agentop coordinator note <M…A…> \"…\", then {then}.")
    else:
        print(f"Next: record it in {pursuit.STATE_FILE}, then {then}.")
    if due:
        print(f"{due}: it takes the pending text, finishes the plan and starts the new direction.")


def _locked(project: str, fn, *args):
    try:
        with pursuit._locked(project):
            return fn(project, *args)
    except pursuit.PursuitError as exc:
        _fail(str(exc))


def _print_file(path) -> None:
    try:
        print(path.read_text().rstrip())
    except OSError:
        _fail(f"{path} doesn't exist yet. Run agentop coordinator direction new --text -")


def coordinator_direction(args, project: str) -> None:
    command = getattr(args, "direction_command", None)
    if command == "change":
        try:
            action, replaced, waits = pursuit.change_direction(project, _read(args.text))
        except pursuit.PursuitError as exc:
            _fail(str(exc))
        ref = track.action_ref(action)
        print(f"{'Replaced the pending' if replaced else 'Added the'} direction change as {ref}, first in the plan: "
              f"{action['title']}  [{action['status']}].")
        if waits:
            print(f"It waits for the open tasks: {track.task_names(waits)}. Decide each as usual (accept, reject, fail); "
                  "no new task starts until it is done. When the last closes, run agentop coordinator direction new.")
        else:
            print("No task is open: run agentop coordinator direction new now; it takes the pending text.")
    elif command == "new":
        before = track.generation(project)
        try:
            n = pursuit.new_direction(project, _read(args.text) if args.text is not None else None, args.copy_plan)
        except pursuit.PursuitError as exc:
            _fail(str(exc))
        if n != before:
            pursuit.log(project, "direction.new", direction=n)
            if before:
                print(f"Plan {before}'s final state is in track/{before}/PLAN.md; it is never written again.")
        print(f"Direction {n}: track/{n}/DIRECTION.md and track/{n}/PLAN.md.")
        print("Next: shape the plan with agentop coordinator plan milestone add / action add, then start tasks on its actions.")
    elif command == "add":
        if args.text is None and not args.no_plan_change:
            _fail("agentop coordinator direction add --text - (or --no-plan-change for the addendum waiting on the plan)")
        text = _read(args.text) if args.text is not None else ""
        k = _locked(project, track.add_addendum, text, args.no_plan_change, args.direction)
        n = track.generation(project)
        pursuit.log(project, "direction.addendum", direction=n, addendum=k, plan_change=not args.no_plan_change)
        if args.no_plan_change:
            print(f"Addendum {k} of direction {n} needs no plan change; recorded in track/{n}/DIRECTION.md.")
        else:
            print(f"Addendum {k} is in track/{n}/DIRECTION.md. Next: update the plan for it (plan milestone add, "
                  "action add or action set); yield refuses until you do, or until you record it needs no change "
                  "with agentop coordinator direction add --no-plan-change.")
    elif command == "show":
        _print_file(track.direction_path(project))
    else:
        _fail("agentop coordinator direction change|new|add|show")


def _changes_plan(fn):
    def run(project: str, *args):
        found = fn(project, *args)
        track.plan_changed(project)
        return found
    return run


def coordinator_plan(args, project: str) -> None:
    command = getattr(args, "plan_command", None)
    if command == "show":
        if args.all:
            _print_file(track.plan_path(project))
        else:
            print(_locked(project, lambda p: track.render_plan(track.load_plan(p), done_short=True)).rstrip())
    elif command == "milestone" and getattr(args, "milestone_command", None) == "add":
        found = _locked(project, _changes_plan(track.add_milestone), args.title, args.before)
        print(f"Added M{found['m']} {found['title']}.")
    elif command == "action" and getattr(args, "action_command", None) == "add":
        found = _locked(project, _changes_plan(track.add_action), args.milestone, args.title, args.before)
        print(f"Added {track.action_ref(found)} {found['title']}.")
    elif command == "action" and getattr(args, "action_command", None) == "set":
        found = _locked(project, _changes_plan(track.set_action), args.action, args.status, args.reason, args.note)
        print(f"{track.action_ref(found)} is {found['status']}.")
    elif command == "ask":
        found, refreshed = _locked(project, track.ask, args.ref, args.question, args.recommend, args.default)
        print(f"{track.action_ref(found)} waits on the owner" + (f" (asked {track.asked_times(found)}×)." if refreshed else "."))
    elif command == "answer":
        found = _locked(project, track.answer, args.action, _read(args.text))
        print(f"{track.action_ref(found)} is answered and done; the answer is recorded under it and in its execution file.")
    else:
        _fail("agentop coordinator plan show|milestone add|action add|action set|ask|answer")


def coordinator_milestone(args, project: str) -> None:
    if getattr(args, "milestone_command", None) != "close":
        _fail("agentop coordinator milestone close M1 --summary -")
    found = _locked(project, track.close_milestone, args.milestone, _read(args.summary))
    pursuit.log(project, "milestone.close", milestone=found["m"])
    print(f"M{found['m']} is done, with its summary in the plan.")


def coordinator_note(args, project: str) -> None:
    heading = _locked(project, track.add_note, args.action, _read(args.text), args.by)
    print(f"Recorded {heading} by {args.by}.")


def _task_line(t: dict, project: str) -> str:
    current = t["rounds"][-1]
    if current.get("reported_at"):
        state = "reported, waits for you" if not current.get("blocked") else "BLOCKED, waits for you"
    else:
        state = f"working, sent {_ago(current['sent_at'])}"
    action = t.get("action") or "no action"
    feed = _feed_state(project, t["session"])
    return f"- {t['id']} `{t['session']}` ({action}), round {len(t['rounds'])}/{pursuit.MAX_ROUNDS}, {state}, feed {feed}: {t['goal']}"


def status_text(project: str) -> str:
    """The resume view: what a fresh context needs to carry on."""
    closing = pursuit.close_notice(project).strip()
    out = (["# Priority close request", "", closing, ""] if closing else []) + _closed_section(project)
    if not closing:
        out += _change_section(project)
    found_next = pursuit.read_next(project)
    out += [found_next or "# Next\n\n(none yet: agentop coordinator yield waiting|done --next \"…\" writes it)", ""]
    n = track.generation(project)
    if not n:
        out.append("There is no track/ yet.")
        state = track.projects.folder(project) / pursuit.STATE_FILE
        if state.is_file():
            out += [f"This project still keeps {pursuit.STATE_FILE}; its checks apply until agentop coordinator direction new:",
                    "", state.read_text().rstrip()]
    else:
        out += [f"# Direction {n} (track/{n}/DIRECTION.md)", ""]
        try:
            text = track.direction_path(project).read_text()
            out.append(text.split("\n", 1)[1].strip() if text.startswith("# Direction") else text.strip())
        except OSError:
            out.append("(missing)")
        out += ["", f"# Plan {n} (track/{n}/PLAN.md)", ""]
        try:
            plan = track.load_plan(project)
            out.append(track.render_plan({**plan, "head": []}, done_short=True).strip() or "(no milestones yet)")
            milestone = track.current_milestone(plan)
        except track.TrackError as exc:
            out.append(str(exc))
            milestone = None
        if milestone is not None:
            path = track.execution_path(project, n, milestone["m"])
            out += ["", f"# Execution of M{milestone['m']}, the current milestone (track/{n}/{path.name})", ""]
            out.append(track.readable_execution(path) or "(no entries yet)")
    tasks = pursuit.open_tasks(project)
    out += ["", "# Tasks", ""]
    out += [_task_line(t, project) for t in tasks] or ["- No open tasks."]
    found = pursuit.problems(project)
    reported = [t["session"] for t in tasks if not pursuit.awaiting_report(t)]
    if reported:
        found.append(f"{', '.join(reported)} reported and wait for your decision")
    out += ["", "# Warnings (agentop coordinator yield refuses while they hold)", ""]
    out += [f"- {w}" for w in found] or ["- None."]
    return "\n".join(out)


def _closed_section(project: str) -> list[str]:
    state = track.closed_state(project)
    if state is None:
        return []
    path = track.closed_path(project)
    lines = [line for line in path.read_text().splitlines() if line.startswith("- ")]
    now = "closed" if " closed by " in state else "reopened"
    return [f"# Closed ({path.relative_to(track.projects.folder(project))}): {now} now", "", *lines, ""]


def _change_section(project: str) -> list[str]:
    change = track.current_change(project)
    if change is None:
        return []
    tasks = pursuit.open_tasks(project)
    out = [f"# Direction change pending: {track.action_ref(change)}, before anything else", "",
           f"{track.action_line(change)}", "", track.change_text(change), ""]
    if tasks:
        out += [f"It waits for {len(tasks)} open task(s); no new task starts until then:",
                *[f"- {t['id']} `{t['session']}`" for t in tasks]]
    else:
        out.append("No task is open: run agentop coordinator direction new now; it takes this text.")
    return out + [""]


def coordinator_status(args, project: str) -> None:
    print(status_text(project))


def run_coordinator(args, parser) -> None:
    command = getattr(args, "coordinator_command", None)
    if not command:
        parser.print_help()
        return
    if command == "yield":
        run_yield(args)
        return
    if command == "review":
        run_review(args)
        return
    if command == "task":
        if not getattr(args, "task_command", None):
            _fail("agentop coordinator task start|report|accept|reject|fail|list|show")
        run_task(args, parser)
        return
    project = _project(args, closed_too=command == "status")
    if command == "close":
        here = _here()
        if not here or here[1] != project or here[2] != "Coordinator":
            _fail("only the project Coordinator records a close request")
        pursuit.request_close(project, args.note)
        print(pursuit.close_notice(project).strip())
        return
    {
        "direction": coordinator_direction,
        "plan": coordinator_plan,
        "milestone": coordinator_milestone,
        "note": coordinator_note,
        "status": coordinator_status,
    }[command](args, project)
