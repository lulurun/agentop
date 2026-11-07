"""Print the /api/v2 schema. The web client's types are generated from it."""

from __future__ import annotations

import json

from .config import Settings
from .identity import Identity
from .routes import Context, create_v2_app
from .store import SessionStore


def schema() -> dict:
    return create_v2_app(Context(Settings(), Identity("", "", ""), SessionStore())).openapi()


def render() -> str:
    return json.dumps(schema(), indent=2, sort_keys=True) + "\n"


if __name__ == "__main__":
    print(render(), end="")
