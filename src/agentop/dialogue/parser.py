"""ResponseParser: extracts dialogue messages from raw scrollback using the BEGIN/END protocol."""

from __future__ import annotations

import logging
import re

from agentop.dialogue.status import ReceiveStatus

LOG = logging.getLogger(__name__)


class ResponseParser:
    def parse(self, raw: str, name: str, turn: int, nonce: str) -> tuple[str, ReceiveStatus]:
        """Extract the BEGIN/END delimited block matching name, turn, and nonce.

        Scans lines bottom-up: collect lines once END is found, stop and return
        once BEGIN is found.  Bottom-up scan means the last complete block wins.
        Uses 'in' rather than startswith so a leading '●' from Claude Code's
        renderer does not break the match.
        """
        begin_tag = f"--- BEGIN {name} turn:{turn} nonce:{nonce}"
        end_tag = f"--- END {name} turn:{turn} nonce:{nonce}"

        buffer: list[str] = []
        in_block = False

        for line in reversed(raw.splitlines()):
            stripped = line.strip()
            if not in_block:
                if end_tag.lower() in stripped.lower():
                    in_block = True
            else:
                if begin_tag.lower() in stripped.lower():
                    sm = re.search(r"status:(\w+)", stripped, re.IGNORECASE)
                    receive_status = (
                        ReceiveStatus.COMPLETE
                        if (sm and sm.group(1).lower() == ReceiveStatus.COMPLETE)
                        else ReceiveStatus.CONTINUE
                    )
                    body = "\n".join(reversed(buffer)).strip()
                    LOG.info("[%s] turn:%d status:%s body_len:%d", name, turn, receive_status, len(body))
                    return (body, receive_status)
                else:
                    buffer.append(line)

        LOG.warning("[%s] turn:%d nonce:%s — delimiters not found", name, turn, nonce)
        return ("", ReceiveStatus.DELIMITER_NOT_FOUND_WILL_RETRY)
