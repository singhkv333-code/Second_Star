"""Tests for the pure Stage 2 keyword evaluator.

Table-driven. The DB-bound variant is exercised in
``test_stage1_stage2_integration.py``; here we just nail the
semantics of ``evaluate_keyword_set`` so the planner LLM has a clear
contract to target in Phase 4.
"""
from __future__ import annotations

import pytest

from backend.news_events.pipeline.keyword import evaluate_keyword_set
from backend.news_events.schemas import KeywordSet


def _ks(**kwargs) -> KeywordSet:
    return KeywordSet.model_validate(kwargs)


@pytest.mark.parametrize(
    "title,summary,keyword_set,expected",
    [
        # 1) Empty set passes everything.
        ("Anything goes here", "x", _ks(), True),
        # 2) must_have_one — single hit suffices.
        (
            "RBI cuts repo rate",
            None,
            _ks(must_have_one=["repo rate", "policy rate"]),
            True,
        ),
        # 3) must_have_one — no hit → fail.
        (
            "Sensex closes higher",
            None,
            _ks(must_have_one=["RBI", "repo"]),
            False,
        ),
        # 4) must_have_one_of — outer AND, inner OR.
        (
            "RBI announces fresh rate decision",
            None,
            _ks(must_have_one_of=[["RBI", "Reserve Bank"], ["rate", "policy"]]),
            True,
        ),
        # 5) must_have_one_of — one inner list fails ⇒ overall fail.
        (
            "RBI announces fresh decision",
            None,
            _ks(must_have_one_of=[["RBI"], ["rate", "policy"]]),
            False,
        ),
        # 6) must_not_have — hit rejects even if must_have passes.
        (
            "RBI repo rate cut, says analyst speculatively",
            None,
            _ks(
                must_have_one=["RBI"],
                must_not_have=["speculatively"],
            ),
            False,
        ),
        # 7) Case insensitivity — "rbi" in title matches "RBI" in keyword.
        (
            "rbi statement on liquidity",
            None,
            _ks(must_have_one=["RBI"]),
            True,
        ),
        # 8) Substring matches inside summary too.
        (
            "Market update",
            "...weighing the RBI's stance on the repo...",
            _ks(must_have_one=["RBI"]),
            True,
        ),
        # 9) Empty inner list inside must_have_one_of is a no-op (vacuous).
        (
            "Sensex closes higher",
            None,
            _ks(must_have_one_of=[[]]),
            True,
        ),
        # 10) Whitespace + blanks in keywords are stripped at model
        #     validation time, so an effectively-empty must_have_one
        #     becomes a no-op.
        (
            "Sensex closes higher",
            None,
            _ks(must_have_one=["", "   "]),
            True,
        ),
    ],
)
def test_evaluate_keyword_set_table(title, summary, keyword_set, expected):
    assert (
        evaluate_keyword_set(
            title=title,
            summary=summary,
            keyword_set=keyword_set,
        )
        is expected
    )
