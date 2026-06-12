"""Low-level tmux wrappers: pane capture and session control."""

from __future__ import annotations

import subprocess


class CapturePane:
    """Static helpers for capturing tmux pane content."""

    @staticmethod
    def screen(session: str) -> str:
        """Return the text currently visible on screen (no scrollback)."""
        try:
            r = subprocess.run(
                ["tmux", "capture-pane", "-t", session, "-p"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            return r.stdout if r.returncode == 0 else ""
        except (subprocess.TimeoutExpired, subprocess.SubprocessError, FileNotFoundError):
            return ""

    @staticmethod
    def scrollback(session: str) -> str:
        """Return the full scrollback history plus the current screen."""
        try:
            r = subprocess.run(
                ["tmux", "capture-pane", "-t", session, "-p", "-S", "-"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            return r.stdout if r.returncode == 0 else ""
        except (subprocess.TimeoutExpired, subprocess.SubprocessError, FileNotFoundError):
            return ""


class Session:
    """Static helpers for tmux session operations."""

    @staticmethod
    def has(name: str) -> bool:
        """Return True if the named tmux session exists."""
        try:
            r = subprocess.run(
                ["tmux", "has-session", "-t", name],
                capture_output=True,
                timeout=3,
            )
            return r.returncode == 0
        except (subprocess.TimeoutExpired, subprocess.SubprocessError, FileNotFoundError):
            return False

    @staticmethod
    def list_panes(name: str, fmt: str = "#{pane_pid}") -> list[str]:
        """Return pane field values for the given session using the given format string."""
        try:
            r = subprocess.run(
                ["tmux", "list-panes", "-t", name, "-F", fmt],
                capture_output=True,
                text=True,
                timeout=3,
            )
            return r.stdout.strip().splitlines() if r.returncode == 0 else []
        except (subprocess.TimeoutExpired, subprocess.SubprocessError, FileNotFoundError):
            return []

    @staticmethod
    def send_keys(name: str, *keys: str) -> None:
        """Send one or more key strings to the session (each as a separate send-keys call)."""
        for key in keys:
            try:
                subprocess.run(
                    ["tmux", "send-keys", "-t", name, key],
                    capture_output=True,
                    timeout=5,
                )
            except (subprocess.TimeoutExpired, subprocess.SubprocessError, FileNotFoundError):
                pass

    @staticmethod
    def kill(name: str) -> None:
        """Kill the named tmux session."""
        try:
            subprocess.run(
                ["tmux", "kill-session", "-t", name],
                capture_output=True,
                timeout=5,
            )
        except (subprocess.TimeoutExpired, subprocess.SubprocessError, FileNotFoundError):
            pass

    @staticmethod
    def new(name: str, cwd: str) -> bool:
        """Create a new detached session. Returns True on success."""
        try:
            r = subprocess.run(
                ["tmux", "new-session", "-d", "-s", name, "-c", cwd],
                capture_output=True,
                timeout=5,
            )
            return r.returncode == 0
        except (subprocess.TimeoutExpired, subprocess.SubprocessError, FileNotFoundError):
            return False

    @staticmethod
    def paste_text(name: str, text: str) -> None:
        """Load text into the tmux buffer and paste it into the session."""
        try:
            subprocess.run(
                ["tmux", "load-buffer", "-"],
                input=text.encode(),
                capture_output=True,
                timeout=5,
            )
            subprocess.run(
                ["tmux", "paste-buffer", "-t", name],
                capture_output=True,
                timeout=5,
            )
        except (subprocess.TimeoutExpired, subprocess.SubprocessError, FileNotFoundError):
            pass

    @staticmethod
    def paste_text_bracketed(name: str, text: str) -> None:
        """Paste text wrapped in bracketed-paste escape sequences.

        Bracketed paste (ESC[200~ ... ESC[201~) prevents the terminal from
        interpreting embedded newlines as Enter key presses, so the entire
        multi-line block is delivered as one atomic input.
        """
        bracketed = "\x1b[200~" + text + "\x1b[201~"
        try:
            subprocess.run(
                ["tmux", "load-buffer", "-"],
                input=bracketed.encode(),
                capture_output=True,
                timeout=5,
            )
            subprocess.run(
                ["tmux", "paste-buffer", "-t", name],
                capture_output=True,
                timeout=5,
            )
        except (subprocess.TimeoutExpired, subprocess.SubprocessError, FileNotFoundError):
            pass
