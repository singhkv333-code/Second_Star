"""Typed tool-failure signals (chat-kernel, 2026-07-10).

``ToolRedirect`` replaces the old convention of embedding a prose
"Use <tool> ..." hint inside an error string for the chat loop to
regex-scan back out (`_ROUTE_HINT_RE`). That convention broke once
already: a 200-char error truncation severed the trailing hint and the
redirect silently never fired (fixed by raising the cap to 600 — the
root fragility stayed). A typed field cannot be severed.

The prose message STILL carries the hint text — it is what the LLM
reads as the tool result — but the routing decision now rides the
structured attribute, with the regex kept only as a fallback for
legacy raise-sites.
"""
from __future__ import annotations


class ToolRedirect(ValueError):
    """A tool refusing a request it structurally cannot express, while
    naming the tool that can. ``redirect_to`` must be a real tool name."""

    def __init__(self, message: str, *, redirect_to: str) -> None:
        super().__init__(message)
        self.redirect_to = redirect_to
