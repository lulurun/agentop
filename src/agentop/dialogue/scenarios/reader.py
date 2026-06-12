"""Load a scenario TOML file into a Scenario dataclass."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Scenario:
    name_a: str
    name_b: str
    role_a: str
    role_b: str


def load(path: str | Path) -> Scenario:
    """Load a scenario TOML file.

    Expected structure:
        name_a = "Alex"
        name_b = "Bob"
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
        return Scenario(
            name_a=data["name_a"],
            name_b=data["name_b"],
            role_a=data["role_a"]["prompt"],
            role_b=data["role_b"]["prompt"],
        )
    except KeyError as exc:
        raise ValueError(f"Scenario file {p} is missing key: {exc}") from exc
