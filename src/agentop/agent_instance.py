"""AgentInstance: a running agent process bound to a tmux session with an Actor."""

from __future__ import annotations

import threading
import time

import psutil

from agentop.actors import get_actor
from agentop.agentcli import AgentCli
from agentop.tmux import Session

# Ctrl-C clears what's typed in the agent's input (and interrupts a running
# turn), so /exit lands on an empty line instead of joining someone's draft.
_CLEAR_SETTLE = 0.5
# How long the agent gets to quit after /exit before its tmux session is killed.
_EXIT_TIMEOUT = 10.0
_POLL = 0.2


class AgentInstance:
    def __init__(self, agent: AgentCli, session: str, name: str = ""):
        self.agent = agent
        self.session = session
        self.actor = get_actor(agent.name)(
            session=session,
            name=name,
            idle_seconds=agent.idle_seconds,
        )

    def send_command(self, text: str) -> None:
        """Send a raw operational command to the agent process (e.g. /exit)."""
        Session.send_keys(self.session, text, "Enter")

    def stop(self) -> dict:
        """Ask the agent to quit, wait for it to exit, then kill the tmux session.

        Returns {"sent_exit", "exited", "forced", "tmux_killed"}. "forced" means the
        agent was still running when the session was killed.
        """
        sent_exit = False
        exited = True
        if Session.has(self.session) and self.agent_running():
            Session.send_keys(self.session, "C-c")
            exited = self._wait_for_exit(_CLEAR_SETTLE)
            if not exited:
                self.send_command("/exit")
                sent_exit = True
                exited = self._wait_for_exit(_EXIT_TIMEOUT)

        tmux_killed = False
        if Session.has(self.session):
            Session.kill(self.session)
            tmux_killed = True

        return {"sent_exit": sent_exit, "exited": exited, "forced": not exited, "tmux_killed": tmux_killed}

    def agent_running(self) -> bool:
        """True while the shell in the session's pane still has a live child: the agent."""
        for pane_pid in Session.list_panes(self.session):
            try:
                children = psutil.Process(int(pane_pid)).children()
            except (ValueError, psutil.Error):
                continue
            for child in children:
                try:
                    if child.status() != psutil.STATUS_ZOMBIE:
                        return True
                except psutil.Error:
                    pass
        return False

    def _wait_for_exit(self, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while True:
            if not self.agent_running():
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(_POLL)

    def attach(self, stop_event: threading.Event) -> AgentInstance:
        self.actor.attach(stop_event)
        return self
