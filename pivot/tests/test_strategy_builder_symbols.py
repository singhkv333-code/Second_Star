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


def test_explicit_gold_answer_builds_the_sleeve_even_without_other_signals():
    # Reported 2026-07-14: user picked "a roughly balanced mix of equity and
    # gold" in the basket-structure clarify question, but risk="balanced" and
    # horizon="medium" (neither "earns" the sleeve on the old heuristic) meant
    # gold was silently dropped and the card was titled "Diversified Equity
    # Basket" with zero gold exposure. gold_requested must override that.
    from backend.services.strategy_contracts import AssetPrefs
    slots = SlotState(asset_prefs=AssetPrefs(gold_requested=True))
    card = sb.build_strategy(
        "make me a basket", slots, ctx=None,
        symbols=["RELIANCE", "TCS", "INFY"],
    )
    assert any(s.kind == "gold" for s in card.sleeves)
    assert "gold" in card.title.lower()


def test_no_gold_signal_still_skips_the_sleeve():
    slots = SlotState()  # default: risk=balanced, horizon=medium, no hedge cue
    card = sb.build_strategy(
        "make me a basket", slots, ctx=None,
        symbols=["RELIANCE", "TCS", "INFY"],
    )
    assert not any(s.kind == "gold" for s in card.sleeves)
    assert "gold" not in card.title.lower()


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


def test_thematic_ask_no_longer_auto_seeded():
    """2026-07-17: the deterministic thematic seed was REMOVED — the model
    reasons out and PINS the beneficiaries itself (symbols + symbol_reasons;
    thematic.md carries the pattern). A bare scenario request must NOT get
    code-injected symbols anymore."""
    from backend.agents.tool_executor import _slot_state_from_args

    req = "Make me a basket of stocks that profit from a good monsoon."
    slots = _slot_state_from_args({"request": req})
    assert not slots.symbols  # no code-side pin; model owns name selection


def test_thematic_seed_backstop_respects_explicit_symbols():
    """An explicit model-provided pin must NOT be overwritten by the seed."""
    from backend.agents.tool_executor import _slot_state_from_args

    slots = _slot_state_from_args({
        "request": "profit from a good monsoon",
        "symbols": ["SHAKTIPUMP", "KSB"],
    })
    assert slots.symbols == ["SHAKTIPUMP", "KSB"]


def test_no_seed_on_non_scenario_request():
    from backend.agents.tool_executor import _slot_state_from_args

    slots = _slot_state_from_args({
        "request": "build me a long-term quality portfolio",
    })
    assert not slots.symbols


# ── Exclusion-constraint regression (2026-07-14 eval finding) ────────────────
#
# An "exclude PSU" basket-build request shipped ONGC/IOC/COALINDIA anyway.
# Two gaps, confirmed by reading the pipeline: (1) `_apply_exclusions` only
# ever matched `"psu" in sector`, which is true for `psu_bank` alone — real
# PSUs also sit in `energy`/`metals`/`defence`; (2) the PINNED allow-list path
# (`_build_pinned_strategy` — used for explicit model-provided `symbols` AND
# the deterministic thematic-scenario seed, e.g. crude_spike -> ONGC/OIL)
# never called `_apply_exclusions` at all.


def test_apply_exclusions_drops_non_bank_psu_names():
    """'psu' must drop ONGC/COALINDIA (energy/metals PSUs), not just psu_bank."""
    candidates = [
        sb._Candidate(symbol="ONGC", name="ONGC", sector="energy"),
        sb._Candidate(symbol="COALINDIA", name="Coal India", sector="metals"),
        sb._Candidate(symbol="TCS", name="TCS", sector="it"),
        sb._Candidate(symbol="SBIN", name="SBI", sector="psu_bank"),
    ]
    slots = SlotState()
    slots.asset_prefs.exclusions = ["psu"]
    out = sb._apply_exclusions(candidates, slots)
    assert {c.symbol for c in out} == {"TCS"}


