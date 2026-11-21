"""A record of recall searches and misses, to decide whether keyword search is enough.

See docs/0_12_03_memory.md, section 9: embeddings come in only if keyword
search misses too often, so misses have to be counted. Each search notes what
was searched for and how many messages and sessions it found. A miss is noted
on purpose, once a recall has ended: nothing was found after trying variants,
or what was found was the wrong thing. A search with no results is not a miss
by itself, since a recall tries many words before one matches.

Unlike the messages table this is not derived from the archive, so it is a
file of its own, `recall.jsonl`, one JSON object per line, appended to.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

# A miss takes the searches made just before it, from the same place, as what was tried.
TRIED_SECONDS = 30 * 60
TRIED_MOST = 20
KINDS = ("nothing", "wrong")
SOURCES = ("cli", "app")


class RecallLog:
    def __init__(self, path: Path):
        self.path = path

    def _append(self, entry: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # One short line in append mode, so the CLI and the service can both write.
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def entries(self) -> list[dict]:
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            return []
        found = []
        for line in lines:
            try:
                entry = json.loads(line)
            except ValueError:
                continue  # a line cut short by a crash
            if isinstance(entry, dict):
                found.append(entry)
        return found

    def search(self, query: str, messages: int, sessions: int, source: str) -> None:
        if query.strip():
            self._append(
                {"type": "search", "at": time.time(), "source": source, "q": query.strip(), "messages": messages, "sessions": sessions}
            )

    def miss(self, looked_for: str, kind: str, source: str, tried: list[str] | None = None) -> dict:
        """Note that a recall didn't find what was wanted. `tried` defaults to the searches just before."""
        if kind not in KINDS:
            raise ValueError(f"kind is one of {', '.join(KINDS)}, not {kind!r}")
        if not looked_for.strip():
            raise ValueError("say what was looked for")
        now = time.time()
        if tried is None:
            recent = [
                e["q"]
                for e in self.entries()
                if e.get("type") == "search" and e.get("source") == source and now - e.get("at", 0) <= TRIED_SECONDS
            ]
            tried = list(dict.fromkeys(reversed(recent)))[:TRIED_MOST][::-1]
        entry = {"type": "miss", "at": now, "source": source, "kind": kind, "looked_for": looked_for.strip(), "tried": tried}
        self._append(entry)
        return entry

    def misses(self, limit: int = 50) -> list[dict]:
        """The latest misses, newest first."""
        return [e for e in reversed(self.entries()) if e.get("type") == "miss"][:limit]

    def status(self) -> dict:
        entries = self.entries()
        searches = [e for e in entries if e.get("type") == "search"]
        misses = [e for e in entries if e.get("type") == "miss"]
        return {
            "searches": len(searches),
            "empty": sum(1 for e in searches if not e.get("messages") and not e.get("sessions")),
            "misses": len(misses),
            "wrong": sum(1 for e in misses if e.get("kind") == "wrong"),
            "last_miss": misses[-1] if misses else None,
        }
