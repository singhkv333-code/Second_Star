"""BacktestDataAccessor — correctness + the as-of-bar guarantee.

The critical test in this file is ``test_no_lookahead_adversarial``:
it fills bars past as_of_idx with NaN and asserts every accessor
method returns the same value it would from a TRUNCATED series.
If any computation accidentally reads the future, the NaN-filled
version differs and the test fails loudly.
"""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from backend.workflows.dsl.backtest.bar_loader import LoadedBars
from backend.workflows.dsl.backtest.data_accessor import BacktestDataAccessor


# ── Fixtures ────────────────────────────────────────────────────────


def _make_bars(n_days: int, *, seed: int = 7) -> pd.DataFrame:
    """Synthetic but realistic OHLCV — random walk on close, OHLC
    consistent with the close so indicators don't blow up."""
    rng = np.random.default_rng(seed)
    closes = 100.0 + np.cumsum(rng.normal(0.0, 1.5, n_days))
    closes = np.maximum(closes, 5.0)  # never go negative
    dates = pd.date_range("2024-01-01", periods=n_days, freq="B").normalize()
    df = pd.DataFrame({
        "open":   closes - rng.uniform(0.0, 0.5, n_days),
        "high":   closes + rng.uniform(0.0, 1.0, n_days),
        "low":    closes - rng.uniform(0.0, 1.0, n_days),
        "close":  closes,
        "volume": rng.integers(100_000, 1_500_000, n_days).astype(float),
    }, index=dates)
    # Enforce high >= max(open, close), low <= min(open, close).
    df["high"] = df[["high", "open", "close"]].max(axis=1)
    df["low"] = df[["low", "open", "close"]].min(axis=1)
    return df


def _loaded(df: pd.DataFrame, *, symbol: str = "TCS") -> LoadedBars:
    return LoadedBars(
        by_symbol={(symbol, "NSE"): df},
        master_dates=pd.DatetimeIndex(df.index),
    )


# ── Basic correctness ───────────────────────────────────────────────


def test_get_price_returns_close_of_as_of_bar():
    df = _make_bars(50)
    acc = BacktestDataAccessor(_loaded(df))
    acc.advance_to(10)
    assert acc.get_price(symbol="TCS") == pytest.approx(float(df["close"].iloc[10]))


def test_get_indicator_returns_latest_value():
    df = _make_bars(60)
    acc = BacktestDataAccessor(_loaded(df))
    acc.advance_to(40)
    val = acc.get_indicator(symbol="TCS", indicator="rsi", period=14)
    assert val is not None
    assert 0.0 <= val <= 100.0


def test_get_indicator_caches_full_series():
    """First call computes; subsequent calls just slice."""
    df = _make_bars(60)
    acc = BacktestDataAccessor(_loaded(df))
    acc.advance_to(40)
    v1 = acc.get_indicator(symbol="TCS", indicator="rsi", period=14)
    # Move forward; same indicator should still hit the cache.
    acc.advance_to(45)
    v2 = acc.get_indicator(symbol="TCS", indicator="rsi", period=14)
    # Different bar → different value (random walk).
    assert v1 != v2
    # Cache populated.
    assert any(k[1] == "rsi" for k in acc._indicator_cache)


def test_get_volume_sums_window():
    df = _make_bars(30)
    acc = BacktestDataAccessor(_loaded(df))
    acc.advance_to(10)
    one = acc.get_volume(symbol="TCS", bars=1)
    five = acc.get_volume(symbol="TCS", bars=5)
    assert one == pytest.approx(float(df["volume"].iloc[10]))
    assert five == pytest.approx(float(df["volume"].iloc[6:11].sum()))


def test_advance_to_validates_bounds():
    df = _make_bars(10)
    acc = BacktestDataAccessor(_loaded(df))
    with pytest.raises(IndexError):
        acc.advance_to(20)
    with pytest.raises(IndexError):
        acc.advance_to(-1)


