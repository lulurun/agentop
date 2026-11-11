"""A transcript as a conversation: what the user typed and what the agent wrote back.

Each agent tool writes its sessions as JSONL in its own shape. A reader turns
one record into the messages it holds, keeping only text: the user's prompts and
the assistant's text. Tool calls, tool output, thinking, and the text the tools
inject (instructions, reminders, context) are left out.

The File viewer shows a transcript this way, and the Memory plugin builds its
messages table from it.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import zstandard


@dataclass
class Message:
    record: str
    role: str
    text: str
    at: float | None = None
    session: str | None = None
    project: str | None = None
    turn: str | None = None


def _epoch(stamp) -> float | None:
    if not isinstance(stamp, str):
        return None
    try:
        return datetime.fromisoformat(stamp).timestamp()
    except ValueError:
        return None


def _texts(content, kinds: tuple[str, ...]) -> list[str]:
    if isinstance(content, str):
        return [content]
    if not isinstance(content, list):
        return []
    return [
        b["text"]
        for b in content
        if isinstance(b, dict)
        and b.get("type") in kinds
        and isinstance(b.get("text"), str)
    ]


# ---------------------------------------------------------------- claude

_REMINDER = re.compile(r"<system-reminder>.*?</system-reminder>", re.DOTALL)
_COMMAND = re.compile(r"<command-name>(.*?)</command-name>", re.DOTALL)
_ARGS = re.compile(r"<command-args>(.*?)</command-args>", re.DOTALL)
_CLAUDE_NOISE = (
    "<local-command-",
    "<command-message>",
    "[Request interrupted",
    "<task-notification>",
    "Caveat:",
)


def _claude_prompt(content) -> str:
    if isinstance(content, list) and any(
        isinstance(b, dict) and b.get("type") == "tool_result" for b in content
    ):
        return ""
    text = _REMINDER.sub("", "\n".join(_texts(content, ("text",)))).strip()
    command = _COMMAND.search(text)
    if command and text.startswith("<command-"):
        # A slash command: what was typed, not the expansion.
        args = _ARGS.search(text)
        return f"{command.group(1).strip()} {args.group(1).strip() if args else ''}".strip()
    if text.startswith(_CLAUDE_NOISE):
        return ""
    return text


def claude_messages(record: dict, context: dict) -> Iterator[Message]:
    kind = record.get("type")
    if kind not in ("user", "assistant") or record.get("isMeta"):
        return
    if record.get("isSidechain") and not context.get("sidechain"):
        return
    if record.get("isCompactSummary") or record.get("isApiErrorMessage"):
        return
    uuid = record.get("uuid")
    message = record.get("message") or {}
    if not uuid or not isinstance(message, dict):
        return
    if kind == "user":
        text = _claude_prompt(message.get("content"))
        if text:
            context["turn"] = uuid
    else:
        text = "\n\n".join(_texts(message.get("content"), ("text",))).strip()
    if text:
        yield Message(
            record=uuid,
            role=kind,
            text=text,
            at=_epoch(record.get("timestamp")),
            session=record.get("sessionId"),
            project=record.get("cwd"),
            turn=context.get("turn"),
        )


# ---------------------------------------------------------------- codex

_CODEX_NOISE = ("# AGENTS.md instructions",)
_TAGGED = re.compile(r"<([A-Za-z_][\w-]*)>.*</\1>", re.DOTALL)


def _injected(text: str) -> bool:
    """Text Codex adds to a user message: instructions, context, app events, each one whole tagged block."""
    return text.startswith(_CODEX_NOISE) or _TAGGED.fullmatch(text) is not None


def codex_messages(record: dict, context: dict) -> Iterator[Message]:
    kind = record.get("type")
    payload = record.get("payload") or {}
    if kind == "session_meta":
        context["session"] = payload.get("id") or payload.get("session_id")
        context["project"] = payload.get("cwd")
        return
    if kind == "turn_context":
        context["turn"] = payload.get("turn_id")
        context["project"] = payload.get("cwd") or context.get("project")
        return
    if kind != "response_item" or payload.get("type") != "message":
        return
    role = payload.get("role")
    if role not in ("user", "assistant") or not payload.get("id"):
        return
    kinds = ("input_text",) if role == "user" else ("output_text",)
    parts = [t.strip() for t in _texts(payload.get("content"), kinds)]
    text = "\n\n".join(t for t in parts if t and not (role == "user" and _injected(t)))
    if text:
        yield Message(
            record=payload["id"],
            role=role,
            text=text,
            at=_epoch(record.get("timestamp")),
            session=context.get("session"),
            project=context.get("project"),
            turn=context.get("turn"),
        )


# ---------------------------------------------------------------- antigravity

_REQUEST = re.compile(r"<USER_REQUEST>\s*(.*?)\s*</USER_REQUEST>", re.DOTALL)


def antigravity_messages(record: dict, context: dict) -> Iterator[Message]:
    kind = record.get("type")
    step = record.get("step_index")
    content = record.get("content")
    if step is None or not isinstance(content, str):
        return
    if kind == "USER_INPUT":
        found = _REQUEST.search(content)
        # Some prompts have no closing tag.
        text = (
            found.group(1) if found else content.removeprefix("<USER_REQUEST>")
        ).strip()
        role = "user"
        context["turn"] = str(step)
    elif kind == "PLANNER_RESPONSE":
        text, role = content.strip(), "assistant"
    else:
        return
    if text:
        session = context.get("session")
        yield Message(
            record=f"{session}:{step}",
            role=role,
            text=text,
            at=_epoch(record.get("created_at")),
            session=session,
            turn=context.get("turn"),
        )


Reader = Callable[[dict, dict], Iterator[Message]]

# A transcript, compressed (Codex compresses older ones), or the old copy of one
# the tool rewrote (`name.jsonl.1800000000`).
TRANSCRIPT = re.compile(r"\.jsonl(\.zst)?(\.\d+)?$")


def reader_for(tool: str | None, path: Path) -> tuple[Reader, dict] | None:
    """How to read a tool's transcript file, and the context to start from, or None if it isn't one."""
    if not TRANSCRIPT.search(path.name):
        return None
    if tool == "claude":
        # A subagent's log is a conversation too, one the main log leaves out.
        return claude_messages, {"sidechain": "subagents" in path.parts}
    if tool == "codex" and path.name.startswith("rollout-"):
        return codex_messages, {}
    if (
        tool == "antigravity"
        and path.name.startswith("transcript.jsonl")
        and path.parent.name == "logs"
    ):
        return antigravity_messages, {"session": path.parents[2].name}
    return None


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


def read(tool: str | None, path: Path) -> list[Message] | None:
    """Every message in one transcript file, or None if it isn't a transcript this knows."""
    how = reader_for(tool, path)
    if how is None:
        return None
    reader, context = how
    with open(path, "rb") as f:
        if compressed(path):
            data = zstandard.ZstdDecompressor().stream_reader(f).read().splitlines()
            return list(records(iter(data), context, reader))
        return list(records(f, context, reader))
