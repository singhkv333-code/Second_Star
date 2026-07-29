"""Unit tests for the Phase-3 INDEX-level hedge builder.

Self-contained: the real option engine (``resolve_strategy``), the option chain
(``get_chain``), and the implied-move primitive are MOCKED so the test asserts
the builder's contract (delegation, India gate, defined-risk guard, envelope
disclosures) without a live Kite session.

Note on honest_short: the hedge builder's short leg is a SHORT INDEX OPTION
(NFO-OPT), which is perfectly legal in India — it is NOT a stock/ETF delivery
short. So ``honest_short`` is intentionally NOT invoked here (that rule governs
single-stock/ETF shorts in the option/pair/basket builders). We instead assert
the short leg is a tradeable index option and the expression is not degraded.
"""
from __future__ import annotations

from typing import Any
from unittest import mock

import pytest

from backend.services.option_strategies import StrategyResolutionError
from backend.view_markets.expressions import config_schema
from backend.view_markets.expressions.builders import hedge_builder
from backend.view_markets.expressions.catalog import get_archetype
from backend.view_markets.implied_move import ImpliedMove

_HEDGE = "backend.view_markets.expressions.builders.hedge_builder"

_T3 = get_archetype("T3_optionized_hedged")


def _fake_chain(underlying: str = "NIFTY") -> dict[str, Any]:
    rows = [{"strike": float(s)} for s in range(23000, 27001, 100)]
    return {
        "underlying": underlying,
        "rows": rows,
        "forward": 25000.0,
        "atm_strike": 25000.0,
        "t_years": 0.08,
        "expiry": "2026-07-30",
        "lot_size": 50,
        "validation": {},
    }


def _fake_im(forward: float = 25000.0, em: float = 600.0, source: str = "iv") -> ImpliedMove:
    return ImpliedMove(
        underlying="NIFTY",
        expiry="2026-07-30",
        forward=forward,
        atm_strike=forward,
        atm_iv=0.13,
        t_years=0.08,
        expected_move_abs=em,
        expected_move_pct=round(em / forward * 100, 2),
        low=forward - em,
        high=forward + em,
        straddle_price=700.0,
        source=source,
        asof="2026-06-29T10:00:00Z",
    )


def _fake_resolved(
    legs: list[dict[str, Any]],
    *,
    max_loss: float | None = 18000.0,
    net_premium: float = -250.0,
) -> dict[str, Any]:
    return {
        "locked": {"underlying": "NIFTY", "segment": "NFO-OPT"},
        "editable": {"template": "custom", "qty_lots": 1, "legs": legs},
        "computed": {
            "net_premium": net_premium,
            "max_loss": max_loss,
            "max_profit": 30000.0,
            "pop": 0.55,
            "breakevens": [24800.0],
            "net_greeks": {"delta": -10.0, "gamma": 0.0, "theta": 5.0, "vega": -2.0},
            "capital_required": 90000.0,
        },
        "validation": {"liquidity_flags": []},
        "critique": {},
    }


def _collar_legs() -> list[dict[str, Any]]:
    return [
        {"option_type": "PE", "side": "BUY", "strike": 24400.0, "mid": 180.0,
         "tradingsymbol": "NIFTY24400PE"},
        {"option_type": "CE", "side": "SELL", "strike": 25600.0, "mid": 150.0,
         "tradingsymbol": "NIFTY25600CE"},
    ]


def _patch(resolved: dict[str, Any], *, im: ImpliedMove | None = None, chain=None):
    """Patch the lazily-imported engines at their SOURCE modules."""
    chain = chain if chain is not None else _fake_chain()
    return (
        mock.patch("backend.market.option_chain.get_chain", return_value=chain),
        mock.patch(
            "backend.services.option_strategies.resolve_strategy",
            return_value=resolved,
        ),
        mock.patch(
            "backend.view_markets.implied_move.implied_move_from_chain",
            return_value=im if im is not None else _fake_im(),
        ),
    )


# ── Tests ────────────────────────────────────────────────────────────────────


def test_conservative_collar_carries_required_disclosures(view_db, theme_view):
    resolved = _fake_resolved(_collar_legs())
    p_chain, p_resolve, p_im = _patch(resolved)
    with p_chain, p_resolve as m_resolve, p_im:
        cfg = hedge_builder.build_hedge_expression(
            view_db, theme_view, _T3, "conservative",
            underlying="NIFTY", qty_lots=1, horizon_days=30,
        )

    # Delegated to the real option engine with explicit collar legs.
    assert m_resolve.called
    _, kwargs = m_resolve.call_args
    assert kwargs["explicit_legs"][0]["option_type"] == "PE"
    assert kwargs["explicit_legs"][1]["option_type"] == "CE"

    # Envelope identity + STRUCTURE_KEYS["hedge"] all present.
    assert cfg["expression_kind"] == "hedge"
    assert cfg["tier"] == "conservative"
    structure = cfg["structure"]
    for key in config_schema.STRUCTURE_KEYS["hedge"]:
        assert key in structure
    assert structure["underlying_index"] == "NIFTY"
    assert structure["floor_level"] == 24400.0  # the long-put strike = the floor

    # Defined-risk first: a real, finite floor.
    assert structure["max_loss"] is not None
    assert structure["max_loss"] == 18000.0

    # Standard disclosure surface.
    assert cfg["disclaimer"] == config_schema.DISCLAIMER
    assert cfg["costs"]["round_trip_bps"] > 0
    assert cfg["scores"]["alignment_kind"] == "event_study"


