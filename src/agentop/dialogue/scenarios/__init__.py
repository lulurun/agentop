"""Dialogue scenarios: load role prompts from TOML description files."""

from __future__ import annotations

from pathlib import Path

from agentop.dialogue.scenarios.reader import load

_BUILTIN_DIR = Path(__file__).parent


def default_path() -> Path:
    return _BUILTIN_DIR / "pm-sde.toml"
