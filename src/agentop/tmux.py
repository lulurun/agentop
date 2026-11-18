"""Low-level tmux wrappers: pane capture and session control.

Every tmux call goes through `run`, so the socket and target rules live in one
place. tmux matches a bare target by prefix (`-t general` finds `general-41185`),
so one session can silently act on another. Targets here are always exact:
`=name` for commands that take a session, `=name:` for those that take a pane.
Passing the wrong form fails with "can't find pane".
"""

from __future__ import annotations

import logging
import os
import subprocess
import uuid

LOG = logging.getLogger(__name__)

SOCKET_ENV = "AGENTOP_TMUX_SOCKET"


def argv(*args: str) -> list[str]:
    """Build a tmux command line. AGENTOP_TMUX_SOCKET selects a private server."""
    socket = os.environ.get(SOCKET_ENV)
    return ["tmux", *(["-L", socket] if socket else []), *args]


def run(*args: str, timeout: float = 5, **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(argv(*args), capture_output=True, timeout=timeout, check=False, **kwargs)


def session_target(name: str) -> str:
    return f"={name}"


def pane_target(name: str) -> str:
    return f"={name}:"


_ERRORS = (subprocess.TimeoutExpired, subprocess.SubprocessError, FileNotFoundError)


class PasteError(RuntimeError):
    """Text could not be pasted into a session, so nothing should be submitted."""


class CapturePane:
    """Static helpers for capturing tmux pane content."""

    @staticmethod
    def screen(session: str) -> str:
        """Return the text currently visible on screen (no scrollback)."""
        try:
            r = run("capture-pane", "-t", pane_target(session), "-p", text=True)
            return r.stdout if r.returncode == 0 else ""
        except _ERRORS:
            return ""

    @staticmethod
    def scrollback(session: str) -> str:
        """Return the full scrollback history plus the current screen."""
        try:
            r = run("capture-pane", "-t", pane_target(session), "-p", "-S", "-", text=True)
            return r.stdout if r.returncode == 0 else ""
        except _ERRORS:
            return ""


class Session:
    """Static helpers for tmux session operations."""

    @staticmethod
    def has(name: str) -> bool:
        """Return True if the named tmux session exists."""
        try:
            return run("has-session", "-t", session_target(name), timeout=3).returncode == 0
        except _ERRORS:
            return False

    @staticmethod
    def list_all() -> dict[str, int] | None:
        """Return {session name: attached client count}, or None if tmux could not be asked.

        No server running is an empty dict, not an error.
        """
        try:
            r = run("list-sessions", "-F", "#{session_attached}|#{session_name}", timeout=3, text=True)
        except _ERRORS:
            return None
        if r.returncode != 0:
            return {} if "no server" in r.stderr or "No such file" in r.stderr else None
        sessions = {}
        for line in r.stdout.splitlines():
            attached, _, name = line.partition("|")
            sessions[name] = int(attached) if attached.isdigit() else 0
        return sessions

    @staticmethod
    def version() -> str | None:
        try:
            r = run("-V", timeout=3, text=True)
            return r.stdout.strip() if r.returncode == 0 else None
        except _ERRORS:
            return None

    @staticmethod
    def list_panes(name: str, fmt: str = "#{pane_pid}") -> list[str]:
        """Return pane field values for the given session using the given format string."""
        try:
            r = run("list-panes", "-t", pane_target(name), "-F", fmt, timeout=3, text=True)
            return r.stdout.strip().splitlines() if r.returncode == 0 else []
        except _ERRORS:
            return []

    @staticmethod
    def current() -> str | None:
        """The session this process runs in, from $TMUX_PANE, or None outside tmux."""
        pane = os.environ.get("TMUX_PANE")
        if not pane:
            return None
        try:
            r = run("display-message", "-p", "-t", pane, "#S", timeout=3, text=True)
        except _ERRORS:
            return None
        if r.returncode != 0:
            return None
        return r.stdout.strip() or None

    @staticmethod
    def send_keys(name: str, *keys: str) -> None:
        """Send one or more key strings to the session (each as a separate send-keys call)."""
        for key in keys:
            try:
                run("send-keys", "-t", pane_target(name), key)
            except _ERRORS:
                pass

    @staticmethod
    def leave_mode(name: str) -> None:
        """Take the session's pane out of copy mode (where scrolling puts it), so what is sent next reaches the program."""
        try:
            run("send-keys", "-t", pane_target(name), "-X", "cancel")
        except _ERRORS:
            pass

    @staticmethod
    def kill(name: str) -> None:
        """Kill the named tmux session."""
        try:
            run("kill-session", "-t", session_target(name))
        except _ERRORS:
            pass

    @staticmethod
    def new(name: str, cwd: str) -> bool:
        """Create a new detached session. Returns True on success."""
        try:
            return run("new-session", "-d", "-s", name, "-c", cwd).returncode == 0
        except _ERRORS:
            return False

    @staticmethod
    def set_option(name: str, option: str, value: str) -> bool:
        """Set an option on one session, leaving the global value and other sessions alone."""
        try:
            return run("set-option", "-t", pane_target(name), option, value).returncode == 0
        except _ERRORS:
            return False

    @staticmethod
    def fix_size(name: str, cols: int, rows: int) -> bool:
        """Give the session's window a size no attached client changes (`window-size manual`)."""
        try:
            if run("set-option", "-w", "-t", pane_target(name), "window-size", "manual").returncode != 0:
                return False
            return run("resize-window", "-t", pane_target(name), "-x", str(cols), "-y", str(rows)).returncode == 0
        except _ERRORS:
            return False

    @staticmethod
    def window_size(name: str) -> tuple[int, int] | None:
        """The (cols, rows) of the session's window, or None."""
        try:
            r = run("display-message", "-p", "-t", pane_target(name), "#{window_width} #{window_height}", timeout=3, text=True)
            cols, rows = (int(v) for v in r.stdout.split())
            return cols, rows
        except (*_ERRORS, ValueError):
            return None

    @staticmethod
    def _load_and_paste(name: str, payload: bytes, bracketed: bool = False) -> None:
        """Paste payload into the session through a buffer of its own. Raises PasteError.

        The buffer is named and deleted after: tmux's unnamed buffer is shared, so
        two sends at once (or a copy made in copy mode) could paste one session's
        prompt into another.

        paste-buffer writes the payload into the target pane's pty and can
        block until the receiving process consumes it — slower TUIs (or any
        load on the box) can make this take much longer than a quick local
        tmux call. A short timeout here silently drops large pastes, so this
        is generous.
        """
        buffer = f"agentop-{uuid.uuid4().hex}"
        try:
            r1 = run("load-buffer", "-b", buffer, "-", input=payload, timeout=15)
            if r1.returncode != 0:
                raise PasteError(f"[{name}] tmux load-buffer failed: {r1.stderr.decode(errors='replace').strip()}")
            r2 = run("paste-buffer", "-d", *(["-p"] if bracketed else []), "-b", buffer, "-t", pane_target(name), timeout=30)
            if r2.returncode != 0:
                raise PasteError(f"[{name}] tmux paste-buffer failed: {r2.stderr.decode(errors='replace').strip()}")
        except _ERRORS as exc:
            raise PasteError(f"[{name}] paste of {len(payload)} bytes failed: {exc}") from exc
        except PasteError:
            try:
                run("delete-buffer", "-b", buffer)
            except _ERRORS:
                pass
            raise

    @staticmethod
    def paste_text(name: str, text: str) -> None:
        """Paste text into the session. Raises PasteError."""
        Session._load_and_paste(name, text.encode())

    @staticmethod
    def paste_text_bracketed(name: str, text: str) -> None:
        """Paste text as one bracketed paste, so embedded newlines are not Enter presses. Raises PasteError.

        tmux adds the ESC[200~ ... ESC[201~ markers itself (paste-buffer -p),
        when the application has turned bracketed paste on. Markers written into
        the buffer would not work: tmux 3.5+ shows control characters in a paste
        as text, so the agent would get a literal "^[[200~".
        """
        Session._load_and_paste(name, text.encode(), bracketed=True)
