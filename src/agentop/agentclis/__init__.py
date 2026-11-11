"""The supported agent tools, one module each. See base.py: each module is the one place for its tool's differences."""

from __future__ import annotations

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
