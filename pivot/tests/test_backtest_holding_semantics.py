"""Wave A — backtest holding semantics.

Covers the three holding shapes added so a chat user can express
"buy and hold", "buy on <date> and hold", and "I already own X — test
selling it":

  A1  hold_to_end exit kind (carry to the final bar; assumption string)
  A2  one-time run_at expansion in the workflow backtester
  A3  initial_position seeding in the DSL-tree engine
  A5  this file

Engine tests inject a fixed fetcher / monkeypatch ``_load_bars`` so
they run offline and deterministically (no yfinance).
"""
from __future__ import annotations

import asyncio
from datetime import date

import numpy as np
import pandas as pd
import pytest

from backend.workflows.dsl.backtest.engine import run_backtest
from backend.workflows.dsl.backtest.schema import (
    BacktestRequest,
    ExitPolicyDeclarative,
    ExitPolicyTree,
    InitialPosition,
    lower_exit_policy,
)


# ── Fixtures / helpers ──────────────────────────────────────────────


def _rising_series(n: int = 80, lo: float = 100.0, hi: float = 200.0) -> pd.DataFrame:
    """Monotonically rising closes — RSI climbs past 70, price stays
    above any low threshold after warmup. open == close so fills are
    predictable."""
    closes = np.linspace(lo, hi, n)
    dates = pd.date_range("2024-01-02", periods=n, freq="B").normalize()
    return pd.DataFrame({
        "open": closes, "high": closes + 0.5, "low": closes - 0.5,
        "close": closes, "volume": np.full(n, 100_000.0),
    }, index=dates)


def _fixed_fetcher(df_by_symbol: dict[str, pd.DataFrame]):
    def _fetch(symbol: str, start: date, end: date) -> pd.DataFrame:
        df = df_by_symbol.get(symbol.upper())
        if df is None:
            raise ValueError(f"no fixture data for {symbol}")
        mask = (df.index >= pd.Timestamp(start)) & (df.index <= pd.Timestamp(end))
        return df.loc[mask].copy()
    return _fetch


_PRICE_GT_90 = {
    "type": "comparison", "op": ">",
    "left": {"type": "price", "symbol": "TCS"},
    "right": {"type": "constant", "value": 90},
}


# ── A1 · hold_to_end ────────────────────────────────────────────────


def test_lower_hold_to_end_produces_unreachable_tree():
    """The declarative hold_to_end lowers to a bars_held >= huge tree so
    the exit can never fire — the engine's end-of-window force-close is
    the only exit."""
    lowered = lower_exit_policy(ExitPolicyDeclarative(kind="hold_to_end"))
    assert isinstance(lowered, ExitPolicyTree)
    t = lowered.tree
    assert t["op"] == ">="
    assert t["left"]["type"] == "position" and t["left"]["field"] == "bars_held"
    # An unreachable bound — no finite backtest reaches this many bars.
    assert t["right"]["value"] >= 1e12
    assert lowered.exit_at == "next_open"


def test_engine_hold_to_end_carries_position_to_final_bar():
    """hold_to_end never sells early: exactly one position opens, and it
    force-closes on the last bar (no hidden n-day sale)."""
    series = _rising_series(80)
    fetcher = _fixed_fetcher({"TCS": series})
    req = BacktestRequest(
        tree=_PRICE_GT_90, primary_symbol="TCS",
        start_date=date(2024, 1, 1), end_date=date(2024, 6, 1),
        starting_capital=100_000.0, quantity=10,
        exit_policy=ExitPolicyDeclarative(kind="hold_to_end"),
        save=False,
    )
    result = run_backtest(request=req, user_id=1, fetcher=fetcher)

    # Only one position ever opened — it was held the whole way.
    assert result.metrics.total_trades == 1
    trade = result.trades[0]
    assert trade.exit_reason == "force_close"
    # Exit lands on the last bar of the loaded window.
    assert trade.exit_date == series.index[-1].date()
    # Rising series → the held position made money.
    assert result.metrics.total_return_pct > 0


# ── A3 · initial_position seeding ───────────────────────────────────


