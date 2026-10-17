"""The strategy lab: templates build valid trees, bad requests are refused
with a reason, and the report's figures are the arithmetic they claim."""
import pytest

import strategy_lab as L


def test_every_template_builds_entry_and_exit_with_defaults():
    for t in L.TEMPLATES:
        entry, exit_tree, p = L.trees(t["id"], "INFY", "1d", {})
        assert entry["type"] == "comparison" and exit_tree["type"] == "comparison"
        assert set(p) == {x["key"] for x in t["params"]}


def test_params_are_bounded_and_ordered():
    with pytest.raises(L.LabError, match="between"):
        L.trees("rsi_revert", "INFY", "1d", {"period": 0})
    with pytest.raises(L.LabError, match="shorter"):
        L.trees("ma_cross", "INFY", "1d", {"fast": 50, "slow": 20})
    with pytest.raises(L.LabError, match="below"):
        L.trees("rsi_revert", "INFY", "1d", {"low": 50, "high": 50})
    with pytest.raises(L.LabError, match="one of"):
        L.trees("ma_cross", "INFY", "1d", {"kind": "wma"})


def test_supertrend_reads_direction_not_the_line():
    entry, exit_tree, _ = L.trees("supertrend", "INFY", "1d", {})
    assert entry["left"]["indicator"] == "supertrend" and entry["right"]["value"] == 0
    assert entry["op"] == "crosses_above" and exit_tree["op"] == "crosses_below"


def test_resolve_template_makes_a_draft_the_paper_book_accepts():
    plan = L.resolve({"template": "macd", "symbol": "tcs", "interval": "1h", "period": "6m", "quantity": 5})
    kinds = [s["step_type"] for s in plan["draft"]["steps"]]
    assert kinds == ["trigger.compound", "trigger.exit_compound", "action.place_order"]
    order = plan["draft"]["steps"][-1]["config"]
    assert order == {"symbol": "TCS", "side": "buy", "quantity": 5}
    assert plan["args"]["interval"] == "1h" and plan["args"]["start_date"]
    assert plan["source"] == {"kind": "template", "template": "macd", "params": {"fast": 12, "slow": 26, "signal": 9}}


def test_resolve_refuses_with_reasons():
    with pytest.raises(L.LabError, match="Interval"):
        L.resolve({"template": "macd", "symbol": "TCS", "interval": "5m"})
    with pytest.raises(L.LabError, match="runs over"):
        L.resolve({"template": "macd", "symbol": "TCS", "interval": "15m", "period": "5y"})
    with pytest.raises(L.LabError, match="symbol"):
        L.resolve({"template": "macd"})
    with pytest.raises(L.LabError, match="Sign in"):
        L.resolve({"strategy_id": 3})
    with pytest.raises(L.LabError, match="Say what"):
        L.resolve({})
    with pytest.raises(L.LabError, match="Capital"):
        L.resolve({"template": "macd", "symbol": "TCS", "capital": 5})


def test_resolve_saved_uses_the_owners_draft():
    seen = {}
    def lookup(sid):
        seen["sid"] = sid
        return {"name": "Mine", "steps": [{"step_type": "trigger.compound", "config": {}}]}
    plan = L.resolve({"strategy_id": "7"}, saved_draft=lookup)
    assert seen["sid"] == 7 and plan["args"]["name"] == "Mine"
    assert plan["source"] == {"kind": "saved", "strategy_id": 7}
    with pytest.raises(L.LabError, match="no draft"):
        L.resolve({"strategy_id": 8}, saved_draft=lambda sid: None)


def _res():
    eq = [("2024-01-31", 100.0), ("2024-02-29", 110.0), ("2024-03-29", 99.0),
          ("2024-04-30", 121.0), ("2025-01-31", 121.0)]
    trades = [
        {"net_pnl": 100.0, "return_pct": 0.10, "entry_date": "2024-01-01", "exit_date": "2024-01-11", "exit_reason": "exit_tree"},
        {"net_pnl": -50.0, "return_pct": -0.05, "entry_date": "2024-02-01", "exit_date": "2024-02-03", "exit_reason": "exit_tree"},
        {"net_pnl": -25.0, "return_pct": -0.02, "entry_date": "2024-03-01", "exit_date": "2024-03-02", "exit_reason": "exit_tree"},
        {"net_pnl": 200.0, "return_pct": 0.20, "entry_date": "2024-04-01", "exit_date": "2024-04-05", "exit_reason": "force_close"},
    ]
    return {"trades": trades,
            "equity_curve": [{"t": t, "v": v} for t, v in eq],
            "price_curve": [{"t": t, "v": 100.0} for t, _ in eq]}


