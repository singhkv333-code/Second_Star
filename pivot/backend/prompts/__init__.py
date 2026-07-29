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
    """Legacy chat-role system prompt. New code should use
    `build_system_prompt(role='chat', ...)` which adds the domain primer,
    intent packs, and user context. Reads the lean `system_core.md`
    (falls back to the old monolith) so a stray legacy caller still gets
    the live core rather than the retired system.md."""
    for name in ("system_core.md", "system.md"):
        p = PROMPTS_DIR / name
        if p.exists():
            return p.read_text(encoding="utf-8")
    return ""


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
