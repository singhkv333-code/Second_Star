"""Phase-3 timing mapper — unit tests.

Covers ``timing.timing_to_trigger`` / ``timing.invalidation_spec``: that every
mode maps to the documented SPEC shape, that the canonical 50/30/20 ladder is
honoured for Hybrid, that EVENT rate views gate on a real ``scheduled_macro``
while THEME views gate on an ``indicator`` trend confirmation, that the
thesis-break invalidation is wired, and — critically — that EVERY emitted
trigger config validates against the REAL ``backend.workflows.schemas`` models
and that the mapper NEVER emits a prediction-market (PROGA) trigger.

Self-contained: only the shared ``make_curated_view`` fixtures + the real
trigger Pydantic models are used; no engines, no network, no DB writes beyond
the rolled-back flush the parent ``view_db`` fixture provides.
"""
from __future__ import annotations

from datetime import datetime

import pytest

from backend.view_markets.expressions import timing
from backend.workflows.schemas import (
    TriggerEventConfig,
    TriggerIndicatorConfig,
    TriggerScheduledMacroConfig,
    TriggerScheduleConfig,
)

# Map every step_type the mapper may emit to its real config model — used to
# validate each spec and to assert the PROGA triggers are never produced.
_TRIGGER_MODEL = {
    "trigger.schedule": TriggerScheduleConfig,
    "trigger.event": TriggerEventConfig,
    "trigger.indicator": TriggerIndicatorConfig,
    "trigger.scheduled_macro": TriggerScheduledMacroConfig,
}

_FORBIDDEN_STEP_TYPES = {"trigger.polymarket", "trigger.kalshi"}


def _validate_trigger(trigger: dict) -> None:
    """Assert a {step_type, config} spec is real + never a prediction market."""
    step_type = trigger["step_type"]
    assert step_type not in _FORBIDDEN_STEP_TYPES, "PROGA trigger leaked"
    model = _TRIGGER_MODEL.get(step_type)
    assert model is not None, f"unexpected step_type {step_type!r}"
    # Round-trips through the real registry model — proves the config is valid.
    model.model_validate(trigger["config"])


def _assert_spec_shape(spec: dict, *, expected_mode: str) -> None:
    assert set(spec) == {"mode", "tranches", "rebalance", "invalidation", "note"}
    assert spec["mode"] == expected_mode
    assert spec["note"] and "broker app" in spec["note"]
    assert isinstance(spec["tranches"], list) and spec["tranches"]
    total = 0
    for tr in spec["tranches"]:
        assert set(tr) == {"pct", "trigger"}
        assert isinstance(tr["pct"], int)
        total += tr["pct"]
        _validate_trigger(tr["trigger"])
    assert total == 100, "tranche pcts must sum to 100"
    if spec["invalidation"] is not None:
        _validate_trigger(spec["invalidation"])


# ── Pre-position ─────────────────────────────────────────────────────────────


def test_pre_position_arms_now_one_tranche(event_view):
    spec = timing.timing_to_trigger(event_view, "pre_position")
    _assert_spec_shape(spec, expected_mode="pre_position")
    assert len(spec["tranches"]) == 1
    tranche = spec["tranches"][0]
    assert tranche["pct"] == 100
    assert tranche["trigger"]["step_type"] == "trigger.schedule"
    # run_at is a real, parseable one-time fire (arm NOW), not a cron.
    run_at = tranche["trigger"]["config"]["run_at"]
    datetime.fromisoformat(run_at)
    assert "cron" not in tranche["trigger"]["config"]


# ── Confirmation ─────────────────────────────────────────────────────────────


def test_confirmation_event_gates_on_scheduled_macro(event_view):
    """An RBI-cut EVENT view → a real scheduled_macro(rbi_mpc, cut) gate."""
    spec = timing.timing_to_trigger(event_view, "confirmation")
    _assert_spec_shape(spec, expected_mode="confirmation")
    assert len(spec["tranches"]) == 1
    trig = spec["tranches"][0]["trigger"]
    assert trig["step_type"] == "trigger.scheduled_macro"
    assert trig["config"]["kind"] == "rbi_mpc"
    assert trig["config"]["expected_outcome"] == "cut"


def test_confirmation_theme_gates_on_indicator(theme_view):
    spec = timing.timing_to_trigger(theme_view, "confirmation")
    _assert_spec_shape(spec, expected_mode="confirmation")
    trig = spec["tranches"][0]["trigger"]
    assert trig["step_type"] == "trigger.indicator"
    # Manufacturing theme resolves to its ETF proxy (not the NIFTY fallback).
    assert trig["config"]["symbol"] == "MAKEINDIA"


