from __future__ import annotations

import json
import re
import shutil
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Optional

import psutil

from agentop import oneshot
from agentop.agentclis.base import AgentCli, Model
from agentop.conversation import TRANSCRIPT, Message, Reader, epoch, json_lines
from agentop.results import SavedSession, saved_session

_TOOL_DIR = Path(".gemini") / "antigravity-cli"
# brain/ has one folder per conversation. Conversations from before the rename to
# Antigravity are only in its binary store, conversations/, and aren't read.
_TRANSCRIPT = Path(".system_generated/logs/transcript.jsonl")

_LOG_NAME_RE = re.compile(r"cli-(\d{8})_(\d{6})\.log")
_USER_INPUT_RE = re.compile(r'HandleUserInput called with text: "(.+)"')
_START_TOLERANCE_SEC = 15
# The lines that name the conversation a run works on: a new one, or one it reopened.
_CONVERSATION_RE = re.compile(
    r"(?:Created conversation|Streaming conversation|found conversation|message to conversation) ([0-9a-f-]{36})"
)

# ---------------------------------------------------------------- transcripts as conversations

_REQUEST = re.compile(r"<USER_REQUEST>\s*(.*?)\s*</USER_REQUEST>", re.DOTALL)


def messages(record: dict, context: dict) -> Iterator[Message]:
    """The prompt or reply in one step of an Antigravity transcript."""
    kind = record.get("type")
    step = record.get("step_index")
    content = record.get("content")
    if step is None or not isinstance(content, str):
        return
    if kind == "USER_INPUT":
        found = _REQUEST.search(content)
        # Some prompts have no closing tag.
        text = (found.group(1) if found else content.removeprefix("<USER_REQUEST>")).strip()
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
            at=epoch(record.get("created_at")),
            session=session,
            turn=context.get("turn"),
        )


