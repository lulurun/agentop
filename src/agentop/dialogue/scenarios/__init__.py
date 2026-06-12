"""Dialogue scenarios: load role prompts and agent names from TOML files."""

from __future__ import annotations

from pathlib import Path

_BUILTIN_DIR = Path(__file__).parent


def default_path() -> Path:
    return _BUILTIN_DIR / "pm-sde.toml"
