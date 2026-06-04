"""F&O P1 — strategy template engine: resolution, payoff math, POP,
critique, suggest-flow ladder."""
from datetime import date

import numpy as np
import pytest

from backend.market.instrument_master import refresh_instrument_master
from backend.services.option_strategies import (
    TEMPLATES,
    VIEW_CANDIDATES,
    StrategyResolutionError,
    resolve_strategy,
    suggest_strategies,
)


@pytest.fixture()
def master(db):
    refresh_instrument_master(db)
    return db


@pytest.fixture(autouse=True)
def _fresh_chain_cache():
    # Flush optchain:* on BOTH MockRedis and real Redis — a dev server
    # on the same local Redis shares the keyspace (flake source).
    from backend.cache import redis_client

    if hasattr(redis_client, "_store"):
        redis_client._store.clear()
        redis_client._expires_at.clear()
    elif hasattr(redis_client, "scan_iter"):
        for key in list(redis_client.scan_iter("optchain:*")):
            redis_client.delete(key)
    yield


def test_every_template_resolves_on_mock_nifty(master):
    """All 15 templates must resolve to liquid strikes with a coherent
    decision quad — the catalogue is only as good as its weakest entry."""
    for name, t in TEMPLATES.items():
        payload = resolve_strategy(master, "NIFTY", name)
        legs = payload["editable"]["legs"]
        assert len(legs) == len(t.legs), name
        computed = payload["computed"]
        assert computed["payoff"] and len(computed["payoff"]) == 61, name
        assert computed["capital_required"] > 0, name
        assert payload["critique"]["verdict"] in ("ok", "caution", "risky"), name
        # Defined-risk templates must produce a finite max loss;
        # naked-short templates must produce None (unlimited).
        if name in ("short_strangle", "short_straddle", "cash_secured_put",
                    "covered_call"):
            assert computed["max_loss"] is None, name
        if name in ("bull_call_spread", "bear_put_spread", "iron_condor",
                    "iron_butterfly", "long_call", "long_put",
                    "long_straddle", "long_strangle"):
            assert computed["max_loss"] is not None, name


def test_debit_credit_sign_convention(master):
    long_call = resolve_strategy(master, "NIFTY", "long_call")
    assert long_call["computed"]["net_premium"] < 0  # debit
    credit = resolve_strategy(master, "NIFTY", "bull_put_spread")
    assert credit["computed"]["net_premium"] > 0  # credit


def test_long_call_economics(master):
    p = resolve_strategy(master, "NIFTY", "long_call")
    c = p["computed"]
    debit = -c["net_premium"]
    # Max loss of a long call IS the debit; profit uncapped.
    assert c["max_loss"] == pytest.approx(debit, rel=1e-6)
    assert c["max_profit"] is None
    assert len(c["breakevens"]) == 1
    # Breakeven = strike + premium-per-unit.
    leg = p["editable"]["legs"][0]
    expected_be = leg["strike"] + leg["mid"]
    assert c["breakevens"][0] == pytest.approx(expected_be, rel=2e-3)
    # POP of an ATM long call is < 50% (needs to cover the premium).
    assert c["pop"] is not None and 0.10 < c["pop"] < 0.50


def test_iron_condor_pop_beats_long_straddle(master):
    ic = resolve_strategy(master, "NIFTY", "iron_condor")["computed"]
    ls = resolve_strategy(master, "NIFTY", "long_straddle")["computed"]
    assert ic["pop"] > 0.5 > ls["pop"]
    # Both defined-risk; condor profit capped at its credit.
    assert ic["max_profit"] == pytest.approx(ic["net_premium"], rel=1e-6)


def test_payoff_pnl_consistency(master):
    """Payoff at deep-ITM call edge must equal slope×distance − debit."""
    p = resolve_strategy(master, "NIFTY", "bull_call_spread")
    c = p["computed"]
    legs = p["editable"]["legs"]
    lot = p["locked"]["lot_size"] * p["editable"]["qty_lots"]
    lo_k = min(l["strike"] for l in legs)
    hi_k = max(l["strike"] for l in legs)
    spread_width = (hi_k - lo_k) * lot
    # max profit = width − debit; max loss = debit. abs=0.02 — payoff
    # points round to 2dp before the max/min.
    assert c["max_profit"] == pytest.approx(
        spread_width + c["net_premium"], abs=0.02)
    assert c["max_loss"] == pytest.approx(-c["net_premium"], abs=0.02)


