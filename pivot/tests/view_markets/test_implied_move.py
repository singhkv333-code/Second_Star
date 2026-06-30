"""Focused unit tests for ``backend.view_markets.implied_move``.

Self-contained: the option chain is a hand-built dict (the shape
``option_chain.get_chain`` returns), so no Kite/Redis/DB access is needed.
``implied_move``/``implied_probability`` are exercised by monkeypatching the
``get_chain`` symbol they import lazily.
"""
from __future__ import annotations

import math

import pytest

from backend.view_markets import implied_move as im


def _chain(
    *,
    forward: float = 23_000.0,
    atm_strike: float = 23_000.0,
    t_years: float = 30.0 / 365.0,
    ce_iv: float | None = 0.15,
    pe_iv: float | None = 0.15,
    ce_ltp: float = 250.0,
    pe_ltp: float = 250.0,
    with_em_block: bool = True,
    underlying: str = "NIFTY",
) -> dict:
    """Build a minimal ATM-centred chain dict in the get_chain shape."""
    ce = {"ltp": ce_ltp, "bid": 0.0, "ask": 0.0, "iv": ce_iv}
    pe = {"ltp": pe_ltp, "bid": 0.0, "ask": 0.0, "iv": pe_iv}
    chain: dict = {
        "underlying": underlying,
        "forward": forward,
        "atm_strike": atm_strike,
        "t_years": t_years,
        "expiry": "2026-07-30",
        "segment": "NFO-OPT",
        "asof": "2026-06-29T15:30:00+05:30",
        "rows": [
            {"strike": atm_strike - 100, "ce": dict(ce), "pe": dict(pe)},
            {"strike": atm_strike, "ce": ce, "pe": pe},
            {"strike": atm_strike + 100, "ce": dict(ce), "pe": dict(pe)},
        ],
    }
    if with_em_block:
        ivs = [v for v in (ce_iv, pe_iv) if v]
        if ivs and t_years > 0:
            em = forward * (sum(ivs) / len(ivs)) * math.sqrt(t_years)
        else:
            em = 0.8 * (ce_ltp + pe_ltp)
        chain["expected_move"] = {
            "low": round(forward - em, 2),
            "high": round(forward + em, 2),
            "abs": round(em, 2),
            "pct": round(em / forward * 100.0, 2),
        }
    else:
        chain["expected_move"] = None
    return chain


def test_from_chain_uses_iv_block():
    chain = _chain()
    move = im.implied_move_from_chain(chain)
    assert move is not None
    assert move.source == "iv"
    assert move.atm_iv == pytest.approx(0.15)
    # EM = F × IV × √T
    expected = 23_000.0 * 0.15 * math.sqrt(30.0 / 365.0)
    assert move.expected_move_abs == pytest.approx(expected, rel=1e-3)
    assert move.low == pytest.approx(move.forward - move.expected_move_abs)
    assert move.high == pytest.approx(move.forward + move.expected_move_abs)
    assert move.expected_move_pct == pytest.approx(
        move.expected_move_abs / move.forward * 100.0, abs=1e-3
    )


def test_from_chain_straddle_fallback_when_no_iv():
    chain = _chain(ce_iv=None, pe_iv=None, with_em_block=False)
    move = im.implied_move_from_chain(chain)
    assert move is not None
    assert move.source == "straddle"
    assert move.atm_iv is None
    # 0.85 × (250 + 250)
    assert move.expected_move_abs == pytest.approx(0.85 * 500.0)
    assert move.straddle_price == pytest.approx(500.0)


def test_horizon_rescale_sqrt_t():
    chain = _chain()  # DTE = 30d
    base = im.implied_move_from_chain(chain)
    half = im.implied_move_from_chain(chain, horizon_days=15)
    assert base is not None and half is not None
    # √(15/30) = 1/√2 scaling
    assert half.expected_move_abs == pytest.approx(
        base.expected_move_abs / math.sqrt(2.0), rel=1e-4
    )
    assert half.t_years == pytest.approx(15.0 / 365.0)


def test_none_when_no_em_and_no_quotes():
    chain = _chain(ce_iv=None, pe_iv=None, ce_ltp=0.0, pe_ltp=0.0,
                   with_em_block=False)
    assert im.implied_move_from_chain(chain) is None


def test_none_when_forward_missing():
    chain = _chain()
    chain["forward"] = 0.0
    assert im.implied_move_from_chain(chain) is None


def test_implied_move_fetches_chain(monkeypatch):
    chain = _chain()
    monkeypatch.setattr(
        "backend.market.option_chain.get_chain",
        lambda db, underlying, expiry=None, *, width=10, now=None: chain,
    )
    move = im.implied_move(object(), "NIFTY")
    assert move is not None and move.source == "iv"


def test_implied_move_none_when_chain_unavailable(monkeypatch):
    monkeypatch.setattr(
        "backend.market.option_chain.get_chain",
        lambda *a, **k: None,
    )
    assert im.implied_move(object(), "UNKNOWN") is None


def test_implied_probability_above_and_below(monkeypatch):
    chain = _chain()
    monkeypatch.setattr(
        "backend.market.option_chain.get_chain",
        lambda db, underlying, expiry=None, *, width=10, now=None: chain,
    )
    p_above = im.implied_probability(
        object(), "NIFTY", target_level=23_000.0, direction="above"
    )
    p_below = im.implied_probability(
        object(), "NIFTY", target_level=23_000.0, direction="below"
    )
    assert p_above is not None and p_below is not None
    assert 0.0 <= p_above <= 1.0
    assert p_above + p_below == pytest.approx(1.0, abs=1e-9)
    # At the forward, P(above) is slightly below 0.5 (lognormal drift term).
    assert p_above < 0.5

    # Higher target ⇒ lower P(above).
    p_high = im.implied_probability(
        object(), "NIFTY", target_level=24_000.0, direction="above"
    )
    assert p_high < p_above


def test_implied_probability_none_without_iv(monkeypatch):
    chain = _chain(ce_iv=None, pe_iv=None, with_em_block=False)
    monkeypatch.setattr(
        "backend.market.option_chain.get_chain",
        lambda db, underlying, expiry=None, *, width=10, now=None: chain,
    )
    # Straddle-sourced move has no IV ⇒ cannot price a tail ⇒ None.
    assert im.implied_probability(
        object(), "NIFTY", target_level=23_000.0
    ) is None
