"""AgentInstance: a running agent process bound to a tmux session with an Actor."""

from __future__ import annotations

import threading
import time

from agentop.actor import Actor
from agentop.agentcli import AgentCli
from agentop.tmux import Session

_EXIT_WAIT = 3.0


class AgentInstance:
    def __init__(self, agent: AgentCli, session: str, name: str = ""):
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

    def stop(self) -> dict:
        """Send /exit, wait for the process to quit, then kill the tmux session."""
        sent_exit = False
        if Session.has(self.session):
            self.send_command("/exit")
            sent_exit = True
            time.sleep(_EXIT_WAIT)

        tmux_killed = False
        if Session.has(self.session):
            Session.kill(self.session)
            tmux_killed = True

        return {"sent_exit": sent_exit, "tmux_killed": tmux_killed}

    def attach(self, stop_event: threading.Event) -> AgentInstance:
        self.actor.attach(stop_event)
        return self
