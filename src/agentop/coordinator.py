"""The Coordinator: the session working in a project's folder that the owner talks to. See docs/project.md, section 4."""

from __future__ import annotations

from pathlib import Path

from agentop import projects

# Codex first, then Claude, then whatever else is installed.
PREFERRED = ("codex", "claude")

_REPO = Path(__file__).resolve().parents[2]

DEFAULT_WELCOME = (
    "open a new session for a task, look at what the sessions are doing, "
    "fix agentop, update it, and restart it"
)


def _default_part() -> str:
    repo = f" Its code is in `{_REPO}`." if (_REPO / ".git").exists() else ""
    return (
        "This is the default project, the one the Sessions feature shows. You are the owner's general Coordinator:\n\n"
        "- Open a new session for a task, in the repo it is about, and give it the task.\n"
        "- Look at what the sessions are doing (`agentop list`, `agentop project show`) and sum it up.\n"
        f"- Fix agentop itself.{repo}\n"
        "- Update it (`agentop service update`) and restart it (`agentop service restart`). Sessions keep running across both."
    )


def pick_tool(available: list[str]) -> str | None:
    for tool in PREFERRED:
        if tool in available:
            return tool
    return available[0] if available else None


def instructions(project: dict) -> str:
    """The Coordinator's AGENTS.md."""
    name, folder = project["name"], project["folder"]
    part = project.get("instructions") or (_default_part() if name == projects.DEFAULT else "")
    return f"""# Coordinator of {name}

You are the Coordinator of the agentop project `{name}`. The owner talks to you: you work out with them what they want, sum it up, and route the work to the project's other sessions, starting one when it is needed or when asked. The owner may also talk to those sessions directly.

## This folder

`{folder}` is the project folder. Everything of the project's that isn't in a git repo goes here: briefs, requirements, plans, decisions, notes, hand-over files between sessions. Code stays in its repos. `.project/` is agentop's own record: don't edit it.

## Sessions

- `agentop project show` lists this project's sessions.
- `agentop start <name> --tool <codex|claude|antigravity> --cwd <dir> --prompt "<task>"` starts one in this project. A name is unique in the project, so plain names do: `dev`, `docs`, `deploy`. The session is told about this folder and about you.
- `agentop send <name> "<prompt>"` types a prompt into one. When you need a result back, ask it to answer with `agentop send {projects.COORDINATOR} "<result>"`.
- `agentop stop <name>` stops one.

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


def welcome_prompt(project: dict) -> str:
    welcome = project.get("welcome") or (DEFAULT_WELCOME if project["name"] == projects.DEFAULT else "")
    asks = f" Say what they can ask you, as \"You can ask me to …\": {welcome}." if welcome else ""
    return (
        f"You have just been started as the Coordinator of the agentop project `{project['name']}`; "
        "AGENTS.md in this folder says what that means. Write the owner a short welcome, a few lines: "
        f"who you are and what this project's folder is for.{asks} Then wait for their request."
    )


def member_header(project: dict, name: str) -> str:
    """What a session other than the Coordinator is told first, before its task."""
    return (
        f"[agentop project {project['name']}] You are `{name}`, a session of the agentop project `{project['name']}`. "
        f"Its folder is {project['folder']}: files that aren't for a repo (notes, plans, hand-overs) go there, "
        f"and other sessions read them there. The project's Coordinator is the session called `{projects.COORDINATOR}`; "
        f'reach it with `agentop send {projects.COORDINATOR} "…"`.'
    )
