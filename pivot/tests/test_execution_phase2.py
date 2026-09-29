"""Execution-mode audit fixes: engine honesty (gap stops, N-bar holds,
benchmark drawdown), the draft-backtest entry point, and the removal of
keyword gates from the proposal path. Offline, deterministic."""
from __future__ import annotations

import asyncio
from datetime import date

import numpy as np
import pandas as pd
import pytest

from backend.workflows.dsl.backtest.engine import run_backtest
from backend.workflows.dsl.backtest.schema import BacktestRequest

_ALWAYS = {"type": "always"}


def _fetcher(df):
    def _f(symbol, start, end, interval="1d"):
        m = (df.index >= pd.Timestamp(start)) & (df.index <= pd.Timestamp(end))
        return df.loc[m].copy()
    return _f


def _bars(o, h=None, l=None, c=None, start="2024-01-01"):
    o = np.asarray(o, float)
    idx = pd.bdate_range(start, periods=len(o))
    return pd.DataFrame({
        "open": o, "high": o + 1 if h is None else h,
        "low": o - 1 if l is None else l,
        "close": o if c is None else c, "volume": 1e6}, index=idx)


def _run(df, exit_policy, **kw):
    payload = {"tree": _ALWAYS, "primary_symbol": "XYZ",
               "start_date": df.index[0].date().isoformat(),
               "end_date": df.index[-1].date().isoformat(),
               "starting_capital": 100000.0, "quantity": 10, "save": False,
               "interval": "1d", "exit_policy": exit_policy, **kw}
    return run_backtest(request=BacktestRequest.model_validate(payload),
                        user_id=0, fetcher=_fetcher(df))


def test_gap_down_through_stop_fills_at_the_open():
    o = np.full(60, 100.0)
    o[40:] = 80.0
    df = _bars(o, l=o - 1, c=o)
    r = _run(df, {"kind": "stop_loss_pct", "value": 0.05})
    first = r.trades[0]
    assert first.exit_reason == "stop_loss"
    assert first.exit_price == pytest.approx(80.0)      # not 95.0


def test_stop_without_a_gap_still_fills_at_the_stop():
    o = np.full(60, 100.0)
    l = o - 1
    l[40:] = 90.0                                       # trades through, opens at 100
    df = _bars(o, l=l)
    r = _run(df, {"kind": "stop_loss_pct", "value": 0.05})
    assert r.trades[0].exit_price == pytest.approx(95.0)


def test_n_day_hold_holds_exactly_n_bars():
    df = _bars(np.linspace(100, 130, 80))
    r = _run(df, {"kind": "n_day_hold", "bars": 10})
    t = r.trades[0]
    dates = list(df.index.date)
    assert dates.index(t.exit_date) - dates.index(t.entry_date) == 10


def test_benchmark_drawdown_is_reported_over_the_window():
    c = np.concatenate([np.linspace(100, 200, 40), np.linspace(200, 100, 40)])
    df = _bars(c)
    r = _run(df, {"kind": "hold_to_end"})
    m = r.metrics
    assert m.benchmark_max_drawdown_pct == pytest.approx(50.0, abs=0.5)
    assert m.benchmark_return_pct is not None


# ── the draft entry point and the proposal path ─────────────────────


def _patch_fetch(monkeypatch, df):
    import backend.workflows.dsl.backtest.engine as eng
    real = eng.run_backtest
    monkeypatch.setattr(
        eng, "run_backtest",
        lambda *, request, user_id, fetcher=None: real(
            request=request, user_id=user_id, fetcher=_fetcher(df)))


def _draft_steps(with_exit: bool):
    px = {"type": "price", "symbol": "XYZ", "exchange": "NSE", "basis": "close"}
    steps = [
        {"step_type": "trigger.compound", "config": {
            "entry": {"type": "comparison", "op": ">", "left": px,
                      "right": {"type": "constant", "value": 90}},
            "symbol": "XYZ", "exchange": "NSE"}},
        {"step_type": "action.place_order",
         "config": {"symbol": "XYZ", "side": "buy", "quantity": 10}},
    ]
    if with_exit:
        steps.append({"step_type": "trigger.exit_compound", "config": {
            "entry": {"type": "comparison", "op": ">=",
                      "left": {"type": "position", "field": "unrealised_pct"},
                      "right": {"type": "constant", "value": 0.05}},
            "target_symbol": "XYZ"}})
    return steps


def test_backtest_dsl_draft_uses_the_drafts_own_trees(monkeypatch):
    from backend.services import _dsl_chat_tools as dct
    df = _bars(np.linspace(100, 200, 120))
    _patch_fetch(monkeypatch, df)

    async def _no_translate(*a, **k):
        raise AssertionError("a draft backtest must not re-translate")
    monkeypatch.setattr(dct, "translate_condition_to_tree", _no_translate)

    out = asyncio.run(dct.backtest_dsl_draft({
        "steps": _draft_steps(True), "name": "d",
        "start_date": df.index[0].date().isoformat(),
        "end_date": df.index[-1].date().isoformat()}))
    assert out["_render_hint"] == "indicator_backtest_chart"
    assert out["metrics"]["n_trades"] >= 1
    assert out["trades"][0]["exit_reason"] == "exit_tree"
    assert out["metrics"]["benchmark_max_drawdown_pct"] is not None


