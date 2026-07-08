"""portfolio_source resolver: paper-book holdings adapted to the Kite shape."""
from __future__ import annotations

from backend.services.portfolio_source import _paper_to_kite_holding


def test_paper_holding_maps_to_kite_shape():
    row = {
        "symbol": "INFY", "quantity": 10, "avg_cost": 1400.0,
        "last_price": 1500.0, "unrealized_pnl": 1000.0, "day_pnl": 50.0,
        "invested": 14000.0,
    }
    out = _paper_to_kite_holding(row)
    assert out["tradingsymbol"] == "INFY"
    assert out["exchange"] == "NSE"
    assert out["quantity"] == 10
    assert out["average_price"] == 1400.0
    assert out["last_price"] == 1500.0
    assert out["pnl"] == 1000.0
    # day_change is PER-SHARE (total day_pnl / qty)
    assert out["day_change"] == 5.0
    assert out["day_change_percentage"] == round(50.0 / 14000.0 * 100, 2)


def test_paper_holding_unmarked_lot_falls_back_to_cost():
    row = {"symbol": "TCS", "quantity": 4, "avg_cost": 3000.0,
           "last_price": None, "unrealized_pnl": 0.0, "day_pnl": 0.0,
           "invested": 12000.0}
    out = _paper_to_kite_holding(row)
    assert out["last_price"] == 3000.0  # unmarked → book cost
    assert out["day_change"] == 0.0


def test_paper_holding_zero_qty_no_divide_by_zero():
    row = {"symbol": "X", "quantity": 0, "avg_cost": 0.0, "last_price": None,
           "unrealized_pnl": 0.0, "day_pnl": 0.0, "invested": 0.0}
    out = _paper_to_kite_holding(row)
    assert out["day_change"] == 0.0
    assert out["day_change_percentage"] == 0.0