def test_delta_strike_rule_picks_otm(master):
    p = resolve_strategy(master, "NIFTY", "short_strangle")
    legs = {l["option_type"]: l for l in p["editable"]["legs"]}
    atm = p["locked"]["forward"]
    assert legs["CE"]["strike"] > atm          # OTM call
    assert legs["PE"]["strike"] < atm          # OTM put
    assert abs(legs["CE"]["delta"]) < 0.35     # ~20Δ target
    assert abs(legs["PE"]["delta"]) < 0.35


def test_explicit_legs_resolution_and_unknown_strike(master):
    chain_atm = resolve_strategy(master, "NIFTY", "long_call")
    atm_strike = chain_atm["editable"]["legs"][0]["strike"]
    p = resolve_strategy(
        master, "NIFTY", "custom",
        explicit_legs=[
            {"option_type": "CE", "side": "SELL", "strike": atm_strike},
        ],
    )
    assert p["editable"]["template"] == "custom"
    assert p["computed"]["max_loss"] is None  # naked short call
    with pytest.raises(StrategyResolutionError):
        resolve_strategy(
            master, "NIFTY", "custom",
            explicit_legs=[
                {"option_type": "CE", "side": "BUY", "strike": 1234.5},
            ],
        )


def test_unknown_template_and_underlying_raise(master):
    with pytest.raises(StrategyResolutionError):
        resolve_strategy(master, "NIFTY", "kangaroo_spread")
    with pytest.raises(StrategyResolutionError):
        resolve_strategy(master, "NOTREAL", "long_call")


def test_suggest_flow_returns_risk_ladder(master):
    p = suggest_strategies(master, "NIFTY", "bullish")
    # Default opens conservative; other tiers ride as candidates.
    assert p["editable"]["template"] == "bull_put_spread"
    cand_templates = {c["template"] for c in p["candidates"]}
    assert cand_templates == {"bull_call_spread", "long_call"}
    for c in p["candidates"]:
        assert c["risk_tag"] in ("conservative", "moderate", "aggressive")
        assert "one_liner" in c and c["legs"]
    # Risk override opens the requested tier.
    agg = suggest_strategies(master, "NIFTY", "bullish", risk="aggressive")
    assert agg["editable"]["template"] == "long_call"


def test_suggest_flow_view_aliases_and_unknown(master):
    assert suggest_strategies(master, "NIFTY", "sideways")["view"] == "neutral"
    assert suggest_strategies(master, "NIFTY", "big move")["view"] == "volatile"
    with pytest.raises(StrategyResolutionError):
        suggest_strategies(master, "NIFTY", "to the moon")


def test_suggest_never_assumes_holdings(master):
    """covered_call / protective_put are needs_holding — they must never
    appear in suggest-flow output (no holding check exists there)."""
    for view in VIEW_CANDIDATES:
        p = suggest_strategies(master, "NIFTY", view)
        names = {p["editable"]["template"]} | {
            c["template"] for c in p["candidates"]
        }
        assert "covered_call" not in names
        assert "protective_put" not in names


def test_critique_flags_naked_short_as_risky(master):
    p = resolve_strategy(master, "NIFTY", "short_strangle")
    assert p["critique"]["verdict"] == "risky"
    texts = " ".join(f["text"] for f in p["critique"]["flags"])
    assert "Unlimited loss" in texts


def test_critique_mcx_research_only_flag(master):
    p = resolve_strategy(master, "CRUDEOIL", "long_straddle")
    assert p["validation"]["mcx_execution_blocked"] is True
    texts = " ".join(f["text"] for f in p["critique"]["flags"])
    assert "research-only" in texts


def test_sebi_disclosure_always_present(master):
    p = resolve_strategy(master, "NIFTY", "long_call")
    assert "9 out of 10" in p["locked"]["disclosure"]
    assert p["validation"]["requires_disclosure"] is True
