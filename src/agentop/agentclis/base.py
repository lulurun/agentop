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
import shlex
import shutil
import string
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Callable, Optional


from agentop import launcher, oneshot
from agentop.conversation import Reader
from agentop.results import SavedSession, StartFailed, StartResult
from agentop.tmux import Session

ProgressCallback = Callable[[str], None]

_typing: dict[str, threading.Lock] = {}
_typing_guard = threading.Lock()


def typing(session: str) -> threading.Lock:
    """The lock that queues what is typed into a session, so another prompt can't land between a paste and its Enter."""
    with _typing_guard:
        return _typing.setdefault(session, threading.Lock())


def files_under(base: Path) -> Iterator[Path]:
    """Every file below base, not following links."""
    if base.is_dir():
        for path in base.rglob("*"):
            if path.is_file() and not path.is_symlink():
                yield path


class AgentCli:
    """Interface and shared implementation for every supported agent CLI tool."""

    name: str = ""  # "claude", "codex", "antigravity"
    _home: Optional[Path] = None

    def __init__(self, home: Optional[Path] = None) -> None:
        self._home = home

    @property
    def home(self) -> Path:
        """The home folder the tool keeps its files under: the one given, else the user's, looked up each time."""
        return self._home or Path.home()

    # ------------------------------------------------------------------ typing into it
    # How long the screen must stay still before a reply counts as finished.
    idle_seconds: float = 5.0
    # How long a dialogue waits after launch before the first prompt.
    boot_seconds: float = 8.0
    # The flag that gives the agent another directory to work in (a project's folder), if it has one.
    add_dir_flag: str | None = None

    def deliver(self, session: str, text: str) -> None:
        """Submit a prompt: take the pane out of copy mode, where Enter wouldn't reach the tool, then send_text.

        Every prompt goes through here, one at a time per session (typing). Raises PasteError, before Enter, if the paste failed.
        """
        with typing(session):
            Session.leave_mode(session)
            self.send_text(session, text)

    def send_text(self, session: str, text: str) -> None:
        """Paste text into the tool's input and submit it. Raises PasteError, and sends no Enter, if the paste failed.

        Bracketed, so a line break stays in the prompt: a plain tmux paste turns each
        one into Enter, which submits (Claude Code too, for a short paste). A program
        that hasn't turned bracketed paste on gets a plain paste.
        """
        Session.paste_text_bracketed(session, text)
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

    @property
    def binary(self) -> str:
        """The program launch_cmd runs, which must be on the user's shell PATH."""
        return self.launch_cmd.split()[0]

    def skills_dir(self) -> Optional[Path]:
        """Where the tool looks for the user's skills, or None when agentop installs none for it."""
        return None

    # Where the program was found (agentclis.installed), or None before that.
    executable: Optional[str] = None

    # ------------------------------------------------------------------ one-shot prompts
    # For agentop's own use of a model, such as the Memory plugin's session
    # summaries: one prompt in, one answer out, with no tools, no tmux, and
    # nothing saved as a session of the tool's.

    # The model ask_cmd uses, for the record of what wrote an answer.
    ask_model: Optional[str] = None

    def ask_cmd(self, system: str) -> Optional[list[str]]:
        """The command that answers one prompt on stdin, or None if the tool can't."""
        return None

    def ask(
        self,
        prompt: str,
        system: str,
        cwd: Path,
        timeout: float = 180,
        stop: Callable[[], bool] = lambda: False,
    ) -> str:
        """Answer one prompt. Raises RuntimeError with the reason when it can't, or when stop() turns true."""
        cmd = self.ask_cmd(system)
        if cmd is None:
            raise RuntimeError(f"{self.name} can't answer a one-shot prompt")
        cmd[0] = self.executable or shutil.which(cmd[0]) or cmd[0]
        return self.ask_answer(oneshot.run(self.name, cmd, prompt, cwd, timeout, stop))

    def ask_answer(self, stdout: str) -> str:
        """The answer, from what ask_cmd printed."""
        return stdout.strip()

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
            cmd += f" --model {shlex.quote(params['model'])}"
        if params.get("add_dir") and self.add_dir_flag:
            cmd += f" {self.add_dir_flag} {shlex.quote(params['add_dir'])}"
        return cmd

    def new_session_name(self, short_name: str = "") -> str:
        """Pick a tmux session name. It is fixed from here on, so a session has one identity."""
        base = short_name if short_name else f"agentop_{self.name}"
        rand_id = "".join(random.choices(string.ascii_lowercase + string.digits, k=6))
        return f"{base}-{rand_id}"

    def start_session(
        self,
        cwd: str,
        short_name: str = "",
        params: dict | None = None,
        name: str | None = None,
        on_progress: ProgressCallback | None = None,
    ) -> StartResult:
        """Create a tmux session and launch the agent. Raises StartFailed.

        A tool that takes a session ID at launch is given one, and the result
        has it as `agent_session_id`, so the session is known as agentop's from the start.
        """
        session_id = self.new_session_id()
        cmd = self.start_cmd(session_id) if session_id else self.launch_cmd
        return launcher.launch(self, cmd, cwd, short_name, params, name, on_progress, session_id)

    def resume_session(
        self,
        session_id: str,
        cwd: str,
        short_name: str = "",
        name: str | None = None,
        on_progress: ProgressCallback | None = None,
        params: dict | None = None,
    ) -> StartResult:
        """Raises StartFailed."""
        cmd = self.get_resume_cmd(session_id)
        if not cmd:
            raise StartFailed(f"{self.name} does not support session resume")
        return launcher.launch(self, cmd, cwd, short_name, params, name, on_progress, session_id)

    # ------------------------------------------------------------------ the tool's own session ID

    def new_session_id(self) -> Optional[str]:
        """An ID to start a new session with, for a tool that takes one at launch; else None."""
        return None

    def start_cmd(self, session_id: str) -> str:
        """The launch command for a session with the ID from new_session_id."""
        return self.launch_cmd

    def session_id_after(self, pid: int, started: float) -> Optional[str]:
        """The session a process started at `started` worked on, found from the tool's own
        records, which outlive the process. None if the tool keeps none.
        """
        return None

    # ------------------------------------------------------------------ saved sessions

    def get_saved_sessions(self, limit: int = 20) -> list[SavedSession]:
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

    def memory_folder(self, session_id: str | None, cwd: str | None) -> Optional[Path]:
        """The folder of notes the tool keeps for the session's project, for a person to read, or None if it keeps none."""
        return None

    def conversation_reader(self, path: Path) -> tuple[Reader, dict] | None:
        """How to read a transcript file as a conversation, and the context to start from, or None.

        `path` is anywhere that keeps the tool's layout: the tool's own folder or the archive.
        """
        return None

    def side_conversation(self, path: Path) -> bool:
        """Whether a transcript file is a conversation the agent held on its own, such as a
        subagent's, rather than the one with the user. History's message counts leave these out.
        """
        return False

    def token_usage(self, path: Path) -> Optional[dict]:
        """The tokens one transcript file records, as `TokenUsage` fields, or None if it records none.

        Input tokens leave out what was read from the cache, which is counted on its own.
        """
        return None

    def searchable(self, relative: Path) -> bool:
        """Whether a transcript's messages go in the Memory search table. `relative` is to the transcripts root."""
        return True

    # ------------------------------------------------------------------ metadata

    def get_session_meta(self, pid: int, cwd: str) -> dict:
        """What the live process is doing: any of session_id, ai_title, token_usage, and the tool's own extras."""
        return {}