def test_unknown_symbol_returns_none():
    df = _make_bars(20)
    acc = BacktestDataAccessor(_loaded(df))
    acc.advance_to(5)
    assert acc.get_price(symbol="NOPE") is None
    assert acc.get_indicator(
        symbol="NOPE", indicator="rsi", period=14
    ) is None


# ── The adversarial no-lookahead test ───────────────────────────────


def test_no_lookahead_adversarial():
    """THE critical test.

    Build a 100-bar series. Pick as_of_idx = 50. Compute every accessor
    output. Now build a SECOND series identical for bars 0..50 but with
    bars 51..99 wiped to NaN. Repeat all accessor calls on the second
    series — every result must be identical.

    If any accessor function accidentally reads the future, the
    NaN-filled version's indicator computation collapses to NaN
    (pandas-ta won't compute through NaN gaps) and the assertion
    fires."""
    base = _make_bars(100)
    poisoned = base.copy()
    for col in ("open", "high", "low", "close", "volume"):
        poisoned.loc[poisoned.index[51:], col] = np.nan

    AS_OF = 50

    acc_base = BacktestDataAccessor(_loaded(base))
    acc_pois = BacktestDataAccessor(_loaded(poisoned))
    acc_base.advance_to(AS_OF)
    acc_pois.advance_to(AS_OF)

    # 1. Price
    assert acc_base.get_price(symbol="TCS") == acc_pois.get_price(symbol="TCS")

    # 2. Volume (window doesn't reach future bars)
    for bars in (1, 5, 20):
        assert (
            acc_base.get_volume(symbol="TCS", bars=bars)
            == acc_pois.get_volume(symbol="TCS", bars=bars)
        ), f"volume mismatch at bars={bars} — look-ahead leak"

    # 3. Indicators — exhaustively across the registry's core members.
    # If ANY of these read the future, the poisoned compute will
    # produce a different value (or NaN, which the accessor returns
    # as None).
    for ind, period in [
        ("rsi", 14), ("sma", 20), ("ema", 20), ("macd", 12),
        ("atr", 14), ("bb", 20),
    ]:
        v_base = acc_base.get_indicator(
            symbol="TCS", indicator=ind, period=period,
        )
        v_pois = acc_pois.get_indicator(
            symbol="TCS", indicator=ind, period=period,
        )
        assert v_base == v_pois, (
            f"LOOK-AHEAD LEAK detected for {ind}({period}) at "
            f"idx={AS_OF}: full-series={v_base}, "
            f"future-masked={v_pois}"
        )


def test_component_selects_distinct_bollinger_outputs():
    """Bollinger has five named outputs. With ``component`` unset the
    accessor returns Percent-B (a 0..1 oscillator); with
    ``component=upper`` / ``middle`` / ``lower`` it returns the actual
    band levels — comparable to price. We assert the three band
    components produce distinct values, the upper band > middle band >
    lower band, and the default (%B) is bounded near [0, 1]."""
    df = _make_bars(120)
    acc = BacktestDataAccessor(_loaded(df))
    acc.advance_to(60)

    default = acc.get_indicator(symbol="TCS", indicator="bb", period=20)
    upper = acc.get_indicator(symbol="TCS", indicator="bb", period=20, component="upper")
    middle = acc.get_indicator(symbol="TCS", indicator="bb", period=20, component="middle")
    lower = acc.get_indicator(symbol="TCS", indicator="bb", period=20, component="lower")
    pctb = acc.get_indicator(symbol="TCS", indicator="bb", period=20, component="pctb")

    assert all(v is not None for v in (default, upper, middle, lower, pctb))
    # %B is the canonical default — confirm default == pctb explicit.
    assert default == pytest.approx(pctb)
    # Bands ordered by construction.
    assert upper > middle > lower
    # The default %B is bounded around [-0.5, 1.5] in practice (>1
    # = breakout above upper, <0 = breakout below lower).
    assert -1.0 <= pctb <= 2.0


