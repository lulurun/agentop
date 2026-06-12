"""Dialogue scenarios: role definitions for agent A and agent B."""

from __future__ import annotations

import importlib


def load(name: str) -> tuple[str, str]:
    """Return (role_a, role_b) prompt templates for the named scenario.

    Templates may use {name_a}, {name_b}, {topic}, {progress_file}.
    """
    try:
        mod = importlib.import_module(f"agentop.dialogue.scenarios.{name}")
    except ModuleNotFoundError:
        raise ValueError(f"Unknown scenario: {name!r}")
    return mod.ROLE_A, mod.ROLE_B
