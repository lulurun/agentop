from __future__ import annotations

from typing import Optional

from agentop.agentcli import AgentCli
from agentop.agentclis.antigravity import AntigravityAgentCli
from agentop.agentclis.claude import ClaudeAgentCli
from agentop.agentclis.codex import CodexAgentCli

# Order matters: more specific entries first
AGENTS: list[AgentCli] = [
    ClaudeAgentCli(),
    CodexAgentCli(),
    AntigravityAgentCli(),
]


def get_agent(name: str) -> Optional[AgentCli]:
    """Return the registered agent with the given name, or None."""
    return next((a for a in AGENTS if a.name == name), None)
