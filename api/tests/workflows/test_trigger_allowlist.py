"""Conservative-beta event-trigger allow-list guardrail.

Covers `backend.workflows.propose.validate_trigger_allowlist` (the
authoritative gate) directly, plus one integration pass through
`validate_draft_against_registry` to prove an excluded `trigger.event`
never survives validation.

The gate accepts only the (A)/(B)/(C) families:
  (A) trigger.scheduled_macro with kind ∈ {rbi_mpc,us_fomc,india_cpi,us_cpi}
  (B) trigger.polymarket / trigger.kalshi
  (C) trigger.expiry_day / trigger.ipo_open
and refuses open-ended / out-of-scope event triggers (war, monsoon,
elections, FII/DII flows, index rebalance, …) with planner-actionable
guidance pointing at the nearest real alternative.
"""
from __future__ import annotations

import pytest

from backend.workflows.propose import (
    DraftStep,
    ProposalValidationError,
    WorkflowDraft,
    validate_draft_against_registry,
    validate_trigger_allowlist,
)


def _draft(*steps: DraftStep) -> WorkflowDraft:
    return WorkflowDraft(name="t", steps=list(steps))


# ── trigger.event: unverifiable / out-of-scope markers are refused ───


@pytest.mark.parametrize(
    "marker_text",
    [
        "war breaks out with Pakistan",
        "a ceasefire is announced",
        "the monsoon is below normal",
        "a major flood hits Mumbai",
        "the election verdict is declared",
        "FII flows turn net positive",
        "the NIFTY index rebalance is announced",
    ],
)
def test_unverifiable_event_trigger_is_refused(marker_text: str) -> None:
    draft = _draft(
        DraftStep(
            step_type="trigger.event",
            config={"keywords": [marker_text], "event_description": marker_text},
        ),
    )
    with pytest.raises(ProposalValidationError) as exc:
        validate_trigger_allowlist(draft)
    # The refusal must steer the planner to a real alternative, not just
    # reject — that's the whole point of the conservative gate.
    msg = str(exc.value).lower()
    assert "trigger.event" in msg
    assert "strategy" in msg or "trigger.polymarket" in msg


def test_marker_matched_in_keywords_only() -> None:
    """The hay includes keywords, not just event_description."""
    draft = _draft(
        DraftStep(
            step_type="trigger.event",
            config={"keywords": ["war"], "event_description": "geopolitical risk"},
        ),
    )
    with pytest.raises(ProposalValidationError):
        validate_trigger_allowlist(draft)


# ── trigger.event: legitimate verifiable macro phrasings pass ────────


@pytest.mark.parametrize(
    "kw,desc",
    [
        (["RBI", "repo rate", "rate cut"], "RBI announces a repo rate cut"),
        (["MPC", "monetary policy"], "RBI MPC outcome"),
        (["FOMC", "Fed"], "Fed cuts the federal funds rate"),
        (["CPI", "inflation"], "India CPI print released"),
    ],
)
def test_verifiable_event_trigger_passes(kw: list[str], desc: str) -> None:
    draft = _draft(
        DraftStep(
            step_type="trigger.event",
            config={"keywords": kw, "event_description": desc},
        ),
    )
    # Must not raise.
    validate_trigger_allowlist(draft)


# ── trigger.scheduled_macro: kind must be on the allow-list ──────────


@pytest.mark.parametrize("kind", ["rbi_mpc", "us_fomc", "india_cpi", "us_cpi"])
def test_allowed_macro_kind_passes(kind: str) -> None:
    draft = _draft(
        DraftStep(
            step_type="trigger.scheduled_macro",
            config={"kind": kind, "expected_outcome": "cut"},
        ),
    )
    validate_trigger_allowlist(draft)


@pytest.mark.parametrize("kind", ["fii_flows", "index_rebalance", "monsoon", ""])
def test_disallowed_macro_kind_is_refused(kind: str) -> None:
    draft = _draft(
        DraftStep(
            step_type="trigger.scheduled_macro",
            config={"kind": kind, "expected_outcome": "met"},
        ),
    )
    with pytest.raises(ProposalValidationError) as exc:
        validate_trigger_allowlist(draft)
    assert "trigger.scheduled_macro" in str(exc.value)


# ── non-event triggers pass untouched ────────────────────────────────


def test_price_trigger_passes() -> None:
    draft = _draft(
        DraftStep(
            step_type="trigger.price",
            config={"symbol": "RELIANCE", "operator": ">", "value": 3000},
        ),
    )
    validate_trigger_allowlist(draft)


# ── integration: full registry validation rejects an excluded event ──


def test_full_validation_rejects_thematic_event_trigger() -> None:
    """A registry-valid trigger.event whose content is thematic must
    still be rejected by validate_draft_against_registry (the allow-list
    runs after per-step Pydantic validation)."""
    raw = {
        "name": "war play",
        "steps": [
            {
                "step_type": "trigger.event",
                "config": {
                    "keywords": ["war", "conflict"],
                    "event_description": "war breaks out with Pakistan",
                },
            },
            {
                "step_type": "action.place_order",
                "config": {
                    "symbol": "HAL",
                    "side": "buy",
                    "quantity": 1,
                    "order_type": "market",
                },
            },
        ],
    }
    with pytest.raises(ProposalValidationError) as exc:
        validate_draft_against_registry(raw)
    assert "war" in str(exc.value).lower()


def test_full_validation_accepts_verifiable_event_trigger() -> None:
    raw = {
        "name": "rbi cut buy",
        "steps": [
            {
                "step_type": "trigger.event",
                "config": {
                    "keywords": ["RBI", "repo rate", "rate cut"],
                    "event_description": "RBI announces a repo rate cut",
                },
            },
            {
                "step_type": "action.place_order",
                "config": {
                    "symbol": "NIFTYBEES",
                    "side": "buy",
                    "quantity": 1,
                    "order_type": "market",
                },
            },
        ],
    }
    draft = validate_draft_against_registry(raw)
    assert draft.steps[0].step_type == "trigger.event"
