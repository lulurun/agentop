"""`agentop review`: how well the Coordinator pursued, measured from the project's event log, as a report for it and the owner."""

from __future__ import annotations

import statistics
import time
from pathlib import Path

from agentop import projects

from . import pursuit

PASSIVE_LIMIT = 0.2
OWNER_LIMIT = 0.4
FIRST_ROUND_LIMIT = 0.5
STALE_TASK_SECONDS = 2 * 3600
# docs/0_13_projects.md 4.1, the fixed standard: keep the two in step.
CRITERIA = (
    "keep the objective in view?",
    "recognize the uncertainty?",
    "choose reasonably from the information available at the time?",
    "bound the risk?",
    "check the evidence?",
    "learn from what happened?",
    "continue, stopping only for a real terminal condition?",
)


def _ratio(part: int, whole: int) -> float | None:
    return part / whole if whole else None


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{round(value * 100)}%"


def _when(at: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(at))


def measure(project: str, since: float, now: float | None = None) -> dict:
    """The numbers for events at or after `since`, and the tasks touched then."""
    now = time.time() if now is None else now
    window = [e for e in pursuit.events(project, since) if e["at"] <= now]
    yields = [e for e in window if e["kind"] == "yield"]
    nudges = [e for e in window if e["kind"] == "nudge"]
    by_kind = {k: sum(1 for e in yields if e.get("type") == k) for k in pursuit.KINDS}
    turn_ends = len(yields) + len(nudges)

    tasks = pursuit.load_tasks(project)
    closed = [t for t in tasks if t["status"] != "open" and t.get("closed_at", 0) >= since]
    accepted = [t for t in closed if t["status"] == "accepted"]
    first_round = [t for t in accepted if len(t["rounds"]) == 1]
    report_minutes = [
        (r["reported_at"] - r["sent_at"]) / 60
        for t in tasks
        for r in t["rounds"]
        if r.get("reported_at") and r["reported_at"] >= since
    ]
    stale = [
        t for t in tasks
        if pursuit.awaiting_report(t) and now - t["rounds"][-1]["sent_at"] > STALE_TASK_SECONDS
    ]
    return {
        "since": since,
        "until": now,
        "turn_ends": turn_ends,
        "yields": by_kind,
        "passive": len(nudges),
        "ignored_nudges": sum(1 for e in window if e["kind"] == "nudge.ignored"),
        "passive_rate": _ratio(len(nudges), turn_ends),
        "owner_rate": _ratio(by_kind["owner"], turn_ends),
        "opened": sum(1 for e in window if e["kind"] == "task.open"),
        "accepted": len(accepted),
        "failed": len(closed) - len(accepted),
        "first_round_rate": _ratio(len(first_round), len(accepted)),
        "rounds": {n: sum(1 for t in closed if len(t["rounds"]) == n) for n in range(1, pursuit.MAX_ROUNDS + 1)},
        "report_minutes": round(statistics.median(report_minutes), 1) if report_minutes else None,
        "blocked_reports": sum(1 for e in window if e["kind"] == "task.report" and e.get("blocked")),
        "quiet": sum(1 for e in window if e["kind"] == "task.quiet"),
        "untracked": [e["session"] for e in window if e["kind"] == "start.untracked"],
        "open": [t["id"] for t in tasks if t["status"] == "open"],
        "stale": [t["id"] for t in stale],
        "owner_asks": [e for e in yields if e.get("type") == "owner"],
        "blocks": [e for e in yields if e.get("type") == "blocked"],
        "closed_tasks": closed,
        "state_problems": pursuit.state_problems(project),
    }


def findings(m: dict) -> list[tuple[str, str, str]]:
    """(severity, what, what to do), most severe first."""
    found = []
    if m["ignored_nudges"]:
        found.append(("high", f"{m['ignored_nudges']} times the Coordinator ignored two nudges in a row",
                      "Every turn ends with agentop yield; a nudge is answered by acting or yielding, never by silence."))
    if m["passive"] and (m["passive_rate"] or 0) > PASSIVE_LIMIT:
        found.append(("high", f"{m['passive']} of {m['turn_ends']} turns ended without a stop reason ({_pct(m['passive_rate'])})",
                      "Before ending a turn, name the next action and take it; stop only for done, waiting, owner or blocked."))
    elif m["passive"]:
        found.append(("medium", f"{m['passive']} turns ended without a stop reason", "Yield at the end of every turn."))
    if m["turn_ends"] >= 3 and (m["owner_rate"] or 0) > OWNER_LIMIT:
        found.append(("high", f"{_pct(m['owner_rate'])} of turns handed back to the owner",
                      "For each question below, ask: was the default good enough to proceed on? If so, proceed next time."))
    weak_defaults = [e for e in m["owner_asks"] if e.get("default", "").lower().strip(" .") in ("wait", "nothing", "none", "stop")]
    if weak_defaults:
        found.append(("medium", f"{len(weak_defaults)} owner questions had no default but waiting",
                      "A default is an action you take if there is no answer; give one unless the choice is truly the owner's."))
    if m["accepted"] >= 3 and (m["first_round_rate"] or 0) < FIRST_ROUND_LIMIT:
        found.append(("medium", f"Only {_pct(m['first_round_rate'])} of accepted tasks passed in round 1",
                      "Briefs are under-specified: sharpen the goal, criteria and what to verify before starting."))
    if m["failed"]:
        found.append(("medium", f"{m['failed']} tasks failed", "Note in STATE.md why, and whether the split or the tool choice was wrong."))
    if m["quiet"]:
        found.append(("medium", f"{m['quiet']} times a session went quiet without reporting",
                      "Check its screen sooner (permission prompts, questions); say in briefs when to report blocked."))
    if m["stale"]:
        found.append(("medium", f"Tasks waiting over 2 hours for a report: {', '.join(m['stale'])}",
                      "Look at those sessions now; fail and replan if they are stuck."))
    if m["untracked"]:
        found.append(("low", f"Sessions started outside agentop task: {', '.join(m['untracked'])}",
                      "Start delegated work with agentop task start so it has criteria, an artifact and rounds."))
    for problem in m["state_problems"]:
        found.append(("medium", problem, "STATE.md is what a fresh context reads first; keep it current."))
    order = {"high": 0, "medium": 1, "low": 2}
    return sorted(found, key=lambda f: order[f[0]])


