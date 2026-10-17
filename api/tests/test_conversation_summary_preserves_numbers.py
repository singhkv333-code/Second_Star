"""Regression guard: the conversation-summarizer's system prompt must
instruct the LLM to preserve exact numeric results verbatim.

Reported 2026-07-14 (live eval): asked to recall a backtest's win rate
9 turns later, the model refused ("can't reliably see... don't want to
invent") even though the real figures were sent to the backend that
turn — verified they'd simply been paraphrased away by the whole-
conversation summarizer (`generate_and_store`'s LLM prompt) that bridges
turns older than the raw history window. A lightweight source check
(not a full DB-backed integration test — `generate_and_store` needs a
real SQLAlchemy session) so a future edit can't silently drop this
clause.
"""
from __future__ import annotations

import inspect

from backend.services import conversation_summary


def test_summarizer_prompt_source_preserves_numeric_results():
    src = inspect.getsource(conversation_summary.generate_and_store)
    assert "verbatim" in src.lower()
    assert "win rate" in src.lower() or "return" in src.lower()
