"""Read-only views of a session's working directory.

Paths are confined to that directory. Symlinks are resolved before the check,
so a link cannot lead out of it, and are listed without being followed.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

MAX_ENTRIES = 1000
MAX_CHANGES = 500

# A repository's own config can name a command for git to run on `status`. The
# agent has the run of this directory, but merely looking at it must not execute
# anything.
_GIT = ("git", "-c", "core.fsmonitor=false", "-c", "core.quotePath=false")


class OutsideRoot(Exception):
    """The path leads out of the session's directory."""


def resolve_inside(root: str, relative: str) -> tuple[Path, Path]:
    """Return (real root, real target), refusing a target outside the root."""
    real_root = Path(os.path.realpath(root))
    target = Path(os.path.realpath(real_root / relative.lstrip("/")))
    if target != real_root and real_root not in target.parents:
        raise OutsideRoot(relative)
    return real_root, target


def _git(root: Path, *args: str, stdin: str | None = None) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(
            [*_GIT, "-C", str(root), *args], capture_output=True, text=True, input=stdin, timeout=10, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None


def _ignored(root: Path, paths: list[str]) -> set[str]:
    """Return the paths git ignores. Outside a repository, nothing is."""
    if not paths:
        return set()
    result = _git(root, "check-ignore", "--stdin", stdin="\n".join(paths) + "\n")
    # 0: some are ignored. 1: none are. Anything else: not a repository.
    return set(result.stdout.splitlines()) if result and result.returncode == 0 else set()


def list_directory(root: str, relative: str = "") -> dict:
    """List one directory, folders first, leaving out what git ignores."""
    real_root, target = resolve_inside(root, relative)
    with os.scandir(target) as it:
        entries = [e for e in it if e.name != ".git"]
    entries.sort(key=lambda e: e.name.lower())

    prefix = target.relative_to(real_root)
    ignored = _ignored(real_root, [str(prefix / e.name) for e in entries])
    rows = []
    for entry in entries:
        path = str(prefix / entry.name)
        if path not in ignored:
            is_link = entry.is_symlink()
            rows.append({"name": entry.name, "path": path, "is_dir": entry.is_dir(follow_symlinks=False), "is_link": is_link})
    rows.sort(key=lambda r: not r["is_dir"])
    return {
        "root": str(real_root),
        "path": "" if str(prefix) == "." else str(prefix),
        "entries": rows[:MAX_ENTRIES],
        "truncated": len(rows) > MAX_ENTRIES,
    }


def _parse_status(output: str) -> dict:
    """Parse `git status --porcelain=v2 --branch -z`."""
    branch = None
    ahead = behind = 0
    files = []
    records = iter(output.split("\0"))
    for record in records:
        if record.startswith("# branch.head "):
            branch = record.removeprefix("# branch.head ")
        elif record.startswith("# branch.ab "):
            parts = record.split()
            ahead, behind = int(parts[2]), -int(parts[3])
        elif record[:2] == "1 ":
            fields = record.split(" ", 8)
            files.append({"path": fields[8], "status": fields[1].replace(".", "")[:1] or "M"})
        elif record[:2] == "2 ":
            fields = record.split(" ", 9)
            files.append({"path": fields[9], "status": "R"})
            next(records, None)  # the old path
        elif record[:2] == "u ":
            files.append({"path": record.split(" ", 10)[10], "status": "U"})
        elif record[:2] == "? ":
            files.append({"path": record[2:], "status": "?"})
    return {"branch": None if branch == "(detached)" else branch, "ahead": ahead, "behind": behind, "files": files}


def git_changes(root: str) -> dict:
    """Return branch and changed files. Paths are relative to the repository root."""
    real_root, _ = resolve_inside(root, "")
    result = _git(real_root, "status", "--porcelain=v2", "--branch", "-z")
    if result is None or result.returncode != 0:
        return {"is_repo": False, "branch": None, "ahead": 0, "behind": 0, "files": [], "truncated": False}
    status = _parse_status(result.stdout)
    files = status["files"]
    return {"is_repo": True, **status, "files": files[:MAX_CHANGES], "truncated": len(files) > MAX_CHANGES}
