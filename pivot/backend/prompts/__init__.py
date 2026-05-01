"""Versioned prompt loader.

The system prompt lives in `system.md` so it can be diffed in PRs. Loaded once
at import time; if a hot-reload is needed in production we can swap to a
file-watcher cache, but a deploy is the right reload trigger.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path


PROMPTS_DIR = Path(__file__).resolve().parent


@lru_cache(maxsize=1)
def system_prompt() -> str:
    return (PROMPTS_DIR / "system.md").read_text(encoding="utf-8")


def reload_system_prompt() -> None:
    """Tests use this to pick up edits without restarting the process."""
    system_prompt.cache_clear()
