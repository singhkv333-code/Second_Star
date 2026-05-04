"""Versioned prompt loader + role-aware assembler.

Two paths:
  - `system_prompt()` → returns the raw system.md (legacy callers).
  - `build_system_prompt(role, ...)` → role-aware assembly with the
    domain primer + optional user context. New code uses this.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from backend.prompts.assembler import (
    PromptRole,
    UserContext,
    build_system_prompt,
    reload_prompts,
)


PROMPTS_DIR = Path(__file__).resolve().parent


@lru_cache(maxsize=1)
def system_prompt() -> str:
    """Legacy chat-role system prompt (raw system.md). New code should
    use `build_system_prompt(role='chat', ...)` which adds the domain
    primer and user context."""
    return (PROMPTS_DIR / "system.md").read_text(encoding="utf-8")


def reload_system_prompt() -> None:
    """Tests use this to pick up edits without restarting the process."""
    system_prompt.cache_clear()
    reload_prompts()


__all__ = [
    "system_prompt",
    "reload_system_prompt",
    "build_system_prompt",
    "PromptRole",
    "UserContext",
    "reload_prompts",
]