def test_pinned_path_enforces_exclusions_not_just_missing_data():
    """A user's explicit "no PSU exposure" must outrank a vetted/pinned
    universe — including a name the deterministic scenario seed pinned."""
    slots = SlotState()
    slots.asset_prefs.exclusions = ["psu"]
    card = sb.build_strategy(
        "basket of these, no PSU exposure", slots, ctx=None,
        symbols=["ONGC", "TCS", "INFY"],
    )
    syms = {c.symbol for c in card.constituents}
    assert "ONGC" not in syms
    assert syms == {"TCS", "INFY"}
    assert any("exclu" in a.lower() for a in card.assumptions)


def test_pinned_path_all_excluded_returns_honest_empty_card():
    """If every pinned name is excluded, ship the honest empty-card boundary,
    never a silent no-op basket of zero (mis-)constituents."""
    slots = SlotState()
    slots.asset_prefs.exclusions = ["psu"]
    card = sb.build_strategy(
        "just ONGC please, but no PSU exposure", slots, ctx=None,
        symbols=["ONGC"],
    )
    assert card.constituents == []


# ── Theme-mapping-failure honest boundary (2026-07-14 eval finding) ──────────
#
# When a named theme can't be mapped to a recognised sector ("mid-cap
# manufacturing"), the builder must say so explicitly rather than silently
# drawing a broad cross-sector pool and letting the note read as if no theme
# was ever given.


def test_unmappable_theme_gets_honest_note_not_silent_generic_fallback():
    slots = SlotState(theme="mid-cap manufacturing")
    card = sb.build_strategy(
        "build a basket of mid-cap manufacturing companies", slots, ctx=None,
    )
    assert any("couldn't map" in a.lower() for a in card.assumptions), card.assumptions
    assert any("mid-cap manufacturing" in a for a in card.assumptions)


# ── Honest boundary: asset classes this builder can never construct ─────────
#
# Regression coverage for the bug where a chat user asked for a basket
# "covering different securities" (equities + crypto/US-stocks/other
# commodities), got an equity(+gold)-only basket back, and a follow-up turn
# had NO accurate signal to explain why — so the model improvised a wrong
# excuse. `_unsupported_asset_notes` gives the LLM (via `assumptions`, which
# flows straight into `raw_data`) an explicit, request-specific reason instead
# of a silent drop.

@pytest.mark.parametrize(
    "request_text",
    [
        "build me a basket covering equities, gold, and crypto exposure",
        "diversify across stocks, US stocks and bitcoin",
        "I want some ethereum and nasdaq exposure alongside NSE stocks",
    ],
)
def test_unsupported_asset_notes_flags_crypto_and_us_equity(request_text):
    notes = sb._unsupported_asset_notes(request_text)
    assert notes, f"expected an honest-boundary note for: {request_text!r}"
    assert any("crypto" in n or "US/international" in n for n in notes)


def test_unsupported_asset_notes_silent_for_pure_equity_gold_ask():
    notes = sb._unsupported_asset_notes(
        "build me a balanced basket of quality NSE stocks with a gold sleeve"
    )
    assert notes == []


def test_build_strategy_surfaces_unsupported_asset_note_in_assumptions(monkeypatch):
    """The note must land in the returned card's `assumptions` — the same
    field that already reaches `raw_data.build_strategy` and the FE `showWhy`
    section — not just exist as a standalone helper."""
    monkeypatch.setattr(sb, "_broad_universe", lambda: [
        {"symbol": "RELIANCE", "name": "Reliance", "sector": "energy", "mcap_cr": 1_500_000},
        {"symbol": "TCS", "name": "TCS", "sector": "it", "mcap_cr": 1_200_000},
        {"symbol": "INFY", "name": "Infosys", "sector": "it", "mcap_cr": 600_000},
        {"symbol": "ITC", "name": "ITC", "sector": "fmcg", "mcap_cr": 500_000},
        {"symbol": "HCLTECH", "name": "HCL Tech", "sector": "it", "mcap_cr": 400_000},
    ])

    card = sb.build_strategy(
        request="build me a basket with equities, gold and crypto exposure",
        slots=SlotState(),
        ctx=None,
    )
    assert any("crypto" in a for a in card.assumptions)
