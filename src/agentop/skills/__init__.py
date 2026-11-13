"""The skills agentop ships, and installing them into an agent tool's skills folder.

Each skill is a folder beside this file with a SKILL.md. `agentop serve` copies
them into every installed tool's skills folder (AgentCli.skills_dir) as it
starts, and replaces a copy it made when the skill has changed. A copy carries
a marker file; a folder without one is the user's own, and is left alone.
"""

from __future__ import annotations

import filecmp
import logging
import shutil
from dataclasses import dataclass
from pathlib import Path

LOG = logging.getLogger(__name__)

HERE = Path(__file__).resolve().parent
MARKER = ".agentop"
_MARKER_TEXT = (
    "Installed by agentop serve, which replaces this folder whenever the skill changes.\n"
    "Delete this file to keep your own edits; agentop then leaves the folder alone.\n"
)

INSTALLED = "installed"
UPDATED = "updated"
UNCHANGED = "unchanged"
LINKED = "linked"  # a link to agentop's own copy, which is always current
SKIPPED = "skipped"  # the user's own folder or link


@dataclass(frozen=True)
class Result:
    name: str
    action: str
    path: Path


def bundled() -> list[Path]:
    """The skill folders agentop ships."""
    return sorted(p for p in HERE.iterdir() if p.is_dir() and (p / "SKILL.md").is_file())


def install(root: Path) -> list[Result]:
    """Install or update every bundled skill under root."""
    root.mkdir(parents=True, exist_ok=True)
    return [_install_one(source, root / source.name) for source in bundled()]


def _install_one(source: Path, dest: Path) -> Result:
    def result(action: str) -> Result:
        return Result(source.name, action, dest)

    if dest.is_symlink():
        if dest.exists():
            return result(LINKED if dest.resolve() == source else SKIPPED)
        dest.unlink()  # broken, such as a link to where the skills used to be in the repo
        _copy(source, dest)
        return result(INSTALLED)
    if not dest.exists():
        _copy(source, dest)
        return result(INSTALLED)
    if not (dest / MARKER).is_file():
        return result(SKIPPED)
    if _same(source, dest):
        return result(UNCHANGED)
    staged = dest.with_name(f".{dest.name}.agentop-new")
    shutil.rmtree(staged, ignore_errors=True)
    _copy(source, staged)
    shutil.rmtree(dest)
    staged.rename(dest)
    return result(UPDATED)


def _copy(source: Path, dest: Path) -> None:
    shutil.copytree(source, dest, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    (dest / MARKER).write_text(_MARKER_TEXT)


def _same(source: Path, dest: Path) -> bool:
    def files(base: Path) -> set[Path]:
        return {
            p.relative_to(base)
            for p in base.rglob("*")
            if p.is_file() and p.name != MARKER and "__pycache__" not in p.parts and p.suffix != ".pyc"
        }

    names = files(source)
    if names != files(dest):
        return False
    return all(filecmp.cmp(source / n, dest / n, shallow=False) for n in names)
