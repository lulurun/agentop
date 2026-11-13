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
    def _load_and_paste(name: str, payload: bytes) -> bool:
        """Load payload into the tmux buffer and paste it into the session.

        paste-buffer writes the payload into the target pane's pty and can
        block until the receiving process consumes it — slower TUIs (or any
        load on the box) can make this take much longer than a quick local
        tmux call. A short timeout here silently drops large pastes, so this
        is generous and logs instead of swallowing failures.
        """
        try:
            r1 = run("load-buffer", "-", input=payload, timeout=15)
            if r1.returncode != 0:
                LOG.warning("[%s] tmux load-buffer failed: %s", name, r1.stderr.decode(errors="replace"))
                return False
            r2 = run("paste-buffer", "-t", pane_target(name), timeout=30)
            if r2.returncode != 0:
                LOG.warning("[%s] tmux paste-buffer failed: %s", name, r2.stderr.decode(errors="replace"))
                return False
            return True
        except _ERRORS as exc:
            LOG.warning("[%s] paste of %d bytes failed: %s", name, len(payload), exc)
            return False

    @staticmethod
    def paste_text(name: str, text: str) -> None:
        """Load text into the tmux buffer and paste it into the session."""
        Session._load_and_paste(name, text.encode())

    @staticmethod
    def paste_text_bracketed(name: str, text: str) -> None:
        """Paste text wrapped in bracketed-paste escape sequences.

        Bracketed paste (ESC[200~ ... ESC[201~) prevents the terminal from
        interpreting embedded newlines as Enter key presses, so the entire
        multi-line block is delivered as one atomic input.
        """
        bracketed = "\x1b[200~" + text + "\x1b[201~"
        Session._load_and_paste(name, bracketed.encode())
