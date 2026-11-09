"""Starts update.sh and reports on it. See design 8.4.

An update restarts the service, so it has to outlive the service. Under systemd
it runs in a transient unit of its own, since stopping the service's unit could
take its children with it. Otherwise it runs as a detached process. Either way
it reports through a status file.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from agentop.paths import data_dir

from .manager import ServiceManager

SCRIPT = Path(__file__).with_name("update.sh")
UPDATE_UNIT = "agentop-update"
FETCH_TIMEOUT_SECONDS = 20
CHECK_CACHE_SECONDS = 60

_checked: dict[str, tuple[float, dict]] = {}


class UpdateError(RuntimeError):
    pass


def status_path() -> Path:
    return data_dir() / "update-status.json"


def read_status() -> dict | None:
    try:
        return json.loads(status_path().read_text())
    except (OSError, ValueError):
        return None


def pid_path() -> Path:
    return data_dir() / "update.pid"


def _unit_running() -> bool:
    try:
        result = subprocess.run(
            ["systemctl", "--user", "is-active", f"{UPDATE_UNIT}.service"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.stdout.strip() in ("active", "activating")


def _process_running() -> bool:
    try:
        pid = int(pid_path().read_text())
    except (OSError, ValueError):
        return False
    # It may be this process's child, even after a restart, since a restart keeps the process.
    try:
        reaped, _ = os.waitpid(pid, os.WNOHANG)
        return reaped == 0
    except ChildProcessError:
        pass
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def is_running() -> bool:
    return _process_running() or (bool(shutil.which("systemd-run")) and _unit_running())


def _git(repo: Path, *args: str, timeout: int = 10) -> str:
    env = {**os.environ, "GIT_SSH_COMMAND": "ssh -o BatchMode=yes", "GIT_TERMINAL_PROMPT": "0"}
    return subprocess.run(
        ["git", *args], cwd=repo, env=env, capture_output=True, text=True, timeout=timeout, check=True
    ).stdout.strip()


def available(repo: Path) -> dict:
    """What an update would bring. Cached briefly, because it asks the remote."""
    cached = _checked.get(str(repo))
    if cached and time.monotonic() - cached[0] < CHECK_CACHE_SECONDS:
        return cached[1]
    result: dict = {"current": None, "upstream": None, "behind": None, "check_error": None}
    try:
        result["current"] = _git(repo, "rev-parse", "--short", "HEAD")
        _git(repo, "fetch", "--quiet", timeout=FETCH_TIMEOUT_SECONDS)
        result["upstream"] = _git(repo, "rev-parse", "--short", "@{upstream}")
        result["behind"] = int(_git(repo, "rev-list", "--count", "HEAD..@{upstream}"))
    except subprocess.TimeoutExpired:
        result["check_error"] = "The git remote did not answer."
    except (OSError, subprocess.CalledProcessError) as exc:
        stderr = getattr(exc, "stderr", "") or ""
        result["check_error"] = stderr.strip().splitlines()[-1] if stderr.strip() else "git failed."
    _checked[str(repo)] = (time.monotonic(), result)
    return result


def launch(repo: Path, health_url: str, manager: ServiceManager) -> None:
    """Start the update. Returns once it is running, not once it has finished."""
    if is_running():
        raise UpdateError("An update is already running.")
    _checked.pop(str(repo), None)
    settings = {
        "AGENTOP_REPO": str(repo),
        "AGENTOP_HOME": str(data_dir()),
        "AGENTOP_HEALTH_URL": health_url,
        "AGENTOP_PYTHON": sys.executable,
        "AGENTOP_RESTART_CMD": manager.restart_command(),
        "PATH": os.environ.get("PATH", ""),
    }
    script = os.environ.get("AGENTOP_UPDATE_SCRIPT", str(SCRIPT))
    if manager.name == "systemd":
        command = ["systemd-run", "--user", f"--unit={UPDATE_UNIT}", "--collect", "--quiet"]
        command += [f"--setenv={key}={value}" for key, value in settings.items()]
        command += ["/bin/bash", script]
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=15, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise UpdateError(f"Could not start the update: {exc}") from exc
        if result.returncode != 0:
            raise UpdateError(result.stderr.strip() or "Could not start the update.")
        return
    try:
        process = subprocess.Popen(
            ["/bin/bash", script],
            env={**os.environ, **settings},
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as exc:
        raise UpdateError(f"Could not start the update: {exc}") from exc
    pid_path().parent.mkdir(parents=True, exist_ok=True)
    pid_path().write_text(str(process.pid))