def test_engine_seeds_initial_position_and_exits_on_rule():
    """A seeded holding is opened at window start with the given cost
    basis + quantity, and the exit rule closes it."""
    series = _rising_series(80)
    fetcher = _fixed_fetcher({"TCS": series})
    exit_tree = {
        "type": "comparison", "op": ">",
        "left": {"type": "indicator", "indicator": "rsi",
                 "symbol": "TCS", "period": 14},
        "right": {"type": "constant", "value": 70},
    }
    req = BacktestRequest(
        tree=_PRICE_GT_90, primary_symbol="TCS",
        start_date=date(2024, 1, 1), end_date=date(2024, 6, 1),
        starting_capital=100_000.0, quantity=5,   # distinct from seeded qty
        exit_policy=ExitPolicyTree(kind="tree", tree=exit_tree, exit_at="next_open"),
        initial_position=InitialPosition(quantity=50, avg_price=100.0),
        save=False,
    )
    result = run_backtest(request=req, user_id=1, fetcher=fetcher)

    # The FIRST trade is the seeded one: 50 shares @ 100 cost basis,
    # entered at the window's first bar, closed by the RSI>70 exit tree.
    first = result.trades[0]
    assert first.quantity == 50
    assert first.entry_price == pytest.approx(100.0)
    assert first.entry_date == series.index[0].date()
    assert first.exit_reason == "exit_tree"


def test_engine_initial_position_pnl_math():
    """Seeded 10 shares @ ₹100, held to the window end on a series that
    ends at ~₹200 → gross P&L ≈ (last_close - 100) × 10."""
    series = _rising_series(60, lo=100.0, hi=200.0)
    fetcher = _fixed_fetcher({"TCS": series})
    req = BacktestRequest(
        tree=_PRICE_GT_90, primary_symbol="TCS",
        start_date=date(2024, 1, 1), end_date=date(2024, 5, 1),
        starting_capital=100_000.0, quantity=5,
        exit_policy=ExitPolicyDeclarative(kind="hold_to_end"),
        initial_position=InitialPosition(quantity=10, avg_price=100.0),
        save=False,
    )
    result = run_backtest(request=req, user_id=1, fetcher=fetcher)

    seeded = result.trades[0]
    assert seeded.quantity == 10
    assert seeded.entry_price == pytest.approx(100.0)
    assert seeded.exit_reason == "force_close"
    last_close = float(series["close"].iloc[-1])
    expected_gross = (last_close - 100.0) * 10
    assert seeded.gross_pnl == pytest.approx(expected_gross, rel=1e-6)
    # Net is gross minus costs → strictly below gross but still positive.
    assert 0 < seeded.net_pnl < seeded.gross_pnl


def test_engine_initial_position_default_basis_is_first_open():
    """Omitting avg_price → cost basis defaults to the first bar's open."""
    series = _rising_series(50)
    fetcher = _fixed_fetcher({"TCS": series})
    req = BacktestRequest(
        tree=_PRICE_GT_90, primary_symbol="TCS",
        start_date=date(2024, 1, 1), end_date=date(2024, 4, 1),
        starting_capital=100_000.0, quantity=5,
        exit_policy=ExitPolicyDeclarative(kind="hold_to_end"),
        initial_position=InitialPosition(quantity=20, entry_date=date(2020, 1, 1)),
        save=False,
    )
    result = run_backtest(request=req, user_id=1, fetcher=fetcher)
    seeded = result.trades[0]
    assert seeded.quantity == 20
    assert seeded.entry_price == pytest.approx(float(series["open"].iloc[0]))
    # entry_date is cosmetic — the user-supplied date is preserved.
    assert seeded.entry_date == date(2020, 1, 1)


# ── A2 · one-time run_at in the workflow backtester ─────────────────


def _bdates(n: int) -> pd.DatetimeIndex:
    return pd.bdate_range("2024-01-01", periods=n)


def test_expand_run_at_fires_once_within_window():
    from backend.services.workflow_backtester import _expand_run_at
    dates = _bdates(60)   # 2024-01-01 .. ~2024-03-25
    fires = _expand_run_at("2024-02-01T09:15", dates)
    assert len(fires) == 1
    # First trading day on/after 2024-02-01.
    assert fires[0].date() >= date(2024, 2, 1)
    assert fires[0].date() == min(d.date() for d in dates if d.date() >= date(2024, 2, 1))


def test_expand_run_at_past_window_fires_at_start_with_note():
    from backend.services.workflow_backtester import _expand_run_at
    dates = _bdates(30)
    notes: list[str] = []
    fires = _expand_run_at("2020-01-01T09:15", dates, notes)
    assert fires == [dates[0]]
    assert notes and "predates" in notes[0]


