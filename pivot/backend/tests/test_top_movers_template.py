"""Top-movers agent template — the deterministic builder that turns
"buy the top gainers/losers …" into a valid workflow draft over the wired
fetch.top_movers → action.allocate_notional chain.

Regression guard for the reported failure where "create me an agent that
buys the top gainers after the open and sells them before the close"
refused to build (the free-form planner collapsed it to one symbol or
failed validation)."""
from __future__ import annotations

import asyncio

from backend.workflows.propose import (
    _top_movers_template,
    propose_workflow_async,
    validate_draft_against_registry,
)


def _types(draft) -> list[str]:
    return [s.step_type for s in draft.steps]


def test_open_to_close_gainers_builds_and_validates() -> None:
    intent = (
        "create me an agent that buys the top gainers after the open and "
        "sells them before the close. 5 names. "
        "Capital budget per trade / position: 10K."
    )
    draft = _top_movers_template(intent)
    assert draft is not None
    # Registry + lint validation must pass (raises otherwise).
    v = validate_draft_against_registry(draft.model_dump())
    assert _types(v) == [
        "trigger.market_relative_time",
        "fetch.top_movers",
        "action.allocate_notional",
        "trigger.market_relative_time",
        "action.squareoff",
    ]
    fetch = v.steps[1].config
    assert fetch["direction"] == "gainers"
    assert fetch["limit"] == 5
    alloc = v.steps[2].config
    assert alloc["symbols"] == "{{ context.1.symbols }}"
    assert alloc["side"] == "buy"
    assert alloc["total_inr"] == 10_000
    assert v.steps[3].config["anchor"] == "close"
    assert v.steps[4].config["scope"] == "all"


def test_buy_only_losers_no_exit_leg() -> None:
    draft = _top_movers_template("buy the top 3 losers at the open with 1L")
    assert draft is not None
    v = validate_draft_against_registry(draft.model_dump())
    assert _types(v) == [
        "trigger.market_relative_time",
        "fetch.top_movers",
        "action.allocate_notional",
    ]
    assert v.steps[1].config["direction"] == "losers"
    assert v.steps[1].config["limit"] == 3
    assert v.steps[2].config["total_inr"] == 100_000
    # "at the open" → no offset.
    assert v.steps[0].config["offset_minutes"] == 0


def test_pure_read_is_not_an_agent() -> None:
    # A read ("show me …") must not be turned into an automation.
    assert _top_movers_template("show me today's top gainers") is None
    assert _top_movers_template("what are the biggest losers right now") is None


def test_explicit_short_bails_out() -> None:
    # Live basket shorts aren't wired — never silently build a LONG basket
    # of the losers. Bail so the normal planner handles / declines.
    assert _top_movers_template("short the biggest losers today") is None


def test_default_budget_is_flagged() -> None:
    draft = _top_movers_template("buy the top 5 gainers each morning")
    assert draft is not None
    assert draft.steps[2].config["total_inr"] == 25_000
    assert any("assumed" in w for w in draft.warnings)


def test_async_entry_point_uses_template() -> None:
    # propose_workflow_async runs the template BEFORE any LLM/mock, so this
    # resolves deterministically even without LLM keys.
    intent = "buy the top gainers after the open, sell before the close, 5 names, 10k"
    draft = asyncio.run(propose_workflow_async(intent))
    assert draft.steps[1].step_type == "fetch.top_movers"
    assert draft.steps[2].step_type == "action.allocate_notional"
