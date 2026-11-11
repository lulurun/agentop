"""A transcript as a conversation: what the user typed and what the agent wrote back.

Each agent tool writes its sessions as JSONL in its own shape, so each tool's
module in agentop.agentclis has the reader for it (`AgentCli.conversation_reader`).
A reader turns one record into the messages it holds, keeping only text: the
user's prompts and the assistant's text. Tool calls, tool output, thinking, and
the text the tools inject (instructions, reminders, context) are left out.

This module has what every reader shares. The File viewer shows a transcript
this way, and the Memory plugin builds its messages table from it.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

import zstandard

if TYPE_CHECKING:
    from agentop.agentclis.base import AgentCli


@dataclass
class Message:
    record: str
    role: str
    text: str
    at: float | None = None
    session: str | None = None
    project: str | None = None
    turn: str | None = None


Reader = Callable[[dict, dict], Iterator[Message]]

# A transcript, compressed (Codex compresses older ones), or the old copy of one
# the tool rewrote (`name.jsonl.1800000000`).
TRANSCRIPT = re.compile(r"\.jsonl(\.zst)?(\.\d+)?$")


def epoch(stamp) -> float | None:
    """An ISO timestamp as seconds since the epoch, or None."""
    if not isinstance(stamp, str):
        return None
    try:
        return datetime.fromisoformat(stamp).timestamp()
    except ValueError:
        return None


def texts(content, kinds: tuple[str, ...]) -> list[str]:
    """The text of a message's content: a string, or the blocks of the given types."""
    if isinstance(content, str):
        return [content]
    if not isinstance(content, list):
        return []
    return [b["text"] for b in content if isinstance(b, dict) and b.get("type") in kinds and isinstance(b.get("text"), str)]


def compressed(path: Path) -> bool:
    return ".jsonl.zst" in path.name


def records(data: Iterator[bytes], context: dict, reader: Reader) -> Iterator[Message]:
    """The messages in lines of JSONL. A line that isn't JSON is skipped."""
    for line in data:
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict):
            yield from reader(record, context)


def read(agent: AgentCli | None, path: Path) -> list[Message] | None:
    """Every message in one transcript file, or None if it isn't a transcript the tool knows."""
    how = agent.conversation_reader(path) if agent else None
    if how is None:
        return None
    reader, context = how
    with open(path, "rb") as f:
        if compressed(path):
            data = zstandard.ZstdDecompressor().stream_reader(f).read().splitlines()
            return list(records(iter(data), context, reader))
        return list(records(f, context, reader))
