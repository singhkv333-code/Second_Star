"""Phase 0.6 — the no-look-ahead data boundary, standardized across engines.

Both backtest engines read market data through accessors that conform to the ONE
shared ``DataAccessor`` protocol (``backend.workflows.dsl.data_accessor``) and
honour the same invariant: *no method may read a bar after the as-of bar*.

  * Engine 2b's ``BacktestDataAccessor`` is proven by
    ``tests/workflows/dsl/backtest/test_data_accessor.py::test_no_lookahead_adversarial``.
  * Engine 2's ``_BarStrictAccessor`` had no equivalent adversarial test — this
    file adds it, plus a cross-engine conformance assertion. So the no-look-ahead
    boundary is now standardized AND proven in both engines (full code-unification
    into one literal object is unnecessary — both satisfy the same protocol and
    the same adversarial test, and Engine 2's trigger-expansion architecture
    differs enough that forcing one object would be a high-risk refactor for no
    correctness gain).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backend.services.workflow_backtester import _BarStrictAccessor
from backend.workflows.dsl.backtest.data_accessor import BacktestDataAccessor
from backend.workflows.dsl.data_accessor import DataAccessor

AS_OF = 60
_PROBES = [
    {"indicator": "rsi", "period": 14},
    {"indicator": "sma", "period": 20},
    {"indicator": "ema", "period": 10},
    {"indicator": "atr", "period": 14},
]


def _series(n: int = 100, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2022-01-03", periods=n)
    close = 100.0 + np.cumsum(rng.normal(0, 1, n))
    return pd.DataFrame(
        {
            "Open": close + rng.normal(0, 0.5, n),
            "High": close + rng.uniform(0.2, 1.5, n),
            "Low": close - rng.uniform(0.2, 1.5, n),
            "Close": close,
            "Volume": rng.integers(1_000_000, 5_000_000, n).astype(float),
        },
        index=idx,
    )


def _tamper_future(df: pd.DataFrame, as_of_idx: int) -> pd.DataFrame:
    """Replace every bar AFTER as_of_idx with absurd garbage. If any accessor
    value at the as-of bar changes, that accessor read the future."""
    t = df.copy()
    fut = t.index[as_of_idx + 1:]
    t.loc[fut, ["Open", "High", "Low", "Close"]] = 1e6
    t.loc[fut, "Volume"] = 9e9
    return t


def test_engine2_baraccessor_is_no_lookahead():
    df = _series()
    ts = df.index[AS_OF]
    clean = _BarStrictAccessor({"TEST": df}, ts)
    tampered = _BarStrictAccessor({"TEST": _tamper_future(df, AS_OF)}, ts)

    # Price + volume must ignore the future garbage.
    assert clean.get_price(symbol="TEST", basis="close") == \
        tampered.get_price(symbol="TEST", basis="close")
    assert clean.get_price(symbol="TEST", basis="high") == \
        tampered.get_price(symbol="TEST", basis="high")
    assert clean.get_volume(symbol="TEST", bars=20) == \
        tampered.get_volume(symbol="TEST", bars=20)

    # Every indicator computed at the as-of bar must be identical despite the
    # 1e6 spike planted in every future bar.
    for p in _PROBES:
        a = clean.get_indicator(symbol="TEST", **p)
        b = tampered.get_indicator(symbol="TEST", **p)
        assert a is not None, f"{p} returned None on clean data (warmup?)"
        assert a == pytest.approx(b, rel=1e-12, abs=1e-9), \
            f"{p} LEAKED the future: clean={a} != tampered={b}"


def test_offset_reads_stay_causal():
    """offset=N (bar N back) must also be unaffected by future tampering."""
    df = _series()
    ts = df.index[AS_OF]
    clean = _BarStrictAccessor({"TEST": df}, ts)
    tampered = _BarStrictAccessor({"TEST": _tamper_future(df, AS_OF)}, ts)
    for off in (0, 1, 3):
        assert clean.get_indicator(symbol="TEST", indicator="rsi", period=14, offset=off) == \
            pytest.approx(
                tampered.get_indicator(symbol="TEST", indicator="rsi", period=14, offset=off),
                rel=1e-12,
            )


def test_both_backtest_accessors_conform_to_one_protocol():
    """Standardized contract: both engines' accessors satisfy the SAME
    runtime-checkable DataAccessor protocol (the single no-look-ahead boundary)."""
    assert issubclass(_BarStrictAccessor, DataAccessor)
    assert issubclass(BacktestDataAccessor, DataAccessor)
    df = _series()
    acc = _BarStrictAccessor({"TEST": df}, df.index[AS_OF])
    assert isinstance(acc, DataAccessor)