def test_component_macd_components_distinct():
    """MACD emits three series: macd, signal, hist. Each should be
    addressable independently and they should not collapse to the
    same number for a non-trivial price series."""
    df = _make_bars(150, seed=11)
    acc = BacktestDataAccessor(_loaded(df))
    acc.advance_to(100)

    line = acc.get_indicator(symbol="TCS", indicator="macd", period=26, component="macd")
    signal = acc.get_indicator(symbol="TCS", indicator="macd", period=26, component="signal")
    hist = acc.get_indicator(symbol="TCS", indicator="macd", period=26, component="hist")
    default = acc.get_indicator(symbol="TCS", indicator="macd", period=26)

    assert all(v is not None for v in (line, signal, hist, default))
    # Histogram is the default (kept for backwards-compat).
    assert default == pytest.approx(hist)
    # Identity macd ≈ signal + hist (within FP tolerance).
    assert (line - signal) == pytest.approx(hist, abs=1e-6)


def test_component_cache_key_distinguishes_outputs():
    """Two get_indicator calls on the same indicator with different
    components must NOT share a cache slot — otherwise the second call
    would return the first's series."""
    df = _make_bars(120)
    acc = BacktestDataAccessor(_loaded(df))
    acc.advance_to(60)

    upper = acc.get_indicator(symbol="TCS", indicator="bb", period=20, component="upper")
    lower = acc.get_indicator(symbol="TCS", indicator="bb", period=20, component="lower")
    # If the cache key didn't include component, the second call would
    # have returned ``upper``. Distinct results prove the key works.
    assert upper != lower


# ── Time-shifted reads (Phase C.0.1) ────────────────────────────────


def test_get_price_offset_reads_previous_bar():
    df = _make_bars(50)
    acc = BacktestDataAccessor(_loaded(df))
    acc.advance_to(20)
    cur = acc.get_price(symbol="TCS")
    prev = acc.get_price(symbol="TCS", offset=1)
    assert cur == pytest.approx(float(df["close"].iloc[20]))
    assert prev == pytest.approx(float(df["close"].iloc[19]))


def test_get_price_basis_open_high_low():
    df = _make_bars(50)
    acc = BacktestDataAccessor(_loaded(df))
    acc.advance_to(20)
    assert acc.get_price(symbol="TCS", basis="open") == pytest.approx(
        float(df["open"].iloc[20])
    )
    assert acc.get_price(symbol="TCS", basis="high") == pytest.approx(
        float(df["high"].iloc[20])
    )
    assert acc.get_price(symbol="TCS", basis="low") == pytest.approx(
        float(df["low"].iloc[20])
    )


def test_get_price_offset_past_zero_returns_none():
    df = _make_bars(50)
    acc = BacktestDataAccessor(_loaded(df))
    acc.advance_to(3)
    assert acc.get_price(symbol="TCS", offset=10) is None


def test_get_indicator_offset_reads_past_value():
    df = _make_bars(80)
    acc = BacktestDataAccessor(_loaded(df))
    acc.advance_to(60)
    cur = acc.get_indicator(symbol="TCS", indicator="rsi", period=14)
    five_ago = acc.get_indicator(symbol="TCS", indicator="rsi", period=14, offset=5)
    assert cur is not None and five_ago is not None
    # Different bars almost certainly have different RSI values on a
    # random-walk series.
    assert cur != five_ago


def test_get_volume_offset_shifts_window():
    df = _make_bars(50)
    acc = BacktestDataAccessor(_loaded(df))
    acc.advance_to(30)
    current_5 = acc.get_volume(symbol="TCS", bars=5)
    shifted_5 = acc.get_volume(symbol="TCS", bars=5, offset=5)
    # Sums of two different 5-bar windows should not match on a random
    # series.
    assert current_5 is not None and shifted_5 is not None
    assert current_5 != shifted_5


# ── Aggregate fast path (Phase C.0.3) ───────────────────────────────