def _trend(m: dict, before: dict | None) -> list[str]:
    if not before:
        return ["First review: no trend yet."]
    lines = []
    for key, label in (("passive_rate", "Passive turn ends"), ("owner_rate", "Handed to the owner"), ("first_round_rate", "Accepted in round 1")):
        lines.append(f"- {label}: {_pct(before.get(key))} → {_pct(m.get(key))}")
    return lines


def render(project: str, m: dict, found: list, before: dict | None) -> str:
    y = m["yields"]
    lines = [
        f"# Coordinator review of {project}, {_when(m['until'])}",
        "",
        f"Window: {_when(m['since']) if m['since'] else 'the start'} to {_when(m['until'])}.",
        "",
        "This measures pursuit (did the Coordinator keep choosing and taking useful next actions), not how good the results are.",
        "",
        "## Numbers",
        "",
        f"- Turn ends: {m['turn_ends']}. Done {y['done']}, waiting {y['waiting']}, owner {y['owner']}, blocked {y['blocked']}, "
        f"without a reason {m['passive']} ({_pct(m['passive_rate'])})",
        f"- Tasks: {m['opened']} opened, {m['accepted']} accepted, {m['failed']} failed, {len(m['open'])} open. "
        f"Accepted in round 1: {_pct(m['first_round_rate'])}. "
        f"Rounds used: " + ", ".join(f"{n}: {c}" for n, c in m["rounds"].items()),
        f"- Median time to a report: {m['report_minutes'] if m['report_minutes'] is not None else '—'} min. "
        f"Blocked reports: {m['blocked_reports']}. Quiet without a report: {m['quiet']}",
        "",
        "## Trend since the last review",
        "",
        *_trend(m, before),
        "",
        "## Findings",
        "",
    ]
    lines += [f"- **{sev}**: {what}. {todo}" for sev, what, todo in found] or ["- None."]
    lines += ["", "## Questions handed to the owner", ""]
    lines += [
        f"- {_when(e['at'])}: {e.get('question')} Recommended: {e.get('recommend')} Default: {e.get('default')}"
        for e in m["owner_asks"]
    ] or ["- None."]
    if m["blocks"]:
        lines += ["", "## Blocked", ""]
        lines += [f"- {_when(e['at'])}: needs {e.get('need')}; tried {e.get('tried')}" for e in m["blocks"]]
    lines += ["", "## Tasks closed", ""]
    lines += [
        f"- {t['id']} `{t['session']}` ({t['tool']}), {t['status']} after {len(t['rounds'])} round(s): {t['goal']}"
        + (f" Checked: {t['checked']}" if t.get("checked") else f" Note: {t.get('note', '')}")
        for t in m["closed_tasks"]
    ] or ["- None."]
    lines += [
        "",
        "## Coordinator's reflection",
        "",
        pursuit.REFLECTION_PLACEHOLDER,
        "",
        "Judge your pursuit, not the results. For this stretch of work, did you:",
        "",
        *[f"{n}. {q}" for n, q in enumerate(CRITERIA, 1)],
        "",
        "Results are feedback for calibration, not hindsight proof. Which stops were premature? "
        "Write one concrete change you will make, and keep it in STATE.md under Decisions.",
        "",
    ]
    return "\n".join(lines)


def run(project: str, since: float | None = None, write: bool = True) -> tuple[str, Path | None, dict]:
    """Measure, render, and (with write) save the report and log the review. Returns (text, path, numbers)."""
    previous = [e for e in pursuit.events(project) if e["kind"] == "review"]
    before = previous[-1].get("metrics") if previous else None
    if since is None:
        since = previous[-1]["at"] if previous else 0.0
    m = measure(project, since)
    found = findings(m)
    text = render(project, m, found, before)
    path = None
    if write:
        folder = projects.folder(project) / pursuit.REVIEWS_DIR
        folder.mkdir(exist_ok=True)
        path = folder / f"{time.strftime('%Y-%m-%d-%H%M%S', time.localtime(m['until']))}.md"
        path.write_text(text)
        keep = ("turn_ends", "passive", "passive_rate", "owner_rate", "first_round_rate", "accepted", "failed")
        pursuit.log(project, "review", path=str(path), findings=len(found), metrics={k: m[k] for k in keep})
    return text, path, m
