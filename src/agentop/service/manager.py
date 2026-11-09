"""Who runs this service, and so who can stop or restart it. See design 8.2."""

from __future__ import annotations

import os
import shlex
import shutil
import signal
import subprocess
import sys
from collections.abc import Callable
from typing import Protocol

COMMAND_TIMEOUT_SECONDS = 15
# Asks a service started by hand to restart itself. Not SIGHUP: closing its terminal sends that.
RESTART_SIGNAL = signal.SIGUSR1

_exit: Callable[[], None] | None = None


def serving_here(exit: Callable[[], None]) -> None:
    """Called by `agentop serve` once it restarts itself on RESTART_SIGNAL, with how to make it exit."""
    global _exit
    _exit = exit


def unit_name() -> str:
    return os.environ.get("AGENTOP_UNIT", "agentop-api.service")


class ManagerError(RuntimeError):
    pass


class ServiceManager(Protocol):
    name: str
    can_control: bool

    def stop(self) -> None: ...
    def restart(self) -> None: ...
    def restart_command(self) -> str: ...


class NoManager:
    """Nothing can act on this process: it isn't `agentop serve`, as in tests."""

    name = "none"
    can_control = False

    def _refuse(self) -> None:
        raise ManagerError("This service can't control itself.")

    stop = restart = _refuse

    def restart_command(self) -> str:
        self._refuse()
        return ""


class SelfManager:
    """Started by hand with `agentop serve`. It restarts and stops itself."""

    name = "self"
    can_control = True

    def stop(self) -> None:
        if _exit:
            _exit()

    def restart(self) -> None:
        os.kill(os.getpid(), RESTART_SIGNAL)

    def restart_command(self) -> str:
        """A shell command for the updater, which outlives this process.

        It signals this process and waits for it to stop answering, so that the
        updater's health check meets the new version. If the process is gone,
        because a new version failed to start, it starts it again in the
        background, with the same command line, logging to serve.log.
        """
        from agentop.paths import data_dir

        pid = os.getpid()
        name = RESTART_SIGNAL.name.removeprefix("SIG")
        again = shlex.join([sys.executable, *sys.orig_argv[1:]])
        log = shlex.quote(str(data_dir() / "serve.log"))
        return (
            f"if ps -o args= -p {pid} | grep -q serve && kill -{name} {pid}; then "
            'for _ in $(seq 100); do curl -fsSk -m 1 "$HEALTH_URL" >/dev/null 2>&1 || break; sleep 0.1; done; '
            f"else (cd {shlex.quote(os.getcwd())} && nohup {again} >>{log} 2>&1 </dev/null &); fi"
        )


class SystemdManager:
    name = "systemd"
    can_control = True

    def __init__(self, unit: str | None = None):
        self.unit = unit or unit_name()

    def _systemctl(self, *args: str) -> subprocess.CompletedProcess:
        try:
            result = subprocess.run(
                ["systemctl", "--user", *args], capture_output=True, text=True, timeout=COMMAND_TIMEOUT_SECONDS, check=False
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ManagerError(f"systemctl failed: {exc}") from exc
        return result

    def _act(self, *args: str) -> None:
        result = self._systemctl(*args)
        if result.returncode != 0:
            raise ManagerError(result.stderr.strip() or f"systemctl {args[0]} failed")

    def _properties(self) -> dict[str, str]:
        output = self._systemctl("show", self.unit, "-p", "MainPID").stdout
        return dict(line.split("=", 1) for line in output.splitlines() if "=" in line)

    def main_pid(self) -> int | None:
        pid = self._properties().get("MainPID", "0")
        return int(pid) if pid.isdigit() and int(pid) else None

    # --no-block, so that a service acting on itself doesn't wait for its own death.
    def stop(self) -> None:
        self._act("stop", "--no-block", self.unit)

    def restart(self) -> None:
        self._act("restart", "--no-block", self.unit)

    def restart_command(self) -> str:
        unit = shlex.quote(self.unit)
        return f"systemctl --user daemon-reload && systemctl --user restart {unit}"


def _systemd() -> SystemdManager | None:
    return SystemdManager() if shutil.which("systemctl") else None


def for_process(pid: int) -> ServiceManager:
    """For the service itself. A copy started by hand must not act on the unit, but can act on itself."""
    systemd = _systemd()
    try:
        if systemd and systemd.main_pid() == pid:
            return systemd
    except ManagerError:
        pass
    return SelfManager() if _exit and pid == os.getpid() else NoManager()
