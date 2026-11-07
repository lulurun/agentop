"""Who runs this service, and so who can stop or restart it. See design 8.2."""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from typing import Protocol

COMMAND_TIMEOUT_SECONDS = 15


def unit_name() -> str:
    return os.environ.get("AGENTOP_UNIT", "agentop-api.service")


class ManagerError(RuntimeError):
    pass


@dataclass(frozen=True)
class ManagerStatus:
    active: bool
    pid: int | None = None


class ServiceManager(Protocol):
    name: str
    can_control: bool

    def status(self) -> ManagerStatus: ...
    def start(self) -> None: ...
    def stop(self) -> None: ...
    def restart(self) -> None: ...


class NoManager:
    """The service was started by hand, so nothing can start it again after a stop."""

    name = "none"
    can_control = False

    def status(self) -> ManagerStatus:
        return ManagerStatus(active=False)

    def _refuse(self) -> None:
        raise ManagerError("This service is not managed by systemd.")

    start = stop = restart = _refuse


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
        output = self._systemctl("show", self.unit, "-p", "LoadState,ActiveState,MainPID").stdout
        return dict(line.split("=", 1) for line in output.splitlines() if "=" in line)

    def installed(self) -> bool:
        return self._properties().get("LoadState") == "loaded"

    def main_pid(self) -> int | None:
        pid = self._properties().get("MainPID", "0")
        return int(pid) if pid.isdigit() and int(pid) else None

    def status(self) -> ManagerStatus:
        properties = self._properties()
        return ManagerStatus(active=properties.get("ActiveState") == "active", pid=self.main_pid())

    def start(self) -> None:
        self._act("start", self.unit)

    # --no-block, so that a service acting on itself doesn't wait for its own death.
    def stop(self) -> None:
        self._act("stop", "--no-block", self.unit)

    def restart(self) -> None:
        self._act("restart", "--no-block", self.unit)


def _systemd() -> SystemdManager | None:
    return SystemdManager() if shutil.which("systemctl") else None


def for_cli() -> ServiceManager:
    """For commands run beside the service: is there a unit installed on this machine?"""
    systemd = _systemd()
    try:
        return systemd if systemd and systemd.installed() else NoManager()
    except ManagerError:
        return NoManager()


def for_process(pid: int) -> ServiceManager:
    """For the service itself. A copy started by hand must not act on the unit."""
    systemd = _systemd()
    try:
        return systemd if systemd and systemd.main_pid() == pid else NoManager()
    except ManagerError:
        return NoManager()
