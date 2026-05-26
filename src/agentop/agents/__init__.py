from __future__ import annotations

from typing import Optional

from agentop.agents.antigravity import AntigravityAgent
from agentop.agents.base import BaseAgent
from agentop.agents.claude import ClaudeAgent
from agentop.agents.codex import CodexAgent
from agentop.agents.gemini import GeminiAgent

# Order matters: more specific entries first
AGENTS: list[BaseAgent] = [
    ClaudeAgent(),
    CodexAgent(),
    GeminiAgent(),
    AntigravityAgent(),
]


def get_agent(name: str) -> Optional[BaseAgent]:
    """Return the registered agent with the given name, or None."""
    return next((a for a in AGENTS if a.name == name), None)