def test_short_call_leg_is_a_legal_index_option_not_degraded(view_db, theme_view):
    resolved = _fake_resolved(_collar_legs())
    p_chain, p_resolve, p_im = _patch(resolved)
    with p_chain, p_resolve, p_im:
        cfg = hedge_builder.build_hedge_expression(
            view_db, theme_view, _T3, "conservative",
        )
    shorts = [i for i in cfg["instruments"] if i["role"] == "short"]
    assert len(shorts) == 1
    short = shorts[0]
    assert short["instrument_type"] == "index_option"
    assert short["segment"] == "NFO-OPT"
    assert short["tradeable"] is True  # short INDEX option is legal — no honest_short
    assert cfg["expressability"]["degraded"] is False


def test_balanced_puts_strike_nearer_the_money(view_db, theme_view):
    """Balanced finances the put with a further OTM call → put nearer the money
    than the conservative symmetric collar."""
    captured: dict[str, Any] = {}

    def _capture(db, underlying, template, **kw):
        captured.update(kw)
        return _fake_resolved(_collar_legs())

    p_chain, _, p_im = _patch(_fake_resolved(_collar_legs()))
    with p_chain, mock.patch(
        "backend.services.option_strategies.resolve_strategy", side_effect=_capture
    ), p_im:
        hedge_builder.build_hedge_expression(view_db, theme_view, _T3, "balanced")

    put_strike = captured["explicit_legs"][0]["strike"]
    call_strike = captured["explicit_legs"][1]["strike"]
    # put_k=0.5 → 25000-300=24700 → snaps to 24700; call_k=1.0 → 25600.
    assert put_strike == 24700.0
    assert call_strike == 25600.0


def test_aggressive_uses_long_call_spread_template(view_db, theme_view):
    legs = [
        {"option_type": "CE", "side": "BUY", "strike": 25000.0},
        {"option_type": "CE", "side": "SELL", "strike": 25600.0},
    ]
    resolved = _fake_resolved(legs, net_premium=-200.0)
    captured: dict[str, Any] = {}

    def _capture(db, underlying, template, **kw):
        captured["template"] = template
        return resolved

    p_chain, _, p_im = _patch(resolved)
    with p_chain, mock.patch(
        "backend.services.option_strategies.resolve_strategy", side_effect=_capture
    ), p_im:
        cfg = hedge_builder.build_hedge_expression(
            view_db, theme_view, _T3, "aggressive",
        )
    assert captured["template"] == "bull_call_spread"
    assert "explicit_legs" not in captured  # template picks its own strikes
    assert cfg["structure"]["hedge_template"] == "bull_call_spread"
    assert cfg["structure"]["floor_level"] is None


def test_unbounded_max_loss_is_rejected(view_db, theme_view):
    resolved = _fake_resolved(_collar_legs(), max_loss=None)
    p_chain, p_resolve, p_im = _patch(resolved)
    with p_chain, p_resolve, p_im:
        with pytest.raises(StrategyResolutionError):
            hedge_builder.build_hedge_expression(view_db, theme_view, _T3, "conservative")


def test_non_index_underlying_coerced_to_nifty_with_warning(view_db, theme_view):
    resolved = _fake_resolved(_collar_legs())
    p_chain, p_resolve, p_im = _patch(resolved)
    with p_chain, p_resolve as m_resolve, p_im:
        cfg = hedge_builder.build_hedge_expression(
            view_db, theme_view, _T3, "conservative", underlying="RELIANCE",
        )
    # Gate: hedge at index level via NIFTY, never name-by-name.
    args, _ = m_resolve.call_args
    assert args[1] == "NIFTY"
    assert cfg["structure"]["underlying_index"] == "NIFTY"
    assert any("name-by-name" in w for w in cfg["warnings"])


def test_banknifty_monthly_only_warning(view_db, theme_view):
    resolved = _fake_resolved(_collar_legs())
    p_chain, p_resolve, p_im = _patch(resolved, chain=_fake_chain("BANKNIFTY"))
    with p_chain, p_resolve, p_im:
        cfg = hedge_builder.build_hedge_expression(
            view_db, theme_view, _T3, "conservative", underlying="BANKNIFTY",
        )
    assert cfg["structure"]["underlying_index"] == "BANKNIFTY"
    assert any("monthly-only" in w for w in cfg["warnings"])


def test_missing_implied_move_for_collar_degrades_not_fabricates(view_db, theme_view):
    resolved = _fake_resolved(_collar_legs())
    p_chain, p_resolve, _ = _patch(resolved)
    with p_chain, p_resolve, mock.patch(
        "backend.view_markets.implied_move.implied_move_from_chain", return_value=None
    ):
        with pytest.raises(StrategyResolutionError):
            hedge_builder.build_hedge_expression(view_db, theme_view, _T3, "conservative")
