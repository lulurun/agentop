"""What every agent tool has in common, and the default for each thing that differs.

Each supported tool has one module beside this one, with a subclass of AgentCli
that overrides what that tool does differently. That is the one place for a
tool's differences: how to spot its process, launch and resume it, type into
it, find its title and token usage, where its transcripts are, and how to read
them as a conversation. The rest of agentop asks the tool's AgentCli and never
names a tool itself.
"""

from __future__ import annotations

import random
import string
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Callable, Optional

import psutil

from agentop.conversation import Reader
from agentop.tmux import CapturePane, Session

ProgressCallback = Callable[[str], None]


def files_under(base: Path) -> Iterator[Path]:
    """Every file below base, not following links."""
    if base.is_dir():
        for path in base.rglob("*"):
            if path.is_file() and not path.is_symlink():
                yield path


class AgentCli:
    """Interface and shared implementation for every supported agent CLI tool."""

    name: str = ""  # "claude", "codex", "antigravity"

    # ------------------------------------------------------------------ typing into it
    # How long the screen must stay still before a reply counts as finished.
    idle_seconds: float = 5.0
    # How long a dialogue waits after launch before the first prompt.
    boot_seconds: float = 8.0

    def send_text(self, session: str, text: str) -> None:
        """Type text into the tool's input and submit it."""
        Session.paste_text(session, text)
        Session.send_keys(session, "Enter")

    # ------------------------------------------------------------------ startup
    start_timeout: float = 30.0
    # Wheel scrolling can only reach tmux scrollback with this on. It applies to
    # the one session; the user's global setting is left alone.
    mouse: bool = True

    # ------------------------------------------------------------------ detection

    def matches(self, proc_name: str, cmdline: str) -> bool:
        """Return True if this process belongs to this agent type."""
        pattern = self.name
        name_lower = proc_name.lower()
        cmd_lower = cmdline.lower()
        if name_lower == pattern or name_lower.startswith(pattern + "-"):
            return True
        tokens = cmd_lower.split()
        if tokens:
            first = tokens[0].split("/")[-1]
            if first == pattern or first.startswith(pattern):
                return True
        return False

    # ------------------------------------------------------------------ session lifecycle

    @property
    def launch_cmd(self) -> str:
        """Shell command to launch the agent inside a tmux pane."""
        return self.name

    def get_resume_cmd(self, session_id: str) -> Optional[str]:
        """Return the shell command to resume a saved session, or None if unsupported."""
        return None

    def post_start_hook(self, tmux_session: str) -> None:
        """Called once after the agent process appears in tmux."""
        pass

    def _build_cmd(self, base_cmd: str, params: dict) -> str:
        """Append agent-specific CLI flags derived from params."""
        cmd = base_cmd
        if "model" in params:
            cmd += f" --model {params['model']}"
        return cmd

    def new_session_name(self, short_name: str = "") -> str:
        """Pick a tmux session name. It is fixed from here on, so a session has one identity."""
        base = short_name if short_name else f"agentop_{self.name}"
        rand_id = "".join(random.choices(string.ascii_lowercase + string.digits, k=6))
        return f"{base}-{rand_id}"

    def _find_agent_pid(self, pane_pid: int) -> Optional[int]:
        """Return the PID of this agent among the pane's descendants."""
        try:
            for child in psutil.Process(pane_pid).children(recursive=True):
                try:
                    if self.matches(child.name(), " ".join(child.cmdline() or [])):
                        return child.pid
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
        return None

    def _launch_failed(self, session: str, cmd: str) -> bool:
        """Return True if the shell has already said it cannot run cmd."""
        binary = cmd.split()[0]
        screen = CapturePane.screen(session)
        return "command not found" in screen or f"{binary}: not found" in screen

    def _run_in_tmux(
        self,
        cmd: str,
        cwd: str,
        short_name: str = "",
        params: dict | None = None,
        name: str | None = None,
        on_progress: ProgressCallback | None = None,
    ) -> dict:
        """Create a tmux session, run cmd in its shell, and wait for the agent process.

        The command is typed into the pane rather than passed to new-session
        so that it runs with the user's shell setup, which the service's
        environment does not have.

        On failure the tmux session is left in place when it exists, and its
        name is in the result, so the error stays visible on its screen.
        """
        progress = on_progress or (lambda step: None)
        if params:
            cmd = self._build_cmd(cmd, params)
        name = name or self.new_session_name(short_name)

        progress("creating")
        if not Session.new(name, cwd):
            return {"ok": False, "error": "tmux new-session failed"}
        if self.mouse:
            Session.set_option(name, "mouse", "on")

        progress("launching")
        Session.send_keys(name, cmd, "Enter")

        progress("waiting")
        lines = Session.list_panes(name, "#{pane_pid}")
        pane_pid = int(lines[0]) if lines and lines[0].isdigit() else None
        tool_pid = None
        deadline = time.monotonic() + self.start_timeout
        while pane_pid and time.monotonic() < deadline:
            tool_pid = self._find_agent_pid(pane_pid)
            if tool_pid:
                break
            if self._launch_failed(name, cmd):
                return self._start_failed(name, f"{cmd.split()[0]} is not installed, or not on the shell's PATH")
            time.sleep(0.25)

        if not tool_pid:
            return self._start_failed(name, f"{self.name} did not start within {self.start_timeout:g}s")

        progress("confirming")
        self.post_start_hook(name)
        return {"ok": True, "name": name, "pid": tool_pid, "tmux_session": name}

    @staticmethod
    def _start_failed(name: str, error: str) -> dict:
        return {"ok": False, "error": error, "name": name, "tmux_session": name}

    def start_session(
        self,
        cwd: str,
        short_name: str = "",
        params: dict | None = None,
        name: str | None = None,
        on_progress: ProgressCallback | None = None,
    ) -> dict:
        """Create a tmux session and launch the agent."""
        return self._run_in_tmux(self.launch_cmd, cwd, short_name, params, name, on_progress)

    def resume_session(
        self,
        session_id: str,
        cwd: str,
        short_name: str = "",
        name: str | None = None,
        on_progress: ProgressCallback | None = None,
    ) -> dict:
        """Resume a saved session in a new tmux window."""
        cmd = self.get_resume_cmd(session_id)
        if not cmd:
            return {"ok": False, "error": f"{self.name} does not support session resume"}
        return self._run_in_tmux(cmd, cwd, short_name, name=name, on_progress=on_progress)

    # ------------------------------------------------------------------ saved sessions

    def get_saved_sessions(self, limit: int = 20) -> list[dict]:
        """Return a list of saved/historical sessions that can be resumed."""
        return []

    def delete_session(self, session_id: str) -> None:
        """Permanently remove a saved session from its source storage."""
        raise NotImplementedError(f"{self.name} does not support session deletion")

    # ------------------------------------------------------------------ transcripts

    def transcripts_root(self) -> Path | None:
        """The folder the tool keeps its transcripts in, or None if it keeps none."""
        return None

    def archive_files(self, root: Path) -> Iterator[Path]:
        """The files under the transcripts root that the Memory archive keeps a copy of."""
        return files_under(root)

    def transcript_files(self, session_id: str, root: Optional[Path] = None) -> tuple[Optional[Path], list[Path]]:
        """Return (base folder, files) holding a session's transcript, or (None, []).

        The files are the conversation log and whatever the tool keeps beside it,
        such as subagent logs. Their names are shown relative to the base folder.
        `root` stands in for the transcripts root: the Memory archive keeps the
        same layout under a folder per tool.
        """
        return None, []

    def conversation_reader(self, path: Path) -> tuple[Reader, dict] | None:
        """How to read a transcript file as a conversation, and the context to start from, or None.

        `path` is anywhere that keeps the tool's layout: the tool's own folder or the archive.
        """
        return None

    def searchable(self, relative: Path) -> bool:
        """Whether a transcript's messages go in the Memory search table. `relative` is to the transcripts root."""
        return True

    # ------------------------------------------------------------------ metadata

    def get_ai_title(self, pid: int, cwd: str) -> Optional[str]:
        return None

    def get_extra_meta(self, pid: int, cwd: str) -> dict:
        return {}

    def get_session_meta(self, pid: int, cwd: str) -> dict:
        meta = self.get_extra_meta(pid, cwd)
        title = self.get_ai_title(pid, cwd)
        if title:
            meta["ai_title"] = title
        return meta