def test_expand_run_at_after_window_no_fire_with_note():
    from backend.services.workflow_backtester import _expand_run_at
    dates = _bdates(30)
    notes: list[str] = []
    fires = _expand_run_at("2030-01-01T09:15", dates, notes)
    assert fires == []
    assert notes and "after the backtest window" in notes[0]


def test_backtest_workflow_one_time_buy_and_hold(monkeypatch):
    """A trigger.schedule with a one-time run_at + place_order fires
    exactly once and the position marks-to-market to the window end."""
    from backend.services import workflow_backtester as wb

    n = 40
    closes = np.linspace(100, 160, n)
    idx = pd.bdate_range("2024-01-01", periods=n)
    bars = pd.DataFrame({
        "Open": closes, "High": closes + 1, "Low": closes - 1,
        "Close": closes, "Volume": np.full(n, 1_000_000.0),
    }, index=idx)
    monkeypatch.setattr(wb, "_load_bars", lambda sym, period, **_kw: bars)

    steps = [
        {"step_type": "trigger.schedule",
         "config": {"run_at": "2024-01-10T09:15", "timezone": "Asia/Kolkata"}},
        {"step_type": "action.place_order",
         "config": {"symbol": "TCS", "side": "buy", "quantity": 100}},
    ]
    res = wb.backtest_workflow(steps, period="1y", name="BuyAndHold")

    buys = [s for s in res.signals if s["side"] == "buy"]
    # Fires exactly once (one-time schedule) — no recurring buys.
    assert len(buys) == 1
    # No sell → position held; rising series → positive return, MTM'd.
    assert res.metrics["n_trades"] == 1
    assert res.metrics["total_return_pct"] > 0


def test_one_time_buy_and_hold_tracks_underlying(monkeypatch):
    """F7 regression: a one-time buy-and-hold's STRATEGY return must ≈ the
    underlying's move over the window (within costs), NOT collapse to ~0
    because a fixed tiny share quantity left most of the ₹10L capital idle.
    Both the bare-quantity (deploy-capital) and the notional shapes must
    track the ~+20% underlying and roughly match the buy-and-hold benchmark;
    the summary must label the still-open position as marked-to-market."""
    from backend.services import workflow_backtester as wb

    n = 250
    closes = np.linspace(2000, 2400, n)   # +20% underlying move
    idx = pd.bdate_range("2023-01-02", periods=n)
    bars = pd.DataFrame({
        "Open": closes, "High": closes + 2, "Low": closes - 2,
        "Close": closes, "Volume": np.full(n, 1_000_000.0),
    }, index=idx)
    monkeypatch.setattr(wb, "_load_bars", lambda sym, period, **_kw: bars)

    def _run(order_cfg):
        steps = [
            {"step_type": "trigger.schedule",
             "config": {"run_at": "2023-01-10T09:15", "timezone": "Asia/Kolkata"}},
            {"step_type": "action.place_order",
             "config": {"symbol": "RELIANCE", "side": "buy", **order_cfg}},
        ]
        return wb.backtest_workflow(steps, period="1y", name="BuyHold")

    # All three shapes must track the underlying: bare quantity (deploy-cash
    # default), explicit notional, AND — thanks to the buy-and-hold
    # normalisation — even a fixed small `quantity` (which used to dilute the
    # return to ~0.4% because ~97% of the ₹10L sat idle in cash).
    for cfg in ({}, {"notional_inr": 1_000_000}, {"quantity": 10}):
        res = _run(cfg)
        strat = res.metrics["total_return_pct"]
        bench = res.metrics["benchmark_return_pct"]
        assert res.metrics["n_trades"] == 1
        # Strategy tracks the underlying: within ~1.5pp of the benchmark
        # (friction), and unambiguously NOT the diluted ~0.4% bug.
        assert abs(strat - bench) < 1.5, (cfg, strat, bench)
        assert strat > 15.0, (cfg, strat)
        # Still-open hold is labelled as marked-to-market, not a round-trip.
        assert "still OPEN" in res.summary_text


