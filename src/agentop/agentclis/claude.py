from __future__ import annotations

import json
import re
import socket
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Optional

from agentop.agentclis.base import AgentCli, files_under
from agentop.conversation import TRANSCRIPT, Message, Reader, epoch, texts

_PROJECTS_DIR = Path("~/.claude/projects")

# ---------------------------------------------------------------- transcripts as conversations

_REMINDER = re.compile(r"<system-reminder>.*?</system-reminder>", re.DOTALL)
_COMMAND = re.compile(r"<command-name>(.*?)</command-name>", re.DOTALL)
_ARGS = re.compile(r"<command-args>(.*?)</command-args>", re.DOTALL)
_NOISE = (
    "<local-command-",
    "<command-message>",
    "[Request interrupted",
    "<task-notification>",
    "Caveat:",
)


def _prompt(content) -> str:
    if isinstance(content, list) and any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
        return ""
    text = _REMINDER.sub("", "\n".join(texts(content, ("text",)))).strip()
    command = _COMMAND.search(text)
    if command and text.startswith("<command-"):
        # A slash command: what was typed, not the expansion.
        args = _ARGS.search(text)
        return f"{command.group(1).strip()} {args.group(1).strip() if args else ''}".strip()
    if text.startswith(_NOISE):
        return ""
    return text


def messages(record: dict, context: dict) -> Iterator[Message]:
    """The prompt or reply in one record of a Claude Code session log."""
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
        text = _prompt(message.get("content"))
        if text:
            context["turn"] = uuid
    else:
        text = "\n\n".join(texts(message.get("content"), ("text",))).strip()
    if text:
        yield Message(
            record=uuid,
            role=kind,
            text=text,
            at=epoch(record.get("timestamp")),
            session=record.get("sessionId"),
            project=record.get("cwd"),
            turn=context.get("turn"),
        )


