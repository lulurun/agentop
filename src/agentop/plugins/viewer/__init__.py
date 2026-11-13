"""File viewer: serves a file's text for the viewer tab.

Two sources, both confined: `workdir` is the session's working directory (the
same rule as the Files list), and `transcript` is one of the files the agent
tool reports for the session. A transcript can also be read as a conversation:
the prompts and replies in it, without tool calls and output.

A file from the Changed list opens as its diff against HEAD, through `/diff`.

A Markdown file is drawn in the browser. The images it names come through
`/image`, as data the page puts in the <img>, since the page's requests carry
their token in a header an <img> can't send.
"""

from __future__ import annotations

import asyncio
import base64
from pathlib import Path
from typing import Literal

from fastapi import HTTPException

from agentop import conversation
from agentop.agentclis import get_agent
from agentop.service import files, transcripts

MAX_BYTES = 2 * 1024 * 1024
SNIFF_BYTES = 8192
# A conversation longer than this shows its latest messages.
MAX_MESSAGES = 5000
MAX_IMAGE_BYTES = 5 * 1024 * 1024
# An SVG in an <img> runs no scripts, so it is as safe as the others.
IMAGE_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".avif": "image/avif",
    ".bmp": "image/bmp",
    ".ico": "image/x-icon",
    ".svg": "image/svg+xml",
}


def read_text(path: Path, max_bytes: int = MAX_BYTES) -> dict:
    """The file's text, up to max_bytes. A file with a NUL byte near the start is binary and has no text."""
    size = path.stat().st_size
    with open(path, "rb") as f:
        data = f.read(max_bytes)
    if b"\0" in data[:SNIFF_BYTES]:
        return {"size": size, "binary": True, "truncated": False, "text": ""}
    truncated = size > len(data)
    if truncated:
        # Don't end in the middle of a line, or of a multi-byte character.
        cut = data.rfind(b"\n")
        data = data[: cut + 1] if cut > 0 else data
    return {"size": size, "binary": False, "truncated": truncated, "text": data.decode("utf-8", errors="replace")}


def read_image(path: Path, max_bytes: int = MAX_IMAGE_BYTES) -> dict:
    """An image file as base64, for a data: URL. Raises ValueError for anything else."""
    kind = IMAGE_TYPES.get(path.suffix.lower())
    if kind is None:
        raise ValueError("Not an image the viewer shows")
    size = path.stat().st_size
    if size > max_bytes:
        raise ValueError(f"The image is larger than {max_bytes // (1024 * 1024)} MB")
    return {"type": kind, "size": size, "data": base64.b64encode(path.read_bytes()).decode("ascii")}


def register(api):
    def session_row(session_id: str) -> dict:
        row = next((s for s in api.sessions() if s["id"] == session_id), None)
        if row is None:
            raise HTTPException(404, "Session not found")
        return row

    def locate(row: dict, source: str, path: str) -> Path:
        if source == "transcript":
            return transcripts.resolve_transcript(row.get("tool"), row.get("session_id"), path)
        if not row.get("cwd"):
            raise FileNotFoundError(path)
        _, target = files.resolve_inside(row["cwd"], path)
        return target

    def readable(row: dict, source: str, target: Path) -> bool:
        agent = get_agent(row.get("tool") or "")
        return source == "transcript" and agent is not None and agent.conversation_reader(target) is not None

    async def located(row: dict, source: str, path: str, read):
        try:
            target = await asyncio.to_thread(locate, row, source, path)
            if not target.is_file():
                raise FileNotFoundError(path)
            return target, await asyncio.to_thread(read, target)
        except files.OutsideRoot:
            raise HTTPException(403, "That path is outside the session's directory") from None
        except FileNotFoundError:
            raise HTTPException(404, "No such file") from None
        except PermissionError:
            raise HTTPException(403, "Permission denied") from None
        except ValueError as exc:
            raise HTTPException(415, str(exc)) from None

    @api.router.get("/file")
    async def read_file(session: str, path: str, source: Literal["workdir", "transcript"] = "workdir"):
        row = session_row(session)
        target, result = await located(row, source, path, read_text)
        return {
            "path": path,
            "source": source,
            "absolute": str(target),
            "conversation": readable(row, source, target),
            **result,
        }

    @api.router.get("/image")
    async def read_image_file(session: str, path: str, source: Literal["workdir", "transcript"] = "workdir"):
        """An image a Markdown file shows, from the same places /file reads."""
        row = session_row(session)
        _, result = await located(row, source, path, read_image)
        return {"path": path, **result}

    @api.router.get("/diff")
    async def read_diff(session: str, path: str):
        """A changed file's diff against HEAD, for a file from the Changed list. The file may be deleted."""
        row = session_row(session)
        if not row.get("cwd"):
            raise HTTPException(404, "No such file")
        try:
            result = await asyncio.to_thread(files.git_diff, row["cwd"], path)
        except files.OutsideRoot:
            raise HTTPException(403, "That path is outside the session's directory") from None
        except FileNotFoundError:
            raise HTTPException(404, "This file has no changes") from None
        except PermissionError:
            raise HTTPException(403, "Permission denied") from None
        _, target = files.resolve_inside(row["cwd"], path)
        return {"path": path, "absolute": str(target), **result}

    @api.router.get("/conversation")
    async def read_conversation(session: str, path: str):
        """A transcript's prompts and replies, oldest first."""
        row = session_row(session)
        target, messages = await located(row, "transcript", path, lambda t: conversation.read(get_agent(row.get("tool") or ""), t))
        if messages is None:
            raise HTTPException(404, "This file isn't a conversation the viewer can read")
        return {
            "path": path,
            "absolute": str(target),
            "total": len(messages),
            "messages": [{"role": m.role, "text": m.text, "at": m.at} for m in messages[-MAX_MESSAGES:]],
        }
