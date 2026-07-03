"""Phase 2.2 — position-sizing layer for the DSL backtest engine.

Tests _size_position directly (fast, deterministic): each mode's math, the
no-leverage cap, and that the vol/ATR estimates are CAUSAL (future bars can't
change the size).
"""
from __future__ import annotations

from types import SimpleNamespace

import pandas as pd

from backend.workflows.dsl.backtest.engine import _atr_value, _size_position
from backend.workflows.dsl.backtest.schema import Sizing

ENTRY = 60


def _bars(closes, highs=None, lows=None) -> pd.DataFrame:
    closes = [float(c) for c in closes]
    n = len(closes)
    idx = pd.bdate_range("2022-01-03", periods=n)
    highs = highs or [c * 1.01 for c in closes]
    lows = lows or [c * 0.99 for c in closes]
    return pd.DataFrame(
        {"open": closes, "high": highs, "low": lows, "close": closes,
         "volume": [1e6] * n},
        index=idx,
    )


def _alt_series(d: float, n: int = 100, start: float = 100.0):
    """Closes whose pct-changes alternate ±d → recent realised daily vol ≈ d."""
    out = [start]
    for i in range(1, n):
        out.append(out[-1] * (1 + d if i % 2 else 1 - d))
    return out


def _st(bars, cash, sizing, quantity=1):
    return SimpleNamespace(
        cash=cash, primary_bars=bars,
        request=SimpleNamespace(sizing=sizing, quantity=quantity),
    )


def test_fixed_uses_request_quantity():
    st = _st(_bars(_alt_series(0.01)), 100_000.0, Sizing(mode="fixed"), quantity=7)
    assert _size_position(st, ENTRY, 100.0) == 7


def test_pct_equity_scales_with_equity_and_price():
    sizing = Sizing(mode="pct_equity", pct=0.20)
    st = _st(_bars(_alt_series(0.005)), 100_000.0, sizing)
    # 20% of 100k at ₹100 = 200 shares (below the 980 no-leverage cap).
    assert _size_position(st, ENTRY, 100.0) == 200
    # Double the equity → double the size.
    st2 = _st(_bars(_alt_series(0.005)), 200_000.0, sizing)
    assert _size_position(st2, ENTRY, 100.0) == 400


def test_vol_target_sizes_inversely_to_volatility():
    sizing = Sizing(mode="vol_target", target_vol=0.15, vol_lookback=20)
    low_vol = _st(_bars(_alt_series(0.013)), 100_000.0, sizing)   # ann ≈ 20%
    high_vol = _st(_bars(_alt_series(0.026)), 100_000.0, sizing)  # ann ≈ 41%
    q_low = _size_position(low_vol, ENTRY, 100.0)
    q_high = _size_position(high_vol, ENTRY, 100.0)
    assert q_low > q_high > 0  # lower vol ⇒ bigger position for the same target


def test_atr_risk_sizes_inversely_to_atr():
    sizing = Sizing(mode="atr_risk", risk_pct=0.01, atr_period=14, atr_mult=2.0)
    closes = _alt_series(0.0)  # flat closes so ATR is driven by the H/L band
    tight = _st(
        _bars(closes, highs=[c * 1.005 for c in closes], lows=[c * 0.995 for c in closes]),
        100_000.0, sizing,
    )
    wide = _st(
        _bars(closes, highs=[c * 1.05 for c in closes], lows=[c * 0.95 for c in closes]),
        100_000.0, sizing,
    )
    assert _size_position(tight, ENTRY, 100.0) > _size_position(wide, ENTRY, 100.0) > 0


def test_no_leverage_cap():
    # pct=1.0 would want the whole equity; capped at ~98% (headroom for costs).
    st = _st(_bars(_alt_series(0.005)), 100_000.0, Sizing(mode="pct_equity", pct=1.0))
    qty = _size_position(st, ENTRY, 100.0)
    assert qty * 100.0 <= 100_000.0
    assert qty == int(100_000.0 * 0.98 / 100.0)


def test_sizing_is_causal_future_bars_ignored():
    """Tampering bars AFTER the entry must not change the size — vol/ATR are
    computed over bars strictly before the entry."""
    base = _alt_series(0.02)
    clean = _bars(base)
    tampered_closes = list(base)
    for i in range(ENTRY + 1, len(tampered_closes)):
        tampered_closes[i] = 1e6  # absurd future spike
    tampered = _bars(tampered_closes)
    for sizing in (
        Sizing(mode="vol_target", target_vol=0.15, vol_lookback=20),
        Sizing(mode="atr_risk", risk_pct=0.01, atr_period=14, atr_mult=2.0),
    ):
        q_clean = _size_position(_st(clean, 100_000.0, sizing), ENTRY, 100.0)
        q_tampered = _size_position(_st(tampered, 100_000.0, sizing), ENTRY, 100.0)
        assert q_clean == q_tampered, f"{sizing.mode} leaked the future"


def test_atr_value_helper_none_when_too_short():
    assert _atr_value(_bars(_alt_series(0.01, n=5)), period=14) is None
