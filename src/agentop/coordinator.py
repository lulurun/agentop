"""The Coordinator: the session working in a project's folder that the owner talks to. See docs/project.md, section 4."""

from __future__ import annotations

from pathlib import Path

from agentop import projects
from agentop.coordinator_skill import PATH as COORDINATOR_SKILL_SOURCE
from agentop.coordinator_skill import body as coordinator_skill

# Codex first, then Claude, then whatever else is installed.
PREFERRED = ("codex", "claude")

_REPO = Path(__file__).resolve().parents[2]

DEFAULT_WELCOME = (
    "start a session for a task; ask a session to do something and bring back its answer; "
    "tell you what the sessions are doing; fix a problem with agentop; update and restart agentop; "
    "show you how to use agentop"
)
COORDINATOR_SKILL_FILE = "COORDINATOR_SKILL.md"


def _default_part() -> str:
    repo = f" Its code is in `{_REPO}`." if (_REPO / ".git").exists() else ""
    return (
        "This is the default project, the one the Sessions feature shows. You are the owner's general Coordinator:\n\n"
        "- Open a new session for a task, in the repo it is about, and give it the task.\n"
        "- Look at what the sessions are doing (`agentop list`, `agentop project show`) and sum it up.\n"
        f"- Fix agentop itself.{repo}\n"
        "- Update it (`agentop service update`) and restart it (`agentop service restart`). Sessions keep running across both.\n"
        "- Show the owner how to use agentop: `agentop --help`, and the docs in its repo."
    )


def pick_tool(available: list[str]) -> str | None:
    for tool in PREFERRED:
        if tool in available:
            return tool
    return available[0] if available else None


def _skill_part(project: dict) -> str:
    if project.get("owner") != "projects":
        return ""
    return (
        "\n\nThe following project-coordinator skill applies only to this Coordinator session:\n\n"
        f"<project-coordinator-skill>\n{coordinator_skill()}\n</project-coordinator-skill>"
    )


def instructions(project: dict) -> str:
    """The Coordinator's AGENTS.md."""
    name, folder = project["name"], project["folder"]
    part = project.get("instructions") or (_default_part() if name == projects.DEFAULT else "")
    skill_reference = (
        f"\n## Coordinator skill\n\nOnly the project session named `{projects.COORDINATOR}` reads "
        f"`{folder}/{COORDINATOR_SKILL_FILE}`. If you are the Coordinator, read it completely at startup and "
        "after clearing context. Every other session must not load it.\n"
        if project.get("owner") == "projects"
        else ""
    )
    return f"""# Coordinator of {name}

You are the Coordinator of the agentop project `{name}`. The owner talks to you: you work out with them what they want, sum it up, and route the work to the project's other sessions, starting one when it is needed or when asked. The owner may also talk to those sessions directly.

## This folder

`{folder}` is the project folder. Keep briefs, requirements, plans, decisions, notes, and hand-over files here. Keep every source tree in `{folder}/code/<source-tree>`, including its Git repository. `.project/` is agentop's own record: don't edit it.

## Sessions

- `agentop project show` lists this project's sessions.
- `agentop start <name> --tool <codex|claude|antigravity> --cwd <dir> --prompt "<task>"` starts one in this project. A name is unique in the project, so plain names do: `dev`, `docs`, `deploy`. The session is told about this folder and about you.
- `agentop send <name> "<prompt>"` types a prompt into one. When you need a result back, ask it to answer with `agentop send {projects.COORDINATOR} "<result>"`.
- `agentop stop <name>` stops one.
{skill_reference}

## Your part

{part}
""".rstrip() + "\n"


def write_instructions(project: dict) -> None:
    """AGENTS.md and CLAUDE.md in the project folder, unless they are there already: they are the owner's to edit."""
    folder = Path(project["folder"])
    agents = folder / "AGENTS.md"
    if not agents.exists():
        agents.write_text(instructions(project))
    claude = folder / "CLAUDE.md"
    if not claude.exists():
        claude.write_text("@AGENTS.md\n")
    if project.get("owner") == "projects":
        (folder / COORDINATOR_SKILL_FILE).write_text(COORDINATOR_SKILL_SOURCE.read_text())


def welcome_prompt(project: dict) -> str:
    """One line: a prompt with newlines would be sent in parts."""
    default = project["name"] == projects.DEFAULT
    welcome = project.get("welcome") or (DEFAULT_WELCOME if default else "")
    hello = "Hi, I'm the Coordinator." if default else "Hi, I'm the Coordinator of this project."
    unsaid = (
        "Don't mention projects, folders or how agentop works inside; speak from the owner's side."
        if default
        else "Don't name the project or explain its folder; the owner knows what a project is."
    )
    asks = f" Then \"You can ask me to:\" and a bulleted list, one short line each, of: {welcome}" if welcome else ""
    return (
        "You have just been started as a Coordinator in agentop; AGENTS.md in this folder says what that means. "
        f"Write the owner a short welcome. Start with \"{hello}\"{asks}. {unsaid} Then wait for their request."
        f"{_skill_part(project)}"
    )


def resume_prompt(project: dict) -> str:
    """Reload the Coordinator-only skill when a project Coordinator resumes."""
    skill = _skill_part(project)
    if not skill:
        return ""
    return f"You are resuming this project's Coordinator session. Reload and follow the Coordinator skill.{skill}"


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
    ops.first_prompt(name, welcome_prompt(project), project["name"], projects.COORDINATOR)
    return name
