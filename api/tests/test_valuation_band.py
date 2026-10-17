"""P/E from market cap over trailing profit: right through a bonus issue."""
from datetime import date, timedelta

import pandas as pd

from backend.services import valuation_band as vb


def _run(monkeypatch, closes, earnings, shares=100e7):
    idx = pd.to_datetime([d for d, _ in closes])
    monkeypatch.setattr("backend.core.data.historical.get_close_series",
                        lambda s, period: pd.Series([c for _, c in closes], index=idx))
    monkeypatch.setattr(vb, "_identity", lambda s: {"symbol": s, "isin": "X", "name": "Co"})
    monkeypatch.setattr(vb, "_shares", lambda s, _i=None: shares)
    monkeypatch.setattr(vb, "_earnings", lambda who, since: (earnings, "consolidated TTM"))
    return vb.pe_band("CO", 5)


def test_pe_is_market_cap_over_the_profit_known_at_the_time(monkeypatch):
    start = date(2024, 1, 1)
    closes = [(start + timedelta(days=i), 100.0) for i in range(60)]
    # 100 cr shares x Rs 100 = Rs 10,000 cr; profit 500 then 1,000 from day 30
    earnings = [(start, 500.0), (start + timedelta(days=30), 1000.0)]
    r = _run(monkeypatch, closes, earnings)
    assert r["available"]
    pts = r["_charts"][0]["points"]
    assert pts[0]["v"] == 20.0 and pts[-1]["v"] == 10.0
    assert r["current"] == 10.0 and r["high"] == 20.0 and r["percentile"] == 0


def test_loss_making_days_have_no_pe(monkeypatch):
    start = date(2024, 1, 1)
    closes = [(start + timedelta(days=i), 100.0) for i in range(40)]
    earnings = [(start, -50.0), (start + timedelta(days=10), 1000.0)]
    r = _run(monkeypatch, closes, earnings)
    assert len(r["_charts"][0]["points"]) == 30


def test_unknown_symbol_is_said_plainly(monkeypatch):
    monkeypatch.setattr(vb, "_identity", lambda s: None)
    monkeypatch.setattr("backend.core.data.historical.get_close_series",
                        lambda s, period: pd.Series(dtype=float))
    monkeypatch.setattr(vb, "_shares", lambda s, _i=None: None)
    assert vb.pe_band("NOPE") == {"available": False, "error": "unknown symbol NOPE"}
