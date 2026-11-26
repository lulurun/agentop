"""Starting an agent in a tmux session of its own. The tool's AgentCli says what to run; this runs it the same way for every tool."""

from __future__ import annotations

import shlex
import tempfile
import time
from collections.abc import Callable
from contextlib import ExitStack
from typing import TYPE_CHECKING

import psutil

from agentop.results import StartFailed, StartResult
from agentop.tmux import CapturePane, Session

if TYPE_CHECKING:
    from agentop.agentclis.base import AgentCli


def launch(
    agent: AgentCli,
    cmd: str,
    cwd: str,
    short_name: str = "",
    params: dict | None = None,
    name: str | None = None,
    on_progress: Callable[[str], None] | None = None,
    agent_session_id: str | None = None,
) -> StartResult:
    """Run the agent in a new tmux shell and keep the pane available on failure."""
    progress = on_progress or (lambda step: None)
    if params:
        cmd = agent._build_cmd(cmd, params)
    name = name or agent.new_session_name(short_name)

    from agentop.signals import launch_flags

    if agent.name == "codex" and "--dangerously-bypass-hook-trust" not in cmd:
        cmd += " --dangerously-bypass-hook-trust"
    progress("creating")
    if not Session.new(name, cwd):
        raise StartFailed("tmux new-session failed")
    if agent.mouse:
        Session.set_option(name, "mouse", "on")

    with ExitStack() as files:
        try:
            flags = launch_flags(agent.name, name)
            pane_cmd = cmd
            if flags:
                # The terminal can truncate long input before the shell has initialized.
                script = files.enter_context(
                    tempfile.NamedTemporaryFile(mode="w", prefix="agentop-launch-", encoding="utf-8")
                )
                script.write(cmd + flags + "\n")
                script.flush()
                pane_cmd = f". {shlex.quote(script.name)}"
        except OSError as exc:
            raise StartFailed(f"Could not prepare {agent.name} launch: {exc}", name) from exc

        progress("launching")
        Session.send_keys(name, pane_cmd, "Enter")

        progress("waiting")
        lines = Session.list_panes(name, "#{pane_pid}")
        pane_pid = int(lines[0]) if lines and lines[0].isdigit() else None
        tool_pid = None
        deadline = time.monotonic() + agent.start_timeout
        while pane_pid and time.monotonic() < deadline:
            tool_pid = _find_agent_pid(agent, pane_pid)
            if tool_pid:
                break
            if _launch_failed(name, cmd):
                raise StartFailed(f"{cmd.split()[0]} is not installed, or not on the shell's PATH", name)
            time.sleep(0.25)

    if not tool_pid:
        raise StartFailed(f"{agent.name} did not start within {agent.start_timeout:g}s", name)

    progress("confirming")
    agent.post_start_hook(name)
    try:
        # With the pid, it tells this process from a later one given the same pid.
        started = psutil.Process(tool_pid).create_time()
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        started = None
    return StartResult(name, tool_pid, started, agent_session_id)


def _find_agent_pid(agent: AgentCli, pane_pid: int) -> int | None:
    """The agent's process among the pane's descendants."""
    try:
        for child in psutil.Process(pane_pid).children(recursive=True):
            try:
                if agent.matches(child.name(), " ".join(child.cmdline() or [])):
                    return child.pid
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        pass
    return None


def _launch_failed(session: str, cmd: str) -> bool:
    """Whether the shell has already said it can't run cmd."""
    binary = cmd.split()[0]
    screen = CapturePane.screen(session)
    return "command not found" in screen or f"{binary}: not found" in screen
