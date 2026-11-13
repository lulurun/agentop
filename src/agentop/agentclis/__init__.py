"""The supported agent tools, one module each. See base.py: each module is the one place for its tool's differences."""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from collections.abc import Iterable
from typing import Optional

from agentop.agentclis.antigravity import AntigravityAgentCli
from agentop.agentclis.base import AgentCli
from agentop.agentclis.claude import ClaudeAgentCli
from agentop.agentclis.codex import CodexAgentCli

# The tool used when none is named.
DEFAULT = "claude"

# Order matters: more specific entries first
AGENTS: list[AgentCli] = [
    ClaudeAgentCli(),
    CodexAgentCli(),
    AntigravityAgentCli(),
]


def get_agent(name: str) -> Optional[AgentCli]:
    """Return the registered agent with the given name, or None."""
    return next((a for a in AGENTS if a.name == name), None)


def names() -> list[str]:
    """The tools' names, as the CLI and the API take them."""
    return [a.name for a in AGENTS]


# Prefixes each found program in the shell's output, which also holds whatever
# the user's rc files print.
_FOUND = "__agentop_found__ "
_SHELL_TIMEOUT = 15


def installed(agents: Iterable[AgentCli] | None = None) -> list[AgentCli]:
    """The tools whose program can be run on this host.

    Sessions are typed into the user's own shell, so that is where to look: the
    service's PATH is often shorter (a systemd unit's is). A program found by
    either counts.

    Each one found remembers where, in `executable`, for running it outside tmux.
    """
    agents = list(AGENTS if agents is None else agents)
    binaries = {agent.binary for agent in agents}
    found = {b: path for b in binaries if (path := shutil.which(b))}
    # The shell's answer wins: it is the program a session would run.
    found.update(_found_by_shell(binaries))
    for agent in agents:
        if agent.binary in found:
            agent.executable = found[agent.binary]
    return [agent for agent in agents if agent.binary in found]


def _found_by_shell(binaries: Iterable[str]) -> dict[str, str]:
    """The programs a login, interactive shell of the user's finds on its PATH, and where."""
    shell = os.environ.get("SHELL") or "/bin/sh"
    script = "; ".join(
        f"p=$(command -v {shlex.quote(b)} 2>/dev/null) && echo {shlex.quote(_FOUND + b)} \"$p\""
        for b in sorted(binaries)
    )
    try:
        result = subprocess.run(
            [shell, "-lic", script],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=_SHELL_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return {}
    found = {}
    for line in result.stdout.splitlines():
        if line.startswith(_FOUND):
            binary, _, path = line[len(_FOUND) :].strip().partition(" ")
            # An alias or a function prints its name, not a path: run it by name then.
            found[binary] = path if path.startswith("/") else binary
    return found
