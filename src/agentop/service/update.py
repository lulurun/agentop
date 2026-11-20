"""Starts update.sh and reports on it. See design 8.4.

An update restarts the service, so it has to outlive the service. Under systemd
it runs in a transient unit of its own, since stopping the service's unit could
take its children with it. Otherwise it runs as a detached process. Either way
it reports through a status file.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
import tomllib
from pathlib import Path

from agentop.paths import data_dir, state_dir

from .manager import ServiceManager

SCRIPT = Path(__file__).with_name("update.sh")
UPDATE_UNIT = "agentop-update"
FETCH_TIMEOUT_SECONDS = 20
CHECK_CACHE_SECONDS = 60
LATEST_CACHE_SECONDS = 60 * 60
RELEASE_TAG = re.compile(r"v\d+\.\d+\.\d+")
COMMIT_LENGTH = 10

_checked: dict[str, tuple[float, dict]] = {}
_latest: dict[str, tuple[float, dict]] = {}
_running: dict[str, str | None] = {}


class UpdateError(RuntimeError):
    pass


def status_path() -> Path:
    return state_dir() / "update-status.json"


def read_status() -> dict | None:
    try:
        return json.loads(status_path().read_text())
    except (OSError, ValueError):
        return None


def pid_path() -> Path:
    return state_dir() / "update.pid"


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


def latest_release(repo: Path) -> str | None:
    """The newest vX.Y.Z tag the repository has, by version order."""
    tags = _git(repo, "tag", "-l", "v*", "--sort=-v:refname").splitlines()
    return next((t for t in tags if RELEASE_TAG.fullmatch(t)), None)


def _version_key(tag: str) -> tuple[int, ...]:
    return tuple(int(p) for p in tag.removeprefix("v").split("."))


def _package_version(repo: Path) -> str | None:
    try:
        return tomllib.loads((repo / "pyproject.toml").read_text())["project"]["version"]
    except (OSError, KeyError, tomllib.TOMLDecodeError):
        return None


def running_version(repo: Path, update_from: str) -> str | None:
    """X.Y.Z following releases, else the commit. Kept from the first call: the checkout can move under a running service."""
    key = f"{repo}:{update_from}"
    if key not in _running:
        found = None
        if update_from != "release":
            try:
                found = _git(repo, "rev-parse", "HEAD")[:COMMIT_LENGTH]
            except (OSError, subprocess.SubprocessError):
                pass
        _running[key] = found or _package_version(repo)
    return _running[key]


def _newer_on_remote(repo: Path, update_from: str, current: str) -> str | None:
    if update_from == "release":
        refs = _git(repo, "ls-remote", "--tags", "--refs", "origin", "v*", timeout=FETCH_TIMEOUT_SECONDS)
        tags = [line.rsplit("refs/tags/", 1)[-1] for line in refs.splitlines()]
        newest = max((t for t in tags if RELEASE_TAG.fullmatch(t)), key=_version_key, default=None)
        try:
            return newest[1:] if newest and _version_key(newest) > _version_key(current) else None
        except ValueError:
            return newest[1:] if newest else None
    found = _git(repo, "ls-remote", "origin", "refs/heads/main", timeout=FETCH_TIMEOUT_SECONDS).split()
    if not found or found[0].startswith(current):
        return None
    # The checkout may be ahead of the remote, as on a machine that commits to it.
    contained = subprocess.run(["git", "merge-base", "--is-ancestor", found[0], "HEAD"], cwd=repo, capture_output=True, check=False)
    return None if contained.returncode == 0 else found[0][:COMMIT_LENGTH]


def latest(repo: Path, update_from: str) -> dict:
    """What Update would move to, in the form of running_version: the same string when nothing is newer. Asked at most hourly."""
    key = f"{repo}:{update_from}"
    cached = _latest.get(key)
    if cached and time.monotonic() - cached[0] < LATEST_CACHE_SECONDS:
        return cached[1]
    current = running_version(repo, update_from)
    result: dict = {"latest": current, "checked_at": time.time(), "check_error": None}
    try:
        result["latest"] = _newer_on_remote(repo, update_from, current or "") or current
    except subprocess.TimeoutExpired:
        result["check_error"] = "The git remote did not answer."
    except (OSError, subprocess.CalledProcessError) as exc:
        stderr = getattr(exc, "stderr", "") or ""
        result["check_error"] = stderr.strip().splitlines()[-1] if stderr.strip() else "git failed."
    _latest[key] = (time.monotonic(), result)
    return result


def _release_behind(repo: Path, tag: str) -> int:
    """Commits from HEAD to the tag, or 0 when HEAD already has it: an update never goes backwards."""
    if subprocess.run(["git", "merge-base", "--is-ancestor", "HEAD", tag], cwd=repo, capture_output=True, check=False).returncode:
        return 0
    return int(_git(repo, "rev-list", "--count", f"HEAD..{tag}"))


def available(repo: Path, update_from: str = "main") -> dict:
    """What an update would bring. Cached briefly, because it asks the remote.

    For a release, `upstream` is the tag. For main, it is the branch's newest commit.
    """
    key = f"{repo}:{update_from}"
    cached = _checked.get(key)
    if cached and time.monotonic() - cached[0] < CHECK_CACHE_SECONDS:
        return cached[1]
    result: dict = {"current": None, "upstream": None, "behind": None, "check_error": None, "update_from": update_from}
    try:
        result["current"] = _git(repo, "rev-parse", "--short", "HEAD")
        if update_from == "release":
            _git(repo, "fetch", "--quiet", "--tags", "origin", timeout=FETCH_TIMEOUT_SECONDS)
            tag = latest_release(repo)
            if tag is None:
                result["check_error"] = "There is no release yet."
            else:
                result["upstream"] = tag
                result["behind"] = _release_behind(repo, tag)
        else:
            _git(repo, "fetch", "--quiet", timeout=FETCH_TIMEOUT_SECONDS)
            result["upstream"] = _git(repo, "rev-parse", "--short", "@{upstream}")
            result["behind"] = int(_git(repo, "rev-list", "--count", "HEAD..@{upstream}"))
    except subprocess.TimeoutExpired:
        result["check_error"] = "The git remote did not answer."
    except (OSError, subprocess.CalledProcessError) as exc:
        stderr = getattr(exc, "stderr", "") or ""
        result["check_error"] = stderr.strip().splitlines()[-1] if stderr.strip() else "git failed."
    _checked[key] = (time.monotonic(), result)
    return result


def launch(repo: Path, health_url: str, manager: ServiceManager, update_from: str = "main") -> None:
    """Start the update. Returns once it is running, not once it has finished."""
    if is_running():
        raise UpdateError("An update is already running.")
    for key in [k for k in _checked if k.startswith(f"{repo}:")]:
        del _checked[key]
    settings = {
        "AGENTOP_REPO": str(repo),
        "AGENTOP_HOME": str(data_dir()),
        "AGENTOP_HEALTH_URL": health_url,
        "AGENTOP_UPDATE_FROM": update_from,
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
