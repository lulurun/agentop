from __future__ import annotations

from typing import Optional

from agentop.agentcli import AgentCli
from agentop.agentclis.antigravity import AntigravityAgent
from agentop.agentclis.claude import ClaudeAgent
from agentop.agentclis.codex import CodexAgent
from agentop.agentclis.gemini import GeminiAgent

# Order matters: more specific entries first
AGENTS: list[AgentCli] = [
    ClaudeAgent(),
    CodexAgent(),
    GeminiAgent(),
    AntigravityAgent(),
]


def get_agent(name: str) -> Optional[AgentCli]:
    """Return the registered agent with the given name, or None."""
    return next((a for a in AGENTS if a.name == name), None)
