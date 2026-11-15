"""Dialogue scenarios: load role prompts and agent names from TOML files.

A scenario can carry a `description`: the template of its brief, in Markdown,
with `{placeholder}` fields. The app's New dialogue wizard asks for each field
and saves the filled-in description as the dialogue's brief.md.
"""

from __future__ import annotations

import re
from pathlib import Path

_BUILTIN_DIR = Path(__file__).parent

# Filled in by the dialogue itself, never asked for.
RESERVED = frozenset({"name_a", "name_b", "progress_file", "brief", "topic"})
# A scenario without a description gets this one: the whole brief in one field.
DEFAULT_DESCRIPTION = "# {title}\n\n{brief_text}\n"

_PLACEHOLDER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


def default_path() -> Path:
    return _BUILTIN_DIR / "implementation-requirements.toml"


def catalog() -> dict[str, Path]:
    """The built-in scenarios by ID: the file name without .toml, `shelf/` in front for the older ones."""
    found = {p.stem: p for p in sorted(_BUILTIN_DIR.glob("*.toml"))}
    found.update({f"shelf/{p.stem}": p for p in sorted((_BUILTIN_DIR / "shelf").glob("*.toml"))})
    return found


def placeholders(description: str) -> list[str]:
    """The fields a description asks for, in the order they first appear."""
    seen: list[str] = []
    for name in _PLACEHOLDER.findall(description):
        if name not in RESERVED and name not in seen:
            seen.append(name)
    return seen


def fill(description: str, values: dict[str, str], reserved: dict[str, str] | None = None) -> str:
    """The description with every field replaced. Text in a value is never read as a placeholder."""
    known = {**(reserved or {}), **values}
    return _PLACEHOLDER.sub(lambda m: known.get(m.group(1), m.group(0)), description)
