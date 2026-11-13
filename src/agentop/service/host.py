"""Get the host ready as the service starts: find the agent tools, and install agentop's skills for them."""

from __future__ import annotations

import logging

from agentop import agentclis, skills
from agentop.agentclis import AgentCli

LOG = logging.getLogger(__name__)


class NoAgentTools(RuntimeError):
    pass


def prepare() -> list[AgentCli]:
    """Return the installed tools, after installing or updating the skills for each.

    Raises NoAgentTools when none is installed: the service could start no session.
    """
    tools = agentclis.installed()
    if not tools:
        wanted = ", ".join(f"{a.name} ({a.binary})" for a in agentclis.AGENTS)
        raise NoAgentTools(f"No agent tool found on this host's PATH. Install at least one of: {wanted}.")
    LOG.info("Agent tools: %s", ", ".join(a.name for a in tools))
    for agent in tools:
        root = agent.skills_dir()
        if root is None:
            continue
        try:
            results = skills.install(root)
        except OSError as e:
            # Sessions still work without the skills.
            LOG.warning("Could not install skills for %s in %s: %s", agent.name, root, e)
            continue
        for r in results:
            if r.action == skills.SKIPPED:
                LOG.warning("Skill %s not installed for %s: %s is not agentop's copy", r.name, agent.name, r.path)
            elif r.action in (skills.INSTALLED, skills.UPDATED):
                LOG.info("Skill %s %s for %s in %s", r.name, r.action, agent.name, r.path)
    return tools
