"""Load a scenario TOML file and return (prompt_a, prompt_b)."""

from __future__ import annotations

import tomllib
from pathlib import Path


def load(path: str | Path) -> tuple[str, str]:
    """Return (prompt_a, prompt_b) from a scenario TOML file.

    Expected structure:
        [role_a]
        prompt = "..."
        [role_b]
        prompt = "..."

    Prompts may use {name_a}, {name_b}, {topic}, {progress_file} placeholders.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Scenario file not found: {p}")
    with open(p, "rb") as f:
        data = tomllib.load(f)
    try:
        return data["role_a"]["prompt"], data["role_b"]["prompt"]
    except KeyError as exc:
        raise ValueError(f"Scenario file {p} is missing key: {exc}") from exc
