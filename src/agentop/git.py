"""Git repository metadata."""

from __future__ import annotations

import os
import subprocess
from typing import Optional


def get_git_info(cwd: str) -> Optional[dict]:
    """Return git metadata for a directory, or None if not a git repo."""
    if not cwd or not os.path.isdir(cwd):
        return None
    try:
        r = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=cwd,
        )
        if r.returncode != 0:
            return None
        repo_root = r.stdout.strip()

        branch_r = subprocess.run(
            ["git", "branch", "--show-current"],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=cwd,
        )
        branch = branch_r.stdout.strip() if branch_r.returncode == 0 else None

        status_r = subprocess.run(
            ["git", "status", "--short"],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=cwd,
        )
        dirty = bool(status_r.stdout.strip()) if status_r.returncode == 0 else False

        log_r = subprocess.run(
            ["git", "log", "-1", "--oneline"],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=cwd,
        )
        latest_commit = log_r.stdout.strip() if log_r.returncode == 0 else None

        return {
            "repo_root": repo_root,
            "branch": branch,
            "dirty": dirty,
            "latest_commit": latest_commit,
        }
    except (subprocess.TimeoutExpired, subprocess.SubprocessError, FileNotFoundError):
        return None
