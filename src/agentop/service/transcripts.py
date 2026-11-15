"""Where a session's transcript lives: the agent tool's own conversation log.

Only the files the tool reports for that session can be listed or read, so a
request can name one of them but never reach anything else on disk.

When the tool has deleted them, the Memory plugin's archive may still have
them (design 12.11). Its copy has the tool's own layout, so the same lookup
finds them there.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from agentop import conversation, paths
from agentop.agentclis import get_agent

MAX_FILES = 500

# Agent session IDs are UUID-like. Anything else is refused before it reaches a glob.
_SESSION_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")


def archive_root(tool: str) -> Path:
    """Where the Memory plugin keeps its copy of a tool's transcripts."""
    return paths.plugins_dir() / "memory" / "archive" / tool


def _files(tool: str | None, session_id: str | None) -> tuple[Path | None, list[Path], bool]:
    """(base folder, files, whether they are the archive's copy)."""
    if not tool or not session_id or not _SESSION_ID.fullmatch(session_id):
        return None, [], False
    agent = get_agent(tool)
    if agent is None:
        return None, [], False
    for root, archived in ((None, False), (archive_root(tool), True)):
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
