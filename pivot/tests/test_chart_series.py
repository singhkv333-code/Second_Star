"""Charts are drawn from a tool's own data, and never read by the model."""
import json
from datetime import date

from backend.services import chart_series
from backend.services.chart_series import bar_chart, period_label, series_chart
from backend.services.tool_registry import ToolResult


def test_series_are_thinned_but_keep_their_last_point():
    pts = [(f"2021-01-{i % 28 + 1:02d}", i) for i in range(1000)]
    line = series_chart("A", [("A", pts)])["series"][0]["points"]
    assert len(line) <= chart_series.MAX_POINTS + 1
    assert line[-1]["v"] == 999 and line[0]["v"] == 0


def test_fiscal_labels():
    assert period_label(date(2026, 3, 31)) == "FY26"
    assert period_label(date(2025, 12, 31)) == "Dec 25"
    assert period_label(date(2026, 6, 30), quarterly=True) == "Q1 FY27"
    assert period_label(date(2026, 3, 31), quarterly=True) == "Q4 FY26"


def test_bars_align_companies_by_period_and_leave_gaps():
    c = bar_chart("Revenue", [
        ("A", [("2024-03-31", 10), ("2025-03-31", 12), ("2026-03-31", 15)]),
        ("B", [("2025-03-31", 7), ("2026-03-31", "9"), ("2027-03-31", None)]),
    ], unit="Rs. Cr.")
    assert c["labels"] == ["FY24", "FY25", "FY26"]
    assert c["series"][1]["values"] == [None, 7.0, 9.0]


def test_under_three_periods_is_no_chart():
    assert bar_chart("R", [("A", [("2025-03-31", 1), ("2026-03-31", 2)])]) is None


def test_the_model_sees_neither_points_nor_bars():
    chart = bar_chart("R", [("A", [("2024-03-31", 1), ("2025-03-31", 2), ("2026-03-31", 3)])])
    r = ToolResult(name="query_financials", args={}, success=True,
                   data={"symbols": {}, "_charts": [chart]})
    assert "_charts" not in json.loads(r.to_llm_string())
    assert r.data["_charts"][0] is chart


def test_units_belong_to_the_field_not_to_mcs_table_header():
    from backend.services.financials_query import FIELD_LABELS, FIELD_UNITS
    assert FIELD_UNITS["eps_basic"] == "Rs per share"
    assert FIELD_UNITS["roe"] == "%" and FIELD_UNITS["price_to_book"] == "x"
    assert "revenue" not in FIELD_UNITS          # amounts keep the DB's Rs. Cr.
    assert FIELD_LABELS["eps_basic"] == "EPS"