class AntigravityAgentCli(AgentCli):
    name = "antigravity"
    idle_seconds = 15.0

    models = (
        Model("Gemini 3.8 Flash (Low)", "gemini-3.8-flash-low", "light"),
        Model("Gemini 3.8 Flash (High)", "gemini-3.8-flash-high", "standard"),
        Model("Claude Opus 4.6 (Thinking)", "claude-opus-4-6-thinking", "complex"),
    )

    ask_model = "gemini-3.8-flash-low"

    def ask_cmd(self, system: str, model: Optional[str] = None) -> list[str]:
        # agy has no system prompt flag, and print mode takes the prompt only as -p's
        # own value (`-p=…`), never from stdin: ask() puts both there.
        return ["agy", "--model", model or self.ask_model, "--output-format", "json", "--disable-slash-commands"]

    def ask(self, prompt, system, cwd, timeout=180, stop=lambda: False, model=None) -> str:
        cmd = self.ask_cmd(system, model)
        cmd[0] = self.executable or shutil.which(cmd[0]) or cmd[0]
        cmd.append(f"-p={system}\n\n{prompt}")
        return self.ask_answer(oneshot.run(self.name, cmd, "", cwd, timeout, stop))

    def ask_answer(self, stdout: str) -> str:
        lines = [line for line in stdout.splitlines() if line.strip()]
        try:
            result = json.loads(lines[-1])
        except (IndexError, ValueError):
            raise RuntimeError(f"agy printed no result: {stdout.strip()[:200]!r}") from None
        # Print mode keeps its conversation like any other; an answer isn't a session to list or archive.
        conversation = str(result.get("conversation_id") or "")
        if re.fullmatch(r"[0-9a-f-]{36}", conversation):
            shutil.rmtree(self.transcripts_root() / conversation, ignore_errors=True)
        if result.get("status") != "SUCCESS":
            raise RuntimeError(f"agy: {str(result.get('status') or result)[:200]}")
        return str(result.get("response") or "").strip()

    def matches(self, proc_name: str, cmdline: str) -> bool:
        name_lower = proc_name.lower()
        cmd_lower = cmdline.lower()
        if any(word in ("-p", "--print", "--prompt") or word.startswith(("-p=", "--print=", "--prompt=")) for word in cmd_lower.split()[1:]):
            return False
        if name_lower in ("agy", "antigravity") or name_lower.startswith("agy"):
            return True
        # May run as `node /path/to/bin/agy …`
        for token in cmd_lower.split():
            base = token.split("/")[-1]
            if base in ("agy", "antigravity") or base.startswith("agy-"):
                return True
        return False

    @property
    def launch_cmd(self) -> str:
        return "agy --dangerously-skip-permissions"

    def _find_log_file(self, pid: int, proc_start: Optional[float] = None) -> Optional[Path]:
        """Find the log file for this process by matching the filename timestamp to the process start time."""
        log_dir = self.home / _TOOL_DIR / "log"
        if not log_dir.exists():
            return None
        if proc_start is None:
            try:
                proc_start = psutil.Process(pid).create_time()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                return None

        best_file: Optional[Path] = None
        best_diff = float("inf")
        for f in log_dir.glob("cli-*.log"):
            m = _LOG_NAME_RE.match(f.name)
            if not m:
                continue
            try:
                log_ts = datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S").timestamp()
                diff = abs(log_ts - proc_start)
                if diff < best_diff:
                    best_diff = diff
                    best_file = f
            except ValueError:
                continue

        return best_file if best_diff <= _START_TOLERANCE_SEC else None

    def _conversation_in(self, log_file: Optional[Path]) -> Optional[str]:
        """The last conversation a CLI log says it created, opened, or sent to."""
        if log_file is None:
            return None
        found = None
        try:
            with open(log_file, errors="replace") as f:
                for line in f:
                    m = _CONVERSATION_RE.search(line)
                    if m:
                        found = m.group(1)
        except OSError:
            return None
        return found

    def session_id_after(self, pid: int, started: float) -> Optional[str]:
        """Each run of the CLI has its own log, named for when it started."""
        return self._conversation_in(self._find_log_file(pid, started))

    def get_resume_cmd(self, session_id: str) -> str:
        return f"agy --conversation {session_id} --dangerously-skip-permissions"

    def _conv_to_cwd_map(self) -> dict[str, str]:
        """Scan log files to build a conversation_id → cwd mapping."""
        log_dir = self.home / _TOOL_DIR / "log"
        projects_dir = self.home / ".gemini" / "config" / "projects"
        conv_to_proj: dict[str, str] = {}
        proj_to_cwd: dict[str, str] = {}
        _proj_re = re.compile(r"Conversation using project ID: (\S+)")
        _conv_re = re.compile(r"Created conversation (\S+)")
        if log_dir.exists():
            for log_file in log_dir.glob("cli-*.log"):
                current_proj = None
                try:
                    with open(log_file) as f:
                        for line in f:
                            m = _proj_re.search(line)
                            if m:
                                current_proj = m.group(1)
                            m = _conv_re.search(line)
                            if m and current_proj:
                                conv_to_proj[m.group(1)] = current_proj
                except OSError:
                    continue
        if projects_dir.exists():
            for proj_file in projects_dir.glob("*.json"):
                try:
                    proj = json.loads(proj_file.read_text())
                    cwd = proj.get("name", "")
                    if cwd:
                        proj_to_cwd[proj_file.stem] = cwd
                except (OSError, json.JSONDecodeError):
                    continue
        return {c: proj_to_cwd[p] for c, p in conv_to_proj.items() if p in proj_to_cwd}

    # ------------------------------------------------------------------ transcripts

    def transcripts_root(self) -> Path:
        return self.home / _TOOL_DIR / "brain"

    def conversation_reader(self, path: Path) -> tuple[Reader, dict] | None:
        """`<conversation>/.system_generated/logs/transcript.jsonl`; the conversation ID is its folder's name."""
        if not TRANSCRIPT.search(path.name) or not path.name.startswith("transcript.jsonl") or path.parent.name != "logs":
            return None
        return messages, {"session": path.parents[2].name}

    def token_usage(self, path: Path) -> Optional[dict]:
        """Each model step's tokens, summed. A step written more than once counts once."""
        if self.conversation_reader(path) is None:
            return None
        steps: dict = {}
        try:
            for obj in json_lines(path):
                if any(k in obj for k in ("input_tokens", "output_tokens", "cache_read_tokens")):
                    steps[obj.get("step_index", len(steps))] = obj
        except OSError:
            return None
        if not steps:
            return None

        def total(key: str) -> int:
            return sum(v for s in steps.values() if isinstance(v := s.get(key), int))

        return {
            "input_tokens": total("input_tokens"),
            "output_tokens": total("output_tokens"),
            "cache_read_input_tokens": total("cache_read_tokens"),
            "cache_creation_input_tokens": 0,
        }

    def transcript_files(self, session_id: str, root: Optional[Path] = None) -> tuple[Optional[Path], list[Path]]:
        """Everything in the conversation's brain folder: the transcript and the artifacts beside it."""
        folder = (root or self.transcripts_root()) / session_id
        if not folder.is_dir() or folder.name != session_id:
            return None, []
        return folder, sorted(p for p in folder.rglob("*") if p.is_file())

    def get_saved_sessions(self, limit: int = 20) -> list[SavedSession]:
        """Return saved antigravity sessions from the brain transcript directory."""
        brain_dir = self.transcripts_root()
        if not brain_dir.exists():
            return []
        cwd_map = self._conv_to_cwd_map()
        sessions = []
        for conv_dir in brain_dir.iterdir():
            if not conv_dir.is_dir():
                continue
            session_id = conv_dir.name
            transcript = conv_dir / _TRANSCRIPT
            if not transcript.exists():
                continue
            title = None
            try:
                with open(transcript) as f:
                    for line in f:
                        try:
                            obj = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if obj.get("type") == "USER_INPUT" and not title:
                            raw = obj.get("content", "")
                            text = re.sub(r"<[^>]+>", "", raw).strip()
                            title = text[:80] + ("…" if len(text) > 80 else "")
                            break
            except OSError:
                continue
            sessions.append(saved_session(self.name, session_id, title, cwd_map.get(session_id), transcript.stat().st_mtime))
        sessions.sort(key=lambda x: x["last_active"], reverse=True)
        return sessions[:limit]

    def delete_session(self, session_id: str) -> None:
        conv_dir = self.transcripts_root() / session_id
        if not conv_dir.exists():
            raise FileNotFoundError(f"Session {session_id} not found in Antigravity brain")
        shutil.rmtree(conv_dir)

    def get_session_meta(self, pid: int, cwd: str) -> dict:
        """The first user message in the session log, as the title."""
        log_file = self._find_log_file(pid)
        if not log_file:
            return {}
        try:
            with open(log_file) as f:
                for line in f:
                    m = _USER_INPUT_RE.search(line)
                    if m:
                        text = m.group(1)
                        return {"ai_title": text[:80] + ("…" if len(text) > 80 else "")}
        except OSError:
            pass
        return {}
