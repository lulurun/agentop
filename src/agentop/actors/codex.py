from __future__ import annotations

import time

from agentop.actor import Actor
from agentop.tmux import Session

# tmux paste-buffer returning only confirms the paste was queued, not that
# codex's CLI has finished ingesting it from the pty. Sending Enter immediately
# races that ingestion for large prompts (e.g. the turn-1 role+brief message)
# and can submit before the paste lands, silently dropping it. A short settle
# delay scaled to payload size avoids that race.
_BASE_SETTLE_SECONDS = 0.5
_SETTLE_SECONDS_PER_KB = 0.05


class CodexActor(Actor):
    """Actor for codex: uses bracketed paste so bare newlines don't submit prematurely."""

    def send(self, text: str) -> None:
        Session.paste_text_bracketed(self.session, text)
        settle = _BASE_SETTLE_SECONDS + _SETTLE_SECONDS_PER_KB * (len(text) / 1024)
        time.sleep(settle)
        Session.send_keys(self.session, "Enter")
