"""The Coordinator: the session working in a project's folder that the owner talks to. `default` has none.

See docs/0_13_projects.md, section 4.
"""

from __future__ import annotations

from pathlib import Path

from agentop import projects
from agentop.coordinator_skill import PATH as COORDINATOR_SKILL_SOURCE
from agentop.coordinator_skill import body as coordinator_skill
from agentop.coordinator_skill import rules as coordinator_rules

# Codex first, then Claude, then whatever else is installed.
PREFERRED = ("codex", "claude")

COORDINATOR_SKILL_FILE = "COORDINATOR_SKILL.md"
RULES_BEGIN = "<!-- agentop:coordinator-rules begin: agentop rewrites this block -->"
RULES_END = "<!-- agentop:coordinator-rules end -->"


def _tracked(project: dict) -> bool:
    """A Projects project's Coordinator works under the rules, tasks and review; a dialogue's doesn't."""
    return project.get("owner") == "projects"


def _rules_block() -> str:
    return f"{RULES_BEGIN}\n{coordinator_rules()}\n{RULES_END}"


def with_rules(text: str) -> str:
    """AGENTS.md with agentop's rules block replaced, or added at the end; the rest stays the owner's."""
    start, end = text.find(RULES_BEGIN), text.find(RULES_END)
    if start != -1 and end > start:
        return text[:start] + _rules_block() + text[end + len(RULES_END):]
    return text.rstrip() + "\n\n" + _rules_block() + "\n"


def pick_tool(available: list[str]) -> str | None:
    for tool in PREFERRED:
        if tool in available:
            return tool
    return available[0] if available else None


def _skill_part(project: dict) -> str:
    if not _tracked(project):
        return ""
    return (
        "\n\nThe following project-coordinator skill applies only to this Coordinator session:\n\n"
        f"<project-coordinator-skill>\n{coordinator_skill()}\n</project-coordinator-skill>"
    )


def instructions(project: dict) -> str:
    """The Coordinator's AGENTS.md."""
    name, folder = project["name"], project["folder"]
    part = project.get("instructions") or ""
    skill_reference = (
        f"\n## Coordinator skill\n\nOnly the project session named `{projects.COORDINATOR}` reads "
        f"`{folder}/{COORDINATOR_SKILL_FILE}`. If you are the Coordinator, read it completely at startup and "
        "after clearing context. Every other session must not load it.\n"
        if _tracked(project)
        else ""
    )
    start_line = (
        "- `agentop coordinator task start --action <M…A…> <name> --tool <codex|claude|antigravity> --cwd <dir> --goal … "
        "--accept … --artifact …` starts a session on a task of the plan; it reports back with `agentop task report`. "
        "`agentop coordinator --help` has the rest."
        if _tracked(project)
        else "- `agentop start <name> --tool <codex|claude|antigravity> --cwd <dir> --prompt \"<task>\"` starts one in this "
        "project. A name is unique in the project, so plain names do: `dev`, `docs`, `deploy`. The session is told about "
        f"this folder and about you. When you need a result back, ask it to answer with `agentop send {projects.COORDINATOR} \"<result>\"`."
    )
    record = (
        "`track/` holds the direction, plan and execution record that `agentop coordinator` writes, and `.project/` is "
        "agentop's own record: edit neither by hand, except the current `track/<N>/DIRECTION.md`."
        if _tracked(project)
        else "`.project/` is agentop's own record: don't edit it."
    )
    text = f"""# Coordinator of {name}

You are the Coordinator of the agentop project `{name}`. The owner talks to you: you work out with them what they want, sum it up, and route the work to the project's other sessions, starting one when it is needed or when asked. The owner may also talk to those sessions directly.

## This folder

`{folder}` is the project folder. Keep briefs, requirements, plans, decisions, notes, and hand-over files here. Keep every source tree in `{folder}/code/<source-tree>`, including its Git repository. {record}

## Sessions

- `agentop project show` lists this project's sessions.
{start_line}
- `agentop send <name> "<prompt>"` types a prompt into one.
- `agentop stop <name>` stops one.
{skill_reference}

## Your part

{part}
""".rstrip() + "\n"
    return with_rules(text) if _tracked(project) else text


def write_instructions(project: dict) -> None:
    """AGENTS.md and CLAUDE.md in the project folder, unless they are there already: they are the owner's to edit."""
    folder = Path(project["folder"])
    agents = folder / "AGENTS.md"
    if not agents.exists():
        agents.write_text(instructions(project))
    elif _tracked(project):
        current = agents.read_text()
        updated = with_rules(current)
        if updated != current:
            agents.write_text(updated)
    claude = folder / "CLAUDE.md"
    if not claude.exists():
        claude.write_text("@AGENTS.md\n")
    if _tracked(project):
        from agentop.plugins.projects import track

        (folder / COORDINATOR_SKILL_FILE).write_text(COORDINATOR_SKILL_SOURCE.read_text())
        track.write_first(project["name"])


def welcome_prompt(project: dict) -> str:
    """One line: a prompt with newlines would be sent in parts."""
    welcome = project.get("welcome") or ""
    hello = "Hi, I'm the Coordinator of this project."
    unsaid = "Don't name the project or explain its folder; the owner knows what a project is."
    asks = f" Then \"You can ask me to:\" and a bulleted list, one short line each, of: {welcome}" if welcome else ""
    wait = (
        " Before you end the turn, run: agentop coordinator yield owner --question \"What should this project achieve?\" "
        "--recommend \"Tell me the outcome and any constraints; I'll draft the brief and the plan\" "
        "--default \"Nothing to pursue until there is a goal\". Then wait for their request."
        if _tracked(project)
        else " Then wait for their request."
    )
    return (
        "You have just been started as a Coordinator in agentop; AGENTS.md in this folder says what that means. "
        f"Write the owner a short welcome. Start with \"{hello}\"{asks}. {unsaid}{wait}"
        f"{_skill_part(project)}"
    )


def resume_prompt(project: dict) -> str:
    """Reload the Coordinator-only skill when a project Coordinator resumes."""
    skill = _skill_part(project)
    if not skill:
        return ""
    return (
        "You are resuming this project's Coordinator session. Reload and follow the Coordinator skill and the rules in "
        "AGENTS.md, run agentop coordinator status, then carry on toward the direction."
        f"{skill}"
    )


def first_prompt(project: dict) -> str:
    """The welcome, or the resume prompt when the folder already has a track in use (a project re-created on a kept folder)."""
    if _tracked(project):
        from agentop.plugins.projects import track

        if track.started(project["name"]):
            return resume_prompt(project)
    return welcome_prompt(project)


def member_header(project: dict, name: str) -> str:
    """What a session other than the Coordinator is told first, before its task."""
    return (
        f"[agentop project {project['name']}] You are `{name}`, a session of the agentop project `{project['name']}`. "
        f"Its folder is {project['folder']}: source repositories are under its `code/` directory, and project notes, "
        f"plans, and hand-overs go in the project folder, "
        f"and other sessions read them there. The project's Coordinator is the session called `{projects.COORDINATOR}`; "
        f'reach it with `agentop send {projects.COORDINATOR} "…"`.'
    )


def start_now(project: dict, available: list[str]) -> str | None:
    """Start a new Coordinator without the service, as `agentop project create` does. Returns its tmux name."""
    from agentop import ops

    tool = pick_tool(available)
    if tool is None:
        return None
    write_instructions(project)
    name = ops.start(tool, project["folder"], projects.COORDINATOR, project=project["name"]).name
    ops.first_prompt(name, first_prompt(project), project["name"], projects.COORDINATOR)
    return name