def test_backtest_workflow_run_at_predating_window_notes_it(monkeypatch):
    from backend.services import workflow_backtester as wb

    n = 30
    closes = np.linspace(100, 130, n)
    idx = pd.bdate_range("2024-01-01", periods=n)
    bars = pd.DataFrame({
        "Open": closes, "High": closes + 1, "Low": closes - 1,
        "Close": closes, "Volume": np.full(n, 1_000_000.0),
    }, index=idx)
    monkeypatch.setattr(wb, "_load_bars", lambda sym, period, **_kw: bars)

    steps = [
        {"step_type": "trigger.schedule",
         "config": {"run_at": "2020-06-01T09:15", "timezone": "Asia/Kolkata"}},
        {"step_type": "action.place_order",
         "config": {"symbol": "TCS", "side": "buy", "quantity": 50}},
    ]
    res = wb.backtest_workflow(steps, period="1y", name="PastBuy")
    buys = [s for s in res.signals if s["side"] == "buy"]
    assert len(buys) == 1  # still fires (at window start)
    assert "window start" in res.summary_text or "predates" in res.summary_text


# ── A1/A3 · chat-tool handler carries the assumption string ─────────


def _run_handler(args: dict) -> dict:
    from backend.services import _dsl_chat_tools as dct
    return asyncio.run(dct.backtest_dsl_tree(args))


def _patch_handler(monkeypatch, symbol: str = "TCS"):
    """Patch the LLM translation + engine fetcher so the async chat
    handler runs fully offline."""
    from backend.services import _dsl_chat_tools as dct
    import backend.workflows.dsl.backtest.engine as eng

    tree = {
        "type": "comparison", "op": ">",
        "left": {"type": "price", "symbol": symbol},
        "right": {"type": "constant", "value": 90},
    }

    async def _fake_translate(condition, **kwargs):
        return tree, {"stub": True}

    monkeypatch.setattr(dct, "translate_condition_to_tree", _fake_translate)

    # Wrap the real engine so it always runs against the fixed fetcher
    # (the handler otherwise calls it with fetcher=None → yfinance).
    fixed = _fixed_fetcher({symbol: _rising_series(120)})
    real_run = eng.run_backtest
    monkeypatch.setattr(
        eng, "run_backtest",
        lambda *, request, user_id, fetcher=None: real_run(
            request=request, user_id=user_id, fetcher=fixed,
        ),
    )


def test_handler_default_exit_carries_assumption(monkeypatch):
    """No exit rule stated → the default 10-bar hold is surfaced as an
    explicit assumption (never silent)."""
    _patch_handler(monkeypatch)
    out = _run_handler({
        "condition": "buy TCS", "primary_symbol": "TCS", "interval": "1d",
    })
    assert any("10-bar hold (assumed)" in a for a in out["assumptions"]), out["assumptions"]
    assert "Assumptions:" in out["summary_text"]


def test_handler_explicit_exit_kind_has_no_default_assumption(monkeypatch):
    """When the caller pins exit_kind, we don't fabricate the 'assumed'
    note for it."""
    _patch_handler(monkeypatch)
    out = _run_handler({
        "condition": "buy TCS", "primary_symbol": "TCS", "interval": "1d",
        "exit_kind": "n_day_hold", "exit_bars": 20,
    })
    assert not any("assumed" in a for a in out["assumptions"])


def test_handler_hold_to_end_assumption(monkeypatch):
    _patch_handler(monkeypatch)
    out = _run_handler({
        "condition": "buy TCS and hold", "primary_symbol": "TCS",
        "interval": "1d", "exit_kind": "hold_to_end",
    })
    assert any("held to the end" in a for a in out["assumptions"]), out["assumptions"]


def test_handler_seeded_position_assumption_and_run(monkeypatch):
    _patch_handler(monkeypatch)
    out = _run_handler({
        "condition": "sell when RSI above 70", "primary_symbol": "TCS",
        "interval": "1d", "exit_kind": "hold_to_end",
        "initial_position": {"quantity": 50, "avg_price": 1400},
    })
    assert any("Seeded an existing holding of 50 TCS" in a for a in out["assumptions"])
    # The seeded trade actually ran (a trade closed at the window end).
    assert out["metrics"]["n_trades"] >= 1


# ── Short-position honesty guard ─────────────────────────────────────
#
# This engine is long-only end to end (workflows/dsl/backtest/engine.py
# only ever buys-at-entry / sells-at-exit). Ground-truth eval caught a
# "Backtest shorting BANKNIFTY futures on a gap-down" request silently
# running LONG and being narrated as a short — a mechanics fabrication,
# not just a wrong number. The handler must refuse instead, via either
# channel: the explicit `direction` tool arg, or short language surviving
# in the natural-language condition/exit_condition text.


def test_handler_refuses_explicit_short_direction(monkeypatch):
    _patch_handler(monkeypatch)
    with pytest.raises(ValueError, match=r"only simulates LONG"):
        _run_handler({
            "condition": "buy BANKNIFTY on a gap-down",
            "primary_symbol": "BANKNIFTY", "interval": "1d",
            "direction": "short",
        })


