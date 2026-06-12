"""Tmux session scanning and control."""

from __future__ import annotations

import subprocess
import time

class CapturePane:
    """Static helpers for capturing tmux pane content."""

    @staticmethod
    def screen(session: str) -> str:
        """Return the text currently visible on screen (no scrollback)."""
        try:
            r = subprocess.run(
                ["tmux", "capture-pane", "-t", session, "-p"],
                capture_output=True, text=True, timeout=5,
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
                capture_output=True, text=True, timeout=5,
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
                capture_output=True, timeout=3,
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
                capture_output=True, text=True, timeout=3,
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
                    capture_output=True, timeout=5,
                )
            except (subprocess.TimeoutExpired, subprocess.SubprocessError, FileNotFoundError):
                pass

    @staticmethod
    def kill(name: str) -> None:
        """Kill the named tmux session."""
        try:
            subprocess.run(
                ["tmux", "kill-session", "-t", name],
                capture_output=True, timeout=5,
            )
        except (subprocess.TimeoutExpired, subprocess.SubprocessError, FileNotFoundError):
            pass

    @staticmethod
    def new(name: str, cwd: str) -> bool:
        """Create a new detached session. Returns True on success."""
        try:
            r = subprocess.run(
                ["tmux", "new-session", "-d", "-s", name, "-c", cwd],
                capture_output=True, timeout=5,
            )
            return r.returncode == 0
        except (subprocess.TimeoutExpired, subprocess.SubprocessError, FileNotFoundError):
            return False

    @staticmethod
    def paste_text(name: str, text: str) -> None:
        """Load text into the tmux buffer and paste it into the session."""
        try:
            subprocess.run(
                ["tmux", "load-buffer", "-"], input=text.encode(),
                capture_output=True, timeout=5,
            )
            subprocess.run(
                ["tmux", "paste-buffer", "-t", name],
                capture_output=True, timeout=5,
            )
        except (subprocess.TimeoutExpired, subprocess.SubprocessError, FileNotFoundError):
            pass


def scan_tmux() -> list[dict]:
    """Return list of tmux pane dicts."""
    try:
        result = subprocess.run(
            [
                "tmux", "list-panes", "-a", "-F",
                "#{session_name}|#{window_name}|#{pane_index}|#{pane_pid}|#{pane_current_path}|#{pane_current_command}",
            ],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0:
            return []
        panes = []
        for line in result.stdout.strip().splitlines():
            parts = line.split("|")
            if len(parts) == 6:
                pane_pid = int(parts[3]) if parts[3].isdigit() else None
                panes.append({
                    "session_name": parts[0],
                    "window_name": parts[1],
                    "pane_index": parts[2],
                    "pane_pid": pane_pid,
                    "pane_current_path": parts[4],
                    "pane_current_command": parts[5],
                })
        return panes
    except (FileNotFoundError, subprocess.TimeoutExpired, subprocess.SubprocessError):
        return []


def send_to_session(tmux_session: str, text: str) -> dict:
    """Send text followed by Enter to the given tmux session."""
    Session.send_keys(tmux_session, text, "Enter")
    return {"ok": True}


def stop_session(cwd: str, tmux_name: str) -> dict:
    """Send /exit to the agent session via tmux, then kill the tmux session."""
    sent_exit = False
    if tmux_name and Session.has(tmux_name):
        Session.send_keys(tmux_name, "/exit", "Enter")
        sent_exit = True
        time.sleep(3)

    tmux_killed = False
    if tmux_name and Session.has(tmux_name):
        Session.kill(tmux_name)
        tmux_killed = True

    return {"sent_exit": sent_exit, "tmux_killed": tmux_killed}
