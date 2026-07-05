"""B1/B2 — build_strategy `symbols` allow-list (pinned universe) + factor themes.

Hermetic: the fundamentals-DB and price-history fetches are monkeypatched so the
PINNING + factor-routing logic is exercised without Azure / Kite round-trips.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backend.services import strategy_builder as sb
from backend.services.strategy_contracts import SlotState
from backend.services.weighting import compute_weights


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    # No price history → covariance schemes fall back to equal-weight (honestly
    # disclosed); keeps the build deterministic + fully offline.
    monkeypatch.setattr(sb, "_fetch_price_history", lambda syms: {s: [] for s in syms})

    # Batch gate-input fetch: give two names real ratios, leave the rest silent
    # (so we can assert the "(no data)" honesty on the pinned names the DB lacks).
    def _fake_gate_inputs(candidates):
        data = {
            "RELIANCE": dict(roe=9.0, roce=10.0, de=0.4, pe=24.0),
            "TCS": dict(roe=45.0, roce=55.0, de=0.1, pe=28.0),
        }
        for c in candidates:
            d = data.get(c.symbol.upper())
            if not d:
                continue
            c.roe, c.roce, c.de, c.pe = d["roe"], d["roce"], d["de"], d["pe"]
            if c.pe:
                c.earnings_yield = round(1.0 / c.pe, 4)

    monkeypatch.setattr(sb, "_backfill_gate_inputs", _fake_gate_inputs)
    # Full per-name fetch is a no-op (keeps the batch data set above).
    monkeypatch.setattr(sb, "_backfill_fundamentals_parallel", lambda candidates: None)


# ── B1: pinned universe ──────────────────────────────────────────────────────


def test_pins_exact_universe_in_order_none_dropped():
    pinned = ["RELIANCE", "TCS", "ZZZUNKNOWN", "INFY", "HDFCBANK"]
    card = sb.build_strategy("basket of these", SlotState(), ctx=None, symbols=pinned)
    assert [c.symbol for c in card.constituents] == pinned
    total = sum(c.weight_pct for c in card.constituents)
    assert abs(total - 100.0) < 1.0


def test_pinned_missing_data_kept_with_empty_metrics_and_honest_note():
    pinned = ["RELIANCE", "TCS", "ZZZUNKNOWN"]
    card = sb.build_strategy("pin these", SlotState(), ctx=None, symbols=pinned)
    by_sym = {c.symbol: c for c in card.constituents}
    # A name the DB is silent on is kept, but shown WITHOUT fabricated metrics.
    assert by_sym["ZZZUNKNOWN"].gate_metrics == {}
    assert by_sym["TCS"].gate_metrics  # has real ratios
    assert any(
        "no data" in a.lower() or "no db fundamentals" in a.lower()
        for a in card.assumptions
    )
    # The pin disclosure is present.
    assert any("pinned universe" in a.lower() for a in card.assumptions)


def test_pinned_sector_cap_is_advisory_not_enforced():
    # Six IT names — well over the ~32% single-sector cap. Nothing may be dropped.
    pinned = ["TCS", "INFY", "HCLTECH", "WIPRO", "LTIM", "TECHM"]
    card = sb.build_strategy("all-IT pinned basket", SlotState(), ctx=None, symbols=pinned)
    assert [c.symbol for c in card.constituents] == pinned
    assert any("advisor" in a.lower() for a in card.assumptions)


def test_symbols_thread_via_slot_state():
    # No explicit kwarg — the pin travels in-band on slots.symbols (clarify path).
    slots = SlotState(symbols=["RELIANCE", "TCS", "INFY"])
    card = sb.build_strategy("x", slots, ctx=None)
    assert {c.symbol for c in card.constituents} == {"RELIANCE", "TCS", "INFY"}


def test_capital_echoed_onto_card():
    slots = SlotState(capital_inr=200000.0)
    card = sb.build_strategy("x", slots, ctx=None, symbols=["RELIANCE", "TCS", "INFY"])
    assert card.capital_inr == 200000.0


# ── B2: factor themes ────────────────────────────────────────────────────────


def test_momentum_theme_routes_to_factor_scheme():
    card = sb.build_strategy(
        "build me a strategy that benefits from momentum", SlotState(), ctx=None
    )
    assert card.weighting_scheme == "factor"
    assert len(card.constituents) > 0


def test_factor_emphasis_tilts_weights_toward_momentum():
    n = 200
    up = pd.Series(np.linspace(100.0, 200.0, n))    # strong positive momentum
    down = pd.Series(np.linspace(200.0, 120.0, n))  # negative momentum
    ph = {"UP": up, "DOWN": down}
    w = compute_weights(["UP", "DOWN"], "factor", price_history=ph, factor_emphasis="momentum")
    assert w["UP"] > w["DOWN"]