def test_handler_refuses_short_language_in_condition(monkeypatch):
    """Backstop: even if the chat LLM never sets `direction`, short
    language surviving verbatim in `condition` must still be refused —
    it must NOT silently run a long backtest."""
    _patch_handler(monkeypatch)
    with pytest.raises(ValueError, match=r"only simulates LONG"):
        _run_handler({
            "condition": "short BANKNIFTY on a gap-down",
            "primary_symbol": "BANKNIFTY", "interval": "1d",
        })


def test_handler_refuses_short_on_ticker_starting_with_benign_prefix(monkeypatch):
    """A ticker like MARUTI/MARICO/EMAMI starts with a benign token ('MA',
    'EMA') the crossover-leg strip must ignore — the strip has to consume
    whole benign WORDS only, never a ticker's leading letters, or a real
    'short MARUTI' request would be swallowed and silently run long."""
    _patch_handler(monkeypatch, symbol="MARUTI")
    with pytest.raises(ValueError, match=r"only simulates LONG"):
        _run_handler({
            "condition": "short MARUTI on a gap-down",
            "primary_symbol": "MARUTI", "interval": "1d",
        })


def test_handler_refuses_short_language_in_exit_condition(monkeypatch):
    _patch_handler(monkeypatch)
    with pytest.raises(ValueError, match=r"only simulates LONG"):
        _run_handler({
            "condition": "RSI(14) < 30", "primary_symbol": "TCS",
            "interval": "1d", "exit_condition": "cover the short at RSI > 70",
        })


def test_handler_does_not_misfire_on_benign_short_phrasing(monkeypatch):
    """'short-term' / 'short of' must NOT trip the refusal — only a
    genuine short-selling intent word should."""
    _patch_handler(monkeypatch)
    out = _run_handler({
        "condition": "buy TCS when momentum is short-term bullish",
        "primary_symbol": "TCS", "interval": "1d",
    })
    assert "metrics" in out  # ran normally, no ValueError raised


def test_handler_does_not_misfire_on_short_ma_crossover(monkeypatch):
    """'short SMA crosses long SMA' is THE flagship backtest_dsl_tree
    crossover use case ('short'/'long' as MA-leg adjectives) — must NOT
    be misread as a short-sell request."""
    _patch_handler(monkeypatch)
    out = _run_handler({
        "condition": "buy TCS when the short SMA crosses above the long SMA",
        "primary_symbol": "TCS", "interval": "1d",
    })
    assert "metrics" in out  # ran normally, no ValueError raised


# ── propose_dsl_workflow: same long-only gap, live registration path ─
# Flagged by the backtest_dsl_tree fix's own author: a "short X" entry
# condition must not silently register a BUY automation — there's no
# short-entry action_kind in this schema, so refuse honestly instead.


def test_propose_dsl_workflow_refuses_short_entry_as_buy(monkeypatch):
    from backend.services import _dsl_chat_tools as dct

    async def _fake_translate(condition, **kwargs):
        return ({"type": "comparison", "op": ">",
                 "left": {"type": "price", "symbol": "BANKNIFTY"},
                 "right": {"type": "constant", "value": 100}}, {"stub": True})

    monkeypatch.setattr(dct, "translate_condition_to_tree", _fake_translate)
    with pytest.raises(ValueError, match="short"):
        asyncio.run(dct.propose_dsl_workflow({
            "condition": "short BANKNIFTY when it gaps down",
            "primary_symbol": "BANKNIFTY", "action_kind": "buy_market",
            "quantity": 25,
        }))


def test_propose_dsl_workflow_short_term_phrasing_still_builds(monkeypatch):
    from backend.services import _dsl_chat_tools as dct

    async def _fake_translate(condition, **kwargs):
        return ({"type": "comparison", "op": ">",
                 "left": {"type": "price", "symbol": "TCS"},
                 "right": {"type": "constant", "value": 100}}, {"stub": True})

    monkeypatch.setattr(dct, "translate_condition_to_tree", _fake_translate)
    out = asyncio.run(dct.propose_dsl_workflow({
        "condition": "buy TCS on a short-term dip below 100",
        "primary_symbol": "TCS", "action_kind": "buy_market", "quantity": 10,
    }))
    assert out.get("steps")  # built normally, no false-positive refusal
