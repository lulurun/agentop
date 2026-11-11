"""Agentop's own copy of the agent tools' transcripts. See docs/v2-design.md, section 12.11.

A sweep brings the archive up to date with the tools' files. The tools only
append to a transcript, so a grown file costs only its new bytes. Nothing is
ever deleted: a file the tool removes stays here, and a file the tool rewrote
keeps its old copy beside the new one.
"""

from __future__ import annotations

import json
import os
import shutil
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

from agentop.agentclis import AGENTS

SAMPLE_BYTES = 4096
MAX_ERRORS = 20


@dataclass(frozen=True)
class Source:
    tool: str
    root: Path
    files: Callable[[Path], Iterator[Path]]


def default_sources() -> list[Source]:
    """Each tool's transcripts root, and which of its files to keep: the tool's own module says."""
    return [Source(a.name, root, a.archive_files) for a in AGENTS if (root := a.transcripts_root())]


def _read_at(path: Path, offset: int, size: int) -> bytes:
    with open(path, "rb") as f:
        f.seek(offset)
        return f.read(size)


def _still_prefix(source: Path, copy: Path, length: int) -> bool:
    """Whether the copy's bytes are still the start of the source, judged by a sample at each end."""
    head = min(SAMPLE_BYTES, length)
    tail_at = max(0, length - SAMPLE_BYTES)
    return _read_at(source, 0, head) == _read_at(copy, 0, head) and _read_at(source, tail_at, length - tail_at) == _read_at(
        copy, tail_at, length - tail_at
    )


def _append(source: Path, copy: Path, offset: int) -> int:
    """Append the source's bytes from offset on. Returns the copy's new size."""
    size = offset
    with open(source, "rb") as src, open(copy, "ab") as dst:
        src.seek(offset)
        while chunk := src.read(1024 * 1024):
            dst.write(chunk)
            size += len(chunk)
    return size


def _copy(source: Path, copy: Path) -> int:
    copy.parent.mkdir(parents=True, exist_ok=True)
    tmp = copy.with_name(copy.name + ".partial")
    shutil.copyfile(source, tmp)
    os.replace(tmp, copy)
    return copy.stat().st_size


class Archive:
    def __init__(self, folder: Path, sources: list[Source] | None = None):
        self.folder = folder
        self.files_dir = folder / "archive"
        self.state_path = folder / "state.json"
        self.sources = sources if sources is not None else default_sources()
        self.state: dict = self._load()

    def _load(self) -> dict:
        try:
            state = json.loads(self.state_path.read_text())
            if isinstance(state, dict) and isinstance(state.get("files"), dict):
                return state
        except (OSError, ValueError):
            pass
        return {"files": {}, "last_sweep": None}

    def _save(self) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state))
        os.replace(tmp, self.state_path)

    def sweep(self, stop: Callable[[], bool] = lambda: False) -> dict:
        """Bring the archive up to date. Returns what this sweep did. `stop` ends it early; the next sweep carries on."""
        started = time.time()
        counts = {"copied": 0, "appended": 0, "replaced": 0, "unchanged": 0}
        errors: list[str] = []
        files = self.state["files"]
        for source in self.sources:
            if not source.root.is_dir():
                continue
            for path in source.files(source.root):
                if stop():
                    break
                key = f"{source.tool}/{path.relative_to(source.root)}"
                try:
                    counts[self._bring(key, path, files)] += 1
                except OSError as exc:
                    if len(errors) < MAX_ERRORS:
                        errors.append(f"{key}: {exc}")
        self.state["last_sweep"] = {
            "at": started,
            "seconds": round(time.time() - started, 3),
            **counts,
            "errors": errors,
        }
        self._save()
        return self.state["last_sweep"]

    def _bring(self, key: str, path: Path, files: dict) -> str:
        stat = path.stat()
        known = files.get(key)
        if known and known["size"] == stat.st_size and known["mtime"] == stat.st_mtime:
            return "unchanged"
        copy = self.files_dir / key
        result = "copied"
        if copy.exists():
            have = copy.stat().st_size
            if stat.st_size >= have and _still_prefix(path, copy, have):
                size = _append(path, copy, have) if stat.st_size > have else have
                files[key] = {"size": size, "mtime": stat.st_mtime}
                return "appended" if size > have else "unchanged"
            # Rewritten or shrunk: keep what was there, then take the new file.
            os.replace(copy, copy.with_name(f"{copy.name}.{int(time.time())}"))
            result = "replaced"
        files[key] = {"size": _copy(path, copy), "mtime": stat.st_mtime}
        return result

    def status(self) -> dict:
        files = self.state["files"]
        by_tool: dict[str, dict] = {}
        for key, info in files.items():
            tool = key.split("/", 1)[0]
            entry = by_tool.setdefault(tool, {"files": 0, "bytes": 0})
            entry["files"] += 1
            entry["bytes"] += info["size"]
        return {
            "folder": str(self.files_dir),
            "files": len(files),
            "bytes": sum(e["bytes"] for e in by_tool.values()),
            "tools": by_tool,
            "last_sweep": self.state.get("last_sweep"),
        }
