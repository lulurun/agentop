"""The instructions loaded only into project Coordinator sessions."""

from pathlib import Path

PATH = Path(__file__).with_name("SKILL.md")
RULES = Path(__file__).with_name("RULES.md")


def body() -> str:
    """Return the skill body without its discovery metadata."""
    text = PATH.read_text()
    if not text.startswith("---\n"):
        raise ValueError("Coordinator skill has no frontmatter")
    _, separator, content = text[4:].partition("\n---\n")
    if not separator or not content.strip():
        raise ValueError("Coordinator skill has no body")
    return content.strip()


def rules() -> str:
    return RULES.read_text().strip()