def test_confirmation_non_macro_event_falls_back_to_news_event(make_curated_view):
    """An EVENT view with no recognisable rate macro degrades to trigger.event,
    never a fabricated scheduled_macro."""
    view = make_curated_view(
        view_type="event",
        title="Acme Corp wins the defence tender",
        thesis="A large order-book award re-rates Acme on the announcement.",
        category="corporate",
    )
    spec = timing.timing_to_trigger(view, "confirmation")
    trig = spec["tranches"][0]["trigger"]
    assert trig["step_type"] == "trigger.event"
    assert trig["config"]["keywords"]  # ≥1 keyword


def test_confirmation_relative_uses_indicator_regime(relative_view):
    spec = timing.timing_to_trigger(relative_view, "confirmation")
    trig = spec["tranches"][0]["trigger"]
    assert trig["step_type"] == "trigger.indicator"
    # No theme proxy on a relative view → honest NIFTY regime gate.
    assert trig["config"]["symbol"] == "NIFTY"


# ── Hybrid ───────────────────────────────────────────────────────────────────


def test_hybrid_splits_50_30_20_ladder(theme_view):
    spec = timing.timing_to_trigger(theme_view, "hybrid")
    _assert_spec_shape(spec, expected_mode="hybrid")
    pcts = [t["pct"] for t in spec["tranches"]]
    assert pcts == [50, 30, 20] == list(timing.TRANCHE_LADDER)
    # Starter is armed now; later adds are gated (not all schedule-now).
    assert spec["tranches"][0]["trigger"]["step_type"] == "trigger.schedule"
    # Each add smaller than the last (already asserted by the 50/30/20 order).
    follow = spec["tranches"][2]["trigger"]
    assert follow["step_type"] == "trigger.indicator"
    assert follow["config"]["value"] == 60.0  # stronger follow-through cross


def test_hybrid_event_middle_tranche_is_the_macro_gate(event_view):
    spec = timing.timing_to_trigger(event_view, "hybrid")
    mids = spec["tranches"][1]["trigger"]
    assert mids["step_type"] == "trigger.scheduled_macro"
    assert mids["config"]["kind"] == "rbi_mpc"


# ── Invalidation (thesis-break) ──────────────────────────────────────────────


def test_invalidation_event_is_opposite_macro_outcome(event_view):
    inval = timing.invalidation_spec(event_view)
    assert inval is not None
    assert inval["step_type"] == "trigger.scheduled_macro"
    assert inval["config"]["kind"] == "rbi_mpc"
    # A "cut" thesis breaks on a "hike".
    assert inval["config"]["expected_outcome"] == "hike"
    _validate_trigger(inval)


def test_invalidation_hold_event_has_no_single_opposite(make_curated_view):
    view = make_curated_view(
        view_type="event",
        title="RBI holds the repo rate at the next MPC",
        thesis="Sticky core inflation keeps the MPC on hold.",
        category="rates",
    )
    assert timing.invalidation_spec(view) is None


def test_invalidation_theme_wires_scenario_invalidate(make_curated_view):
    """A theme the seed map recognises gets a thesis-break event gate."""
    view = make_curated_view(
        view_type="theme",
        title="Monsoon drought basket",
        thesis="Build me a basket to profit from a deficient monsoon drought.",
        category="agri",
    )
    inval = timing.invalidation_spec(view)
    assert inval is not None
    assert inval["step_type"] == "trigger.event"
    assert inval["config"]["event_description"]  # the scenario's invalidate text
    _validate_trigger(inval)


def test_invalidation_unrecognised_theme_is_none(theme_view):
    # The declarative manufacturing thesis has no positioning verb → no scenario.
    assert timing.invalidation_spec(theme_view) is None


# ── PROGA guard (belt-and-braces across all modes/views) ─────────────────────


@pytest.mark.parametrize("mode", ["pre_position", "confirmation", "hybrid"])
def test_never_emits_prediction_market_trigger(
    mode, event_view, relative_view, theme_view
):
    for view in (event_view, relative_view, theme_view):
        spec = timing.timing_to_trigger(view, mode)
        # _assert_spec_shape validates every trigger incl. the PROGA guard.
        _assert_spec_shape(spec, expected_mode=mode)