def test_backtest_dsl_draft_without_exit_says_it_holds_to_the_end(monkeypatch):
    from backend.services import _dsl_chat_tools as dct
    df = _bars(np.linspace(100, 200, 120))
    _patch_fetch(monkeypatch, df)
    out = asyncio.run(dct.backtest_dsl_draft({
        "steps": _draft_steps(False),
        "start_date": df.index[0].date().isoformat(),
        "end_date": df.index[-1].date().isoformat()}))
    assert any("held to the end" in a for a in out["assumptions"])


def _stub_translate(monkeypatch, symbol="ONGC", exchange="NSE"):
    from backend.services import _dsl_chat_tools as dct
    tree = {"type": "comparison", "op": "<",
            "left": {"type": "pct_change", "symbol": "CRUDEOIL",
                     "exchange": "MCX", "bars": 5},
            "right": {"type": "constant", "value": -0.05}}
    async def _t(condition, **kw):
        return tree, {}
    monkeypatch.setattr(dct, "translate_condition_to_tree", _t)
    return dct


@pytest.mark.parametrize("cond,exit_c", [
    ("buy when brent crude falls 5% in a week", None),
    ("buy when bitcoin is above its 50 day average", None),
    ("RSI(14) < 30", "10 percent trailing stop on my RELIANCE position"),
    ("on Mondays when the trend is up", None),
])
def test_proposal_no_longer_refuses_on_keywords(monkeypatch, cond, exit_c):
    dct = _stub_translate(monkeypatch)
    args = {"condition": cond, "primary_symbol": "ONGC", "quantity": 5}
    if exit_c:
        args["exit_condition"] = exit_c
    out = asyncio.run(dct.propose_dsl_workflow(args))
    assert out["_render_hint"] == "workflow_draft_card"


def test_trigger_exchange_comes_from_the_leaves(monkeypatch):
    dct = _stub_translate(monkeypatch)
    from backend.services._dsl_chat_tools import _tree_exchange
    assert _tree_exchange({"symbol": "CRUDEOIL", "exchange": "MCX"},
                          "CRUDEOIL") == "MCX"
    assert _tree_exchange({"symbol": "TCS"}, "TCS") == "NSE"
    out = asyncio.run(dct.propose_dsl_workflow(
        {"condition": "x", "primary_symbol": "CRUDEOIL", "quantity": 1}))
    assert out["steps"][0]["config"]["exchange"] == "MCX"


def test_missing_quantity_does_not_route_to_ask_user(monkeypatch):
    dct = _stub_translate(monkeypatch)
    with pytest.raises(ValueError) as e:
        asyncio.run(dct.propose_dsl_workflow(
            {"condition": "x", "primary_symbol": "ONGC"}))
    assert "ASK_USER" not in str(e.value)
    assert "state it" in str(e.value)


def test_notify_only_text_points_at_the_alert_tool(monkeypatch):
    dct = _stub_translate(monkeypatch)
    with pytest.raises(ValueError) as e:
        asyncio.run(dct.propose_dsl_workflow(
            {"condition": "x", "primary_symbol": "ONGC",
             "action_kind": "notify_only"}))
    assert "doesn't send alerts" not in str(e.value)
    assert "alert" in str(e.value)


def test_explicit_short_direction_is_still_refused():
    from backend.services import _dsl_chat_tools as dct
    with pytest.raises(ValueError, match="LONG"):
        asyncio.run(dct.backtest_dsl_tree(
            {"condition": "x", "primary_symbol": "TCS", "direction": "short",
             "exit_kind": "hold_to_end"}))


def test_holding_only_test_needs_no_entry_wording(monkeypatch):
    from backend.services import _dsl_chat_tools as dct
    df = _bars(np.linspace(100, 200, 120))
    _patch_fetch(monkeypatch, df)

    async def _t(condition, **kw):
        return {"type": "comparison", "op": ">=",
                "left": {"type": "position", "field": "unrealised_pct"},
                "right": {"type": "constant", "value": 0.10}}, {}
    monkeypatch.setattr(dct, "translate_condition_to_tree", _t)
    out = asyncio.run(dct.backtest_dsl_tree({
        "primary_symbol": "XYZ", "exit_condition": "up 10%",
        "initial_position": {"quantity": 50},
        "start_date": df.index[0].date().isoformat(),
        "end_date": df.index[-1].date().isoformat()}))
    assert any("No entry rule" in a for a in out["assumptions"])
    assert out["metrics"]["n_trades"] == 1
