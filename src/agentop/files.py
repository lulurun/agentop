"""Agent directory and project file scanning."""

from __future__ import annotations

import os
from pathlib import Path

AGENT_DIRS = [
    "~/.claude",
    "~/.codex",
    "~/.gemini",
    "~/.config/claude",
    "~/.config/codex",
    "~/.config/gemini",
    "~/.local/share/claude",
    "~/.local/share/codex",
]

_SCAN_MAX_DEPTH = 4


def _walk_depth(root: Path, max_depth: int):
    """Yield (Path, stat) for all files under root up to max_depth levels deep."""
    def _recurse(path: Path, depth: int):
        try:
            for entry in path.iterdir():
                try:
                    if entry.is_symlink():
                        continue
                    if entry.is_file():
                        yield entry, entry.stat()
                    elif entry.is_dir() and depth < max_depth:
                        yield from _recurse(entry, depth + 1)
                except (PermissionError, OSError):
                    continue
        except (PermissionError, OSError):
            pass

    yield from _recurse(root, 0)


def scan_agent_dirs(limit: int = 60) -> list[dict]:
    """Scan hidden agent directories and return recently modified file metadata."""
    files = []
    for dir_pattern in AGENT_DIRS:
        dir_path = Path(dir_pattern).expanduser()
        if not dir_path.exists():
            continue
        p = str(dir_path)
        if "claude" in p:
            tool = "claude"
        elif "gemini" in p:
            tool = "gemini"
        else:
            tool = "codex"
        for f, stat in _walk_depth(dir_path, _SCAN_MAX_DEPTH):
            suffix = f.suffix.lower()
            if suffix == ".jsonl":
                ftype = "jsonl"
            elif suffix == ".json":
                ftype = "json"
            elif suffix in (".db", ".sqlite", ".sqlite3"):
                ftype = "sqlite"
            elif suffix == ".log":
                ftype = "log"
            else:
                ftype = suffix[1:] if suffix else "file"
            files.append(
                {
                    "tool": tool,
                    "path": str(f),
                    "modified_time": stat.st_mtime,
                    "size_bytes": stat.st_size,
                    "type": ftype,
                }
            )
    files.sort(key=lambda x: x["modified_time"], reverse=True)
    return files[:limit]


def get_recent_project_files(cwd: str, limit: int = 10) -> list[str]:
    """Return paths of recently modified files in a project directory."""
    if not cwd or not os.path.isdir(cwd):
        return []
    entries: list[tuple[float, str]] = []
    try:
        for dirpath, dirnames, filenames in os.walk(cwd):
            dirnames[:] = [d for d in dirnames if d != ".git"]
            for fname in filenames:
                fpath = os.path.join(dirpath, fname)
                try:
                    entries.append((os.stat(fpath).st_mtime, fpath))
                except OSError:
                    continue
    except OSError:
        pass
    entries.sort(reverse=True)
    return [p for _, p in entries[:limit]]
