from __future__ import annotations

from agentop.actor import Actor
from agentop.tmux import Session


class CodexActor(Actor):
    """Actor for codex: uses bracketed paste so bare newlines don't submit prematurely."""

    def send(self, text: str) -> None:
        Session.paste_text_bracketed(self.session, text)
        Session.send_keys(self.session, "Enter")