def test_aggregate_highest_over_price_matches_pandas():
    """The fast-path aggregator must return the same answer as
    pd.Series.max over the equivalent window. Calls _walk directly
    because evaluate() wraps numeric results as UNKNOWN at the root
    (only comparison/logic are valid root types)."""
    from backend.workflows.dsl.evaluator import _walk
    from backend.workflows.dsl.schema import Tree
    from pydantic import TypeAdapter
    df = _make_bars(80, seed=3)
    acc = BacktestDataAccessor(_loaded(df))
    acc.advance_to(60)
    tree = TypeAdapter(Tree).validate_python({
        "type": "aggregate", "op": "highest",
        "source": {"type": "price", "symbol": "TCS"},
        "bars": 20,
    })
    result = _walk(tree, accessor=acc, state={})
    expected = float(df["close"].iloc[60 - 19 : 61].max())
    assert result == pytest.approx(expected, abs=1e-9), (
        f"aggregator highest got {result}, pandas max got {expected}"
    )


def test_aggregate_percentrank_in_unit_range():
    from backend.workflows.dsl.evaluator import _walk
    from backend.workflows.dsl.schema import Tree
    from pydantic import TypeAdapter
    df = _make_bars(120, seed=5)
    acc = BacktestDataAccessor(_loaded(df))
    acc.advance_to(100)
    tree = TypeAdapter(Tree).validate_python({
        "type": "aggregate", "op": "percentrank",
        "source": {"type": "indicator", "indicator": "atr",
                    "symbol": "TCS", "period": 14},
        "bars": 60,
    })
    result = _walk(tree, accessor=acc, state={})
    assert result is not None
    assert 0.0 <= result <= 1.0


def test_aggregate_barssince_counts_correctly():
    """Sinusoid that oscillates RSI above and below 30. barssince
    should report a small non-zero number for an as-of bar shortly
    after an oversold trough."""
    from backend.workflows.dsl.evaluator import _walk
    from backend.workflows.dsl.schema import Tree
    from pydantic import TypeAdapter
    n = 150
    t = np.arange(n)
    closes = 100.0 + 20.0 * np.sin(t / 6.0)
    dates = pd.date_range("2024-01-02", periods=n, freq="B").normalize()
    df = pd.DataFrame({
        "open": closes, "high": closes + 0.3, "low": closes - 0.3,
        "close": closes, "volume": np.full(n, 100_000.0),
    }, index=dates)
    acc = BacktestDataAccessor(_loaded(df))

    # Compute RSI<30 fire bars directly so we know what to expect.
    import pandas_ta_classic as ta
    rsi = ta.rsi(pd.Series(closes), length=14)
    fire_bars = list(np.where((rsi < 30).fillna(False))[0])
    assert fire_bars, "fixture is broken — need at least one RSI<30 fire"

    # Pick an as-of bar a few bars past the LAST observed fire so the
    # answer is a small positive integer.
    as_of = min(fire_bars[-1] + 4, n - 1)
    acc.advance_to(as_of)
    expected = as_of - fire_bars[-1]

    tree = TypeAdapter(Tree).validate_python({
        "type": "aggregate", "op": "barssince",
        "source": {
            "type": "comparison", "op": "<",
            "left": {"type": "indicator", "indicator": "rsi",
                     "symbol": "TCS", "period": 14},
            "right": {"type": "constant", "value": 30},
        },
        "bars": 60,
    })
    result = _walk(tree, accessor=acc, state={})
    assert result is not None, "barssince returned None despite a known fire"
    assert int(result) == expected, (
        f"barssince got {result}, expected {expected}"
    )


# ── Phase C.5 shortcut leaves through the evaluator ────────────────


