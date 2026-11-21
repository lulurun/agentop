"""Where a session's transcript lives: the agent tool's own conversation log.

Only the files the tool reports for that session can be listed or read, so a
request can name one of them but never reach anything else on disk.

When the tool has deleted them, a plugin's archive may still have them: the
Memory plugin offers one (design 12.11). An archive keeps the tool's own
layout under a folder per tool, so the same lookup finds them there.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path

from agentop import conversation
from agentop.agentclis import get_agent

MAX_FILES = 500

# Agent session IDs are UUID-like. Anything else is refused before it reaches a glob.
_SESSION_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")


# The archives on offer, from enabled plugins. Set by the service's plugin host; none without one.
archive_roots: Callable[[], list[Path]] = list


def _files(tool: str | None, session_id: str | None) -> tuple[Path | None, list[Path], bool]:
    """(base folder, files, whether they are the archive's copy)."""
    if not tool or not session_id or not _SESSION_ID.fullmatch(session_id):
        return None, [], False
    agent = get_agent(tool)
    if agent is None:
        return None, [], False
    for root, archived in ((None, False), *((archive / tool, True) for archive in archive_roots())):
        if root is not None and not root.is_dir():
            continue
        base, found = agent.transcript_files(session_id, root)
        if base is not None:
            found = [f for f in found if f.is_file() and base in f.parents]
            if found:
                return base, found[: MAX_FILES + 1], archived
    return None, [], False


def list_transcripts(tool: str | None, session_id: str | None) -> dict:
    """The session's transcript files, with paths relative to their base folder."""
    base, found, archived = _files(tool, session_id)
    rows = []
    for path in found[:MAX_FILES]:
        try:
            stat = path.stat()
        except OSError:
            continue
        rows.append({"path": str(path.relative_to(base)), "size": stat.st_size, "modified": stat.st_mtime})
    return {"root": str(base) if base else None, "files": rows, "truncated": len(found) > MAX_FILES, "archived": archived}


def resolve_transcript(tool: str | None, session_id: str | None, relative: str) -> Path:
    """The absolute path of one listed transcript file. Anything not listed raises FileNotFoundError."""
    base, found, _ = _files(tool, session_id)
    if base is not None:
        for path in found[:MAX_FILES]:
            if str(path.relative_to(base)) == relative:
                return path
    raise FileNotFoundError(relative)


def memory_folder(tool: str | None, session_id: str | None, cwd: str | None) -> Path | None:
    """The folder of notes the session's tool keeps for its project, or None."""
    agent = get_agent(tool) if tool else None
    if agent is None:
        return None
    if session_id and not _SESSION_ID.fullmatch(session_id):
        session_id = None
    return agent.memory_folder(session_id, cwd)


TOKEN_FIELDS = ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")


@lru_cache(maxsize=256)
def _file_stats(tool: str, path: str, _size: int, _mtime_ns: int) -> tuple[int, int, dict | None]:
    """(prompts, replies, token usage) in one transcript file. Cached by size and time,
    so a past session's tab reads its transcript once.
    """
    agent = get_agent(tool)
    file = Path(path)
    if agent is None or agent.conversation_reader(file) is None:
        return 0, 0, None
    prompts = replies = 0
    if not agent.side_conversation(file):
        for message in conversation.read(agent, file) or []:
            prompts += message.role == "user"
            replies += message.role == "assistant"
    return prompts, replies, agent.token_usage(file)


def transcript_stats(tool: str | None, session_id: str | None) -> dict:
    """What a session's transcript holds: its prompts and replies (not a subagent's),
    the tokens every file records, subagents included, and the files' size on disk.
    """
    _base, found, archived = _files(tool, session_id)
    prompts = replies = size = 0
    usage: dict | None = None
    for path in found[:MAX_FILES]:
        try:
            stat = path.stat()
            p, r, u = _file_stats(tool, str(path), stat.st_size, stat.st_mtime_ns)
        except (OSError, ValueError):
            continue
        size += stat.st_size
        prompts, replies = prompts + p, replies + r
        if u:
            usage = {k: (usage or {}).get(k, 0) + u.get(k, 0) for k in TOKEN_FIELDS}
    return {
        "prompts": prompts,
        "replies": replies,
        "token_usage": usage,
        "files": min(len(found), MAX_FILES),
        "bytes": size,
        "truncated": len(found) > MAX_FILES,
        "archived": archived,
    }


def read_conversation(tool: str | None, session_id: str | None) -> list[conversation.Message]:
    """The session's prompts and replies, not a subagent's, oldest first across all its files."""
    _base, found, _ = _files(tool, session_id)
    agent = get_agent(tool) if tool else None
    messages: list[conversation.Message] = []
    for path in found[:MAX_FILES]:
        if agent is None or agent.side_conversation(path):
            continue
        try:
            messages.extend(conversation.read(agent, path) or [])
        except (OSError, ValueError):
            continue
    # A message with no time keeps its place after the one before it.
    last = 0.0
    keys = []
    for m in messages:
        last = m.at if m.at is not None else last
        keys.append(last)
    return [m for _, _, m in sorted(zip(keys, range(len(messages)), messages), key=lambda k: (k[0], k[1]))]
