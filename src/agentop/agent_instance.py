"""AgentInstance: a running agent process bound to a tmux session with an Actor."""

from __future__ import annotations

import threading

from agentop.actor import Actor
from agentop.agents.base import BaseAgent
from agentop.tmux import Session


class AgentInstance:
    def __init__(self, agent: BaseAgent, session: str, name: str = ""):
        self.agent = agent
        self.session = session
        self.actor = Actor(
            session=session,
            name=name,
            idle_seconds=agent.idle_seconds,
            use_bracketed_paste=agent.use_bracketed_paste,
        )

    def send_command(self, text: str) -> None:
        """Send a raw operational command to the agent process (e.g. /exit)."""
        Session.send_keys(self.session, text, "Enter")

    def stop(self) -> None:
        self.send_command("/exit")

    def attach(self, stop_event: threading.Event) -> AgentInstance:
        self.actor.attach(stop_event)
        return self
