"""Versioned prompts. The version string is part of every recording key."""

from functools import cache
from importlib import resources
from typing import Final

PROMPT_VERSION: Final = "extraction-v1"


@cache
def load_prompt(version: str = PROMPT_VERSION) -> str:
    """Return the prompt text for ``version`` (file ``<version with _>.txt`` in this package)."""
    name = version.replace("-", "_") + ".txt"
    return resources.files(__package__).joinpath(name).read_text(encoding="utf-8").strip()
