"""Layered macro-outcome verifier — hermetic unit tests.

Every dependency is injected (rss_fetch / llm_complete / pm_search) so
these run with zero network and a deterministic LLM. Covers the
safety-critical paths: confident match, negative outcome (no fire), the
evidence-substring anti-hallucination guard, CPI numeric comparison, and
the prediction-market fallback when the official tier is inconclusive.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from backend.macro_events.verifier import verify_macro_outcome


def _item(title: str, summary: str = "", url: str = "http://x") -> SimpleNamespace:
    return SimpleNamespace(title=title, summary=summary, url=url)


def _rss(items: list):
    async def _fetch(source_id: str, feed_url: str) -> list:
        return items
    return _fetch


def _llm(payload: dict):
    async def _complete(system: str, user: str) -> str:
        return json.dumps(payload)
    return _complete


def _pm(snaps: list):
    async def _search(query: str) -> list:
        return snaps
    return _search


pytestmark = pytest.mark.asyncio


async def test_rbi_cut_confirmed_by_official() -> None:
    headline = "RBI cuts repo rate by 25 bps to 6.00% as MPC eases policy"
    res = await verify_macro_outcome(
        "rbi_mpc", "cut",
        rss_fetch=_rss([_item(headline)]),
        llm_complete=_llm({
            "decision": "cut", "confidence": 0.97,
            "evidence": "RBI cuts repo rate by 25 bps",
        }),
        pm_search=_pm([]),
    )
    assert res.matched is True
    assert res.decision == "cut"
    assert res.tier == "official"
    assert res.audit["headline"] == headline


async def test_rbi_hold_does_not_fire_a_cut_trigger() -> None:
    headline = "RBI keeps repo rate unchanged at 6.50%, MPC holds policy"
    res = await verify_macro_outcome(
        "rbi_mpc", "cut",
        rss_fetch=_rss([_item(headline)]),
        llm_complete=_llm({
            "decision": "hold", "confidence": 0.96,
            "evidence": "RBI keeps repo rate unchanged",
        }),
        pm_search=_pm([]),
    )
    assert res.matched is False
    assert res.decision == "hold"  # the real outcome, authoritative


async def test_evidence_guard_rejects_fabricated_quote() -> None:
    """LLM 'confirms' a cut but its evidence quote is NOT in the source
    text → guard trips → unknown (no fire)."""
    headline = "RBI keeps repo rate unchanged at 6.50%"
    res = await verify_macro_outcome(
        "rbi_mpc", "cut",
        rss_fetch=_rss([_item(headline)]),
        llm_complete=_llm({
            "decision": "cut", "confidence": 0.99,
            "evidence": "the committee slashed rates by 50 basis points",
        }),
        pm_search=_pm([]),
    )
    assert res.matched is False
    assert res.decision == "unknown"


async def test_evidence_guard_rejects_offtopic_decision_word() -> None:
    """LLM 'confirms' a cut, and its evidence quote IS in the source, but
    the quote is about an unrelated 'cut' (to growth forecasts) — the
    on-topic context-keyword + word-boundary guard must reject it."""
    headline = ("MPC keeps the repo rate unchanged at 6.50% even as it "
                "flags cuts to global growth forecasts")
    res = await verify_macro_outcome(
        "rbi_mpc", "cut",
        rss_fetch=_rss([_item(headline)]),
        llm_complete=_llm({
            "decision": "cut", "confidence": 0.95,
            # verbatim in source, but about growth forecasts, not the rate:
            "evidence": "cuts to global growth forecasts",
        }),
        pm_search=_pm([]),
    )
    assert res.matched is False
    assert res.decision == "unknown"


async def test_evidence_guard_rejects_subword_match() -> None:
    """A bare 'cut' inside 'cutting' must not satisfy the guard (too short
    + word-boundary)."""
    headline = "RBI is cutting through red tape; repo rate held at 6.50%"
    res = await verify_macro_outcome(
        "rbi_mpc", "cut",
        rss_fetch=_rss([_item(headline)]),
        llm_complete=_llm({
            "decision": "cut", "confidence": 0.95, "evidence": "cut",
        }),
        pm_search=_pm([]),
    )
    assert res.matched is False


async def test_low_confidence_does_not_fire() -> None:
    headline = "RBI cuts repo rate by 25 bps"
    res = await verify_macro_outcome(
        "rbi_mpc", "cut",
        min_confidence=0.85,
        rss_fetch=_rss([_item(headline)]),
        llm_complete=_llm({
            "decision": "cut", "confidence": 0.50,
            "evidence": "RBI cuts repo rate by 25 bps",
        }),
        pm_search=_pm([]),
    )
    assert res.matched is False
    assert res.decision == "unknown"


async def test_no_matching_headline_then_pm_empty_is_unknown() -> None:
    res = await verify_macro_outcome(
        "rbi_mpc", "cut",
        rss_fetch=_rss([_item("Unrelated penalty on a co-op bank")]),
        llm_complete=_llm({"decision": "cut", "confidence": 0.9, "evidence": "x"}),
        pm_search=_pm([]),
    )
    assert res.matched is False
    assert res.decision == "unknown"


async def test_pm_fallback_confirms_when_official_inconclusive() -> None:
    """No matching official headline, but a resolved-YES market confirms
    the cut → fire via the prediction_market tier."""
    resolved = SimpleNamespace(
        closed=True, yes_price=0.98, market_id="0xabc",
        question="Will RBI cut the repo rate in June 2026?",
    )
    res = await verify_macro_outcome(
        "rbi_mpc", "cut",
        rss_fetch=_rss([]),                  # no official headline
        llm_complete=_llm({"decision": "unknown", "confidence": 0.0, "evidence": ""}),
        pm_search=_pm([resolved]),
    )
    assert res.matched is True
    assert res.tier == "prediction_market"
    assert res.audit["market_id"] == "0xabc"


async def test_pm_fallback_unresolved_market_does_not_fire() -> None:
    open_mkt = SimpleNamespace(
        closed=False, yes_price=0.62, market_id="0xdef", question="open mkt",
    )
    res = await verify_macro_outcome(
        "rbi_mpc", "cut",
        rss_fetch=_rss([]),
        llm_complete=_llm({"decision": "unknown", "confidence": 0.0, "evidence": ""}),
        pm_search=_pm([open_mkt]),
    )
    assert res.matched is False


async def test_us_cpi_numeric_comparison_met() -> None:
    headline = "US CPI rises to 3.4% in May, hotter than expected"
    res = await verify_macro_outcome(
        "us_cpi", "met",
        comparison=">", threshold=3.0,
        rss_fetch=_rss([_item(headline)]),
        llm_complete=_llm({
            "value": 3.4, "confidence": 0.95,
            "evidence": "US CPI rises to 3.4%",
        }),
        pm_search=_pm([]),
    )
    assert res.matched is True
    assert res.decision == "met"
    assert res.audit["value"] == 3.4


async def test_us_cpi_numeric_comparison_not_met() -> None:
    headline = "US CPI eases to 2.6% in May"
    res = await verify_macro_outcome(
        "us_cpi", "met",
        comparison=">", threshold=3.0,
        rss_fetch=_rss([_item(headline)]),
        llm_complete=_llm({
            "value": 2.6, "confidence": 0.95,
            "evidence": "US CPI eases to 2.6%",
        }),
        pm_search=_pm([]),
    )
    assert res.matched is False
    assert res.decision == "not_met"


async def test_unknown_kind_is_unknown() -> None:
    res = await verify_macro_outcome(
        "fii_flows", "met",
        rss_fetch=_rss([_item("x")]),
        llm_complete=_llm({}),
        pm_search=_pm([]),
    )
    assert res.matched is False
    assert res.decision == "unknown"