def test_report_arithmetic():
    r = L.report(_res())
    assert r["trades"] == 4 and r["wins"] == 2 and r["losses"] == 2
    assert r["profit_factor"] == round(300 / 75, 2)
    assert r["expectancy"] == round(225 / 4, 2)
    assert r["avg_win"] == 150 and r["avg_loss"] == -37.5 and r["payoff"] == 4.0
    assert r["best_trade_pct"] == 20 and r["worst_trade_pct"] == -5
    assert r["max_win_streak"] == 1 and r["max_loss_streak"] == 2
    assert r["avg_hold_days"] == round((10 + 2 + 1 + 4) / 4, 1)
    assert r["open_at_end"] == 1


def test_report_drawdown_and_months():
    r = L.report(_res())
    assert r["curve"]["drawdown"] == [0.0, 0.0, -10.0, 0.0, 0.0]
    assert r["max_dd_at"] == "2024-03-29" and r["longest_underwater_bars"] == 1
    y24 = next(x for x in r["monthly"] if x["year"] == "2024")
    assert y24["months"][:4] == [0.0, 10.0, -10.0, 22.22]
    assert y24["total"] == 21.0
    y25 = next(x for x in r["monthly"] if x["year"] == "2025")
    assert y25["months"][0] == 0.0 and y25["total"] == 0.0


def test_no_losing_trade_leaves_profit_factor_undefined():
    res = _res()
    res["trades"] = [t for t in res["trades"] if t["net_pnl"] > 0]
    assert L.report(res)["profit_factor"] is None


def test_curve_is_thinned_but_keeps_both_ends():
    eq = [{"t": f"2024-01-{i:02d}", "v": 100 + i} for i in range(1, 29)] * 40
    r = L.report({"trades": [], "equity_curve": eq, "price_curve": []}, cap=50)
    assert len(r["curve"]["t"]) <= 51
    assert r["curve"]["equity"][0] == 101 and r["curve"]["equity"][-1] == 128


def test_report_series_for_charts():
    # a steady 0.1% a bar: a perfect log-linear trend, and zero volatility
    eq = [{"t": f"2024-01-{(i % 28) + 1:02d}T{i:04d}", "v": 100000 * 1.001 ** i} for i in range(80)]
    res = {"interval": "1d", "trades": [{"net_pnl": 10, "return_pct": 0.02}, {"net_pnl": -5, "return_pct": -0.01},
                                        {"net_pnl": 7, "return_pct": 0.015}],
           "equity_curve": eq, "price_curve": eq}
    r = L.report(res)
    assert r["trend"]["r2"] > 0.999
    assert abs(r["trend"]["pace_pct"] - (1.001 ** 252 - 1) * 100) < 0.5
    assert r["volatility"]["annual_pct"] < 1e-6
    assert r["curve"]["vol"][0] is None and r["curve"]["vol"][-1] is not None
    assert len(r["curve"]["trend"]) == len(r["curve"]["t"])
    d = r["distribution"]
    assert sum(b["n"] for b in d["bins"]) == 3
    assert d["bins"][0]["lo"] <= -1 and d["bins"][-1]["hi"] >= 2
    assert r["trade_returns"] == [2.0, -1.0, 1.5]


def test_hold_curve_from_closes_and_its_check():
    eq = [{"t": "2024-01-01", "v": 1000.0}, {"t": "2024-01-02", "v": 1000.0}, {"t": "2024-01-03", "v": 1010.0}]
    closes = [("2024-01-01T00:00:00", 50.0), ("2024-01-02T00:00:00", 55.0), ("2024-01-03T00:00:00", 60.0)]
    ok = {"equity_curve": eq, "metrics": {"benchmark_return_pct": 20.0}}
    h = L.hold_curve(ok, closes)
    assert h == [1000.0, 1100.0, 1200.0]
    assert L.report(ok, hold=h)["curve"]["hold"] == [1000.0, 1100.0, 1200.0]
    # disagrees with the engine's own buy & hold: no line at all
    assert L.hold_curve({"equity_curve": eq, "metrics": {"benchmark_return_pct": 5.0}}, closes) is None
    # without closes there is no hold line, and price_curve is never read as one
    r = L.report({"equity_curve": eq, "price_curve": eq})
    assert r["curve"]["hold"] == [None, None, None]


def test_hold_curve_intraday_date_stamps_align_by_position():
    eq = [{"t": "2026-04-06", "v": 1000.0}] * 3 + [{"t": "2026-04-07", "v": 1000.0}]
    closes = [("2026-04-03T15:15:00", 9.0),           # warm-up bar before the curve
              ("2026-04-06T09:15:00", 10.0), ("2026-04-06T10:15:00", 11.0),
              ("2026-04-06T11:15:00", 12.0), ("2026-04-07T09:15:00", 15.0)]
    assert L.hold_curve({"equity_curve": eq, "metrics": {"benchmark_return_pct": 50.0}}, closes) == [1000.0, 1100.0, 1200.0, 1500.0]
    # ends on the wrong day: no line
    assert L.hold_curve({"equity_curve": eq, "metrics": {}}, closes[:-1]) is None