def test_gap_leaf_matches_manual_computation():
    """A bar where open is below previous close → gap node returns
    the same percentage as (open - prev_close) / prev_close."""
    from backend.workflows.dsl.evaluator import _walk
    from backend.workflows.dsl.schema import Tree
    from pydantic import TypeAdapter

    n = 60
    closes = np.full(n, 100.0)
    opens = np.full(n, 100.0)
    # Engineer a gap-down at idx 40: open=98 while prev close=100.
    opens[40] = 98.0
    dates = pd.date_range("2024-01-02", periods=n, freq="B").normalize()
    df = pd.DataFrame({
        "open": opens, "high": np.maximum(opens, closes) + 0.5,
        "low": np.minimum(opens, closes) - 0.5,
        "close": closes, "volume": np.full(n, 100_000.0),
    }, index=dates)
    acc = BacktestDataAccessor(_loaded(df))
    acc.advance_to(40)

    tree = TypeAdapter(Tree).validate_python({"type": "gap", "symbol": "TCS"})
    result = _walk(tree, accessor=acc, state={})
    expected = (98.0 - 100.0) / 100.0
    assert result == pytest.approx(expected, abs=1e-9)


def test_pct_change_5bar_matches_pandas():
    from backend.workflows.dsl.evaluator import _walk
    from backend.workflows.dsl.schema import Tree
    from pydantic import TypeAdapter
    df = _make_bars(80, seed=3)
    acc = BacktestDataAccessor(_loaded(df))
    acc.advance_to(60)
    tree = TypeAdapter(Tree).validate_python(
        {"type": "pct_change", "symbol": "TCS", "bars": 5}
    )
    result = _walk(tree, accessor=acc, state={})
    cur = float(df["close"].iloc[60])
    past = float(df["close"].iloc[55])
    expected = (cur - past) / past
    assert result == pytest.approx(expected, abs=1e-9)


def test_math_subtract_two_prices_returns_correct_delta():
    """price(today) - price(yesterday) == today's close - yesterday's
    close."""
    from backend.workflows.dsl.evaluator import _walk
    from backend.workflows.dsl.schema import Tree
    from pydantic import TypeAdapter
    df = _make_bars(80, seed=11)
    acc = BacktestDataAccessor(_loaded(df))
    acc.advance_to(50)
    tree = TypeAdapter(Tree).validate_python({
        "type": "math", "op": "-",
        "operands": [
            {"type": "price", "symbol": "TCS"},
            {"type": "price", "symbol": "TCS", "offset": 1},
        ],
    })
    result = _walk(tree, accessor=acc, state={})
    expected = float(df["close"].iloc[50]) - float(df["close"].iloc[49])
    assert result == pytest.approx(expected, abs=1e-9)


def test_math_divide_by_zero_returns_none():
    """0-denominator on math/÷ must return None so Kleene UNKNOWN
    propagates rather than the engine raising."""
    from backend.workflows.dsl.evaluator import _walk
    from backend.workflows.dsl.schema import Tree
    from pydantic import TypeAdapter
    df = _make_bars(80)
    acc = BacktestDataAccessor(_loaded(df))
    acc.advance_to(30)
    tree = TypeAdapter(Tree).validate_python({
        "type": "math", "op": "/",
        "operands": [
            {"type": "constant", "value": 100},
            {"type": "constant", "value": 0},
        ],
    })
    assert _walk(tree, accessor=acc, state={}) is None


def test_strict_mode_shadow_check(monkeypatch):
    """In DSL_BACKTEST_STRICT mode the accessor performs a second
    computation over the truncated slice and asserts the values
    match. Confirm the path runs cleanly for a known-causal
    indicator."""
    monkeypatch.setenv("DSL_BACKTEST_STRICT", "1")
    df = _make_bars(80)
    acc = BacktestDataAccessor(_loaded(df))
    acc.advance_to(50)
    val = acc.get_indicator(symbol="TCS", indicator="rsi", period=14)
    assert val is not None
    # If RSI were not causal, the shadow check inside get_indicator
    # would have raised AssertionError. Reaching this line is the
    # signal that the indicator + accessor pair is safe.
