"""Running a tool's one-shot command: one prompt on stdin, one answer on stdout, with no tmux and no session."""

from __future__ import annotations

import subprocess
import time
from collections.abc import Callable
from pathlib import Path


def run(tool: str, cmd: list[str], prompt: str, cwd: Path, timeout: float, stop: Callable[[], bool]) -> str:
    """What cmd printed. Raises RuntimeError with the reason when it fails, takes over timeout, or stop() turns true."""
    cwd.mkdir(parents=True, exist_ok=True)
    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            errors="replace",
            cwd=cwd,
        )
    except OSError as e:
        raise RuntimeError(f"{tool} did not run: {e}") from None
    deadline = time.monotonic() + timeout
    given: str | None = prompt  # stdin is written on the first call only
    while True:
        try:
            stdout, stderr = proc.communicate(given, timeout=0.5)
            break
        except subprocess.TimeoutExpired:
            given = None
            if stop() or time.monotonic() > deadline:
                proc.kill()
                proc.communicate()
                raise RuntimeError(f"{tool} {'was stopped' if stop() else f'took over {timeout:g}s'}") from None
    if proc.returncode != 0:
        detail = (stderr or stdout).strip().splitlines()
        raise RuntimeError(f"{tool} failed ({proc.returncode}): {detail[-1] if detail else 'no output'}")
    return stdout
