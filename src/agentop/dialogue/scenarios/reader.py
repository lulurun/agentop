"""Load a scenario TOML file into a Scenario dataclass."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Scenario:
    name_a: str
    name_b: str
    role_a: str
    role_b: str
    max_turns: int | None = None
    opening: str | None = None  # optional kickoff prompt sent after role_a


def load(path: str | Path) -> Scenario:
    """Load a scenario TOML file.

    Supports two formats:

    New format (role names inside each section):
        name = "pm-sde"
        [settings]
        max_turns = 50
        [role_a]
        name = "PM"
        prompt = "..."
        [role_b]
        name = "SDE"
        prompt = "..."
        [opening]
        prompt = "..."

    Legacy format (role names at top level):
        name_a = "Alex"
        name_b = "Bob"
        [role_a]
        prompt = "..."
        [role_b]
        prompt = "..."
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Scenario file not found: {p}")
    with open(p, "rb") as f:
        data = tomllib.load(f)

    try:
        role_a_section = data.get("role_a", {})
        role_b_section = data.get("role_b", {})

        name_a = role_a_section.get("name") or data.get("name_a")
        name_b = role_b_section.get("name") or data.get("name_b")

        if not name_a or not name_b:
            raise KeyError("role name (role_a.name / role_b.name or top-level name_a / name_b)")

        max_turns = data.get("settings", {}).get("max_turns")
        opening = data.get("opening", {}).get("prompt")

        return Scenario(
            name_a=name_a,
            name_b=name_b,
            role_a=role_a_section["prompt"],
            role_b=role_b_section["prompt"],
            max_turns=max_turns,
            opening=opening,
        )
    except KeyError as exc:
        raise ValueError(f"Scenario file {p} is missing key: {exc}") from exc