class ClaudeAgentCli(AgentCli):
    name = "claude"

    # Shown under the input box, and not on the trust prompt.
    READY_MARKER = "shift+tab to cycle"

    @property
    def launch_cmd(self) -> str:
        return "claude --dangerously-skip-permissions --remote-control"

    def skills_dir(self) -> Path:
        return Path.home() / ".claude" / "skills"

    def matches(self, proc_name: str, cmdline: str) -> bool:
        # Print mode answers one prompt and exits: a script's, or agentop's own
        # (ask), never a session to list.
        if {"-p", "--print"} & set(cmdline.split()[1:]):
            return False
        return super().matches(proc_name, cmdline)

    # The cheapest model is plenty for summaries.
    ask_model = "haiku"

    def ask_cmd(self, system: str) -> list[str]:
        return [
            "claude",
            "--print",
            "--model",
            self.ask_model,
            "--no-session-persistence",
            "--tools",
            "",
            "--system-prompt",
            system,
            "--output-format",
            "json",
        ]

    def ask_answer(self, stdout: str) -> str:
        # Hooks may print before the result: it is the last line.
        lines = [line for line in stdout.splitlines() if line.strip()]
        try:
            result = json.loads(lines[-1])
        except (IndexError, ValueError):
            raise RuntimeError(f"claude printed no result: {stdout.strip()[:200]!r}") from None
        if result.get("is_error"):
            raise RuntimeError(f"claude: {str(result.get('result') or result.get('subtype'))[:200]}")
        return str(result.get("result") or "").strip()

    def get_resume_cmd(self, session_id: str) -> str:
        return f"claude --resume {session_id} --dangerously-skip-permissions --remote-control"

    # ------------------------------------------------------------------ transcripts

    def transcripts_root(self) -> Path:
        return _PROJECTS_DIR.expanduser()

    def archive_files(self, root: Path) -> Iterator[Path]:
        """Every session log and the folders beside them. Not Claude Code's own memory notes."""
        for path in files_under(root):
            parts = path.relative_to(root).parts
            if len(parts) > 2 and parts[1] == "memory":
                continue
            yield path

    def conversation_reader(self, path: Path) -> tuple[Reader, dict] | None:
        if not TRANSCRIPT.search(path.name):
            return None
        # A subagent's log is a conversation too, one the main log leaves out.
        return messages, {"sidechain": "subagents" in path.parts}

    def searchable(self, relative: Path) -> bool:
        """Only the main conversations, `<project>/<session>.jsonl`: not subagents or tool output."""
        return len(relative.parts) == 2

    def delete_session(self, session_id: str) -> None:
        projects_dir = self.transcripts_root()
        for f in projects_dir.glob(f"*/{session_id}.jsonl"):
            f.unlink()
            return
        raise FileNotFoundError(f"Session {session_id} not found in ~/.claude/projects")

    def transcript_files(self, session_id: str, root: Optional[Path] = None) -> tuple[Optional[Path], list[Path]]:
        """The conversation log, and the folder of the same name beside it (subagents, tool results)."""
        projects_dir = root or self.transcripts_root()
        for log in projects_dir.glob(f"*/{session_id}.jsonl"):
            folder = log.with_suffix("")
            extra = sorted(p for p in folder.rglob("*") if p.is_file()) if folder.is_dir() else []
            return log.parent, [log, *extra]
        return None, []

    def get_saved_sessions(self, limit: int = 20) -> list[dict]:
        projects_dir = self.transcripts_root()
        if not projects_dir.exists():
            return []
        sessions = []
        for proj_dir in projects_dir.iterdir():
            if not proj_dir.is_dir():
                continue
            for f in proj_dir.glob("*.jsonl"):
                title = None
                cwd = None
                try:
                    with open(f) as fp:
                        for line in fp:
                            try:
                                obj = json.loads(line)
                                t = obj.get("type")
                                if t == "ai-title" and not title:
                                    title = obj.get("aiTitle")
                                if t in ("system", "user") and not cwd:
                                    cwd = obj.get("cwd")
                            except json.JSONDecodeError:
                                continue
                            if title and cwd:
                                break
                except OSError:
                    continue
                sessions.append(
                    {
                        "session_id": f.stem,
                        "title": title,
                        "cwd": cwd or "",
                        "tool": self.name,
                        "last_active": f.stat().st_mtime,
                    }
                )
        sessions.sort(key=lambda x: x["last_active"], reverse=True)
        return sessions[:limit]

    def post_start_hook(self, tmux_session: str) -> None:
        """Accept Claude's trust / safety-check prompt, or stop as soon as Claude is ready.

        The ready marker is checked first, because a resumed conversation can
        have the word "trust" on screen.
        """
        from agentop.tmux import CapturePane, Session

        for _ in range(40):
            time.sleep(0.25)
            content = CapturePane.screen(tmux_session).lower()
            if self.READY_MARKER in content:
                return
            if "trust" in content or "safety check" in content:
                time.sleep(0.1)
                # "No, exit" is the default selection, so Enter alone declines
                # and claude quits. Move down to "Yes, I trust this folder".
                Session.send_keys(tmux_session, "Down", "Enter")
                return

    def _get_jsonl_path(self, pid: int, cwd: str) -> Optional[Path]:
        """Resolve the JSONL conversation file for a Claude session, or None."""
        session_file = Path(f"~/.claude/sessions/{pid}.json").expanduser()
        if not session_file.exists():
            return None
        try:
            with open(session_file) as f:
                meta = json.load(f)
            session_id = meta.get("sessionId")
            if not session_id:
                return None
            slug = cwd.replace("/", "-")
            jsonl_path = self.transcripts_root() / slug / f"{session_id}.jsonl"
            return jsonl_path if jsonl_path.exists() else None
        except (OSError, json.JSONDecodeError):
            return None

    def _read_token_usage(self, jsonl_path: Path) -> Optional[dict]:
        """Sum token usage across unique assistant messages in a JSONL file."""
        try:
            seen_ids: set = set()
            totals = {
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
            }
            with open(jsonl_path) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                        if obj.get("type") != "assistant":
                            continue
                        msg = obj.get("message", {})
                        msg_id = msg.get("id")
                        if not msg_id or msg_id in seen_ids:
                            continue
                        seen_ids.add(msg_id)
                        usage = msg.get("usage", {})
                        for k in totals:
                            totals[k] += usage.get(k, 0)
                    except json.JSONDecodeError:
                        continue
            return totals if seen_ids else None
        except OSError:
            return None

    def get_ai_title(self, pid: int, cwd: str) -> Optional[str]:
        """Read the AI-generated title from ~/.claude/projects/{slug}/{session_id}.jsonl."""
        jsonl_path = self._get_jsonl_path(pid, cwd)
        if not jsonl_path:
            return None
        try:
            last_title = None
            with open(jsonl_path) as f:
                for line in f:
                    try:
                        obj = json.loads(line)
                        if obj.get("type") == "ai-title":
                            last_title = obj.get("aiTitle")
                    except json.JSONDecodeError:
                        continue
            return last_title
        except OSError:
            return None

    def get_extra_meta(self, pid: int, cwd: str) -> dict:
        """Read remote-control bridge info and token usage from ~/.claude/sessions/{pid}.json."""
        session_file = Path(f"~/.claude/sessions/{pid}.json").expanduser()
        result: dict = {}
        if session_file.exists():
            try:
                with open(session_file) as f:
                    meta = json.load(f)
                session_id = meta.get("sessionId")
                if session_id:
                    result["session_id"] = session_id
                bridge_id = meta.get("bridgeSessionId")
                if bridge_id:
                    hostname = socket.gethostname()
                    raw = bridge_id.replace("session_", "")
                    remote_name = f"{hostname}-{raw[:4]}-{raw[4:8]}" if len(raw) >= 8 else f"{hostname}-{raw}"
                    result.update(
                        {
                            "bridge_session_id": bridge_id,
                            "bridge_url": f"https://claude.ai/code/{bridge_id}",
                            "remote_name": remote_name,
                            "claude_status": meta.get("status"),
                        }
                    )
            except (OSError, json.JSONDecodeError):
                pass

        jsonl_path = self._get_jsonl_path(pid, cwd)
        if jsonl_path:
            token_usage = self._read_token_usage(jsonl_path)
            if token_usage:
                result["token_usage"] = token_usage

        return result
