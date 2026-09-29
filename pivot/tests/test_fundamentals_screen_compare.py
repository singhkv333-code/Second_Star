"""screen_fundamentals with `symbols`: a side-by-side comparison of named
companies on the metrics the model chose, rendered as the screen card."""
from unittest import mock

import backend.services.fundamentals_screen as fs
from backend.agents.tool_executor import _risk_values


class _Rows:
    def mappings(self):
        return self

    def fetchall(self):
        return [{"sc_id": "TCS", "company_name": "Tata Consultancy", "nse_symbol": "TCS",
                 "ticker": "TCS", "industry_slug": "it", "val_pe": 24.1, "val_roe": None,
                 "val_market_cap": 1100000, "ctx_yr1_pct": -20.0, "total_matched": 1}]


class _Session:
    sql = ""

    def execute(self, sql, params):
        _Session.sql, _Session.params = str(sql), params
        return _Rows()

    def close(self):
        pass


def _compare(**kw):
    with mock.patch.object(fs, "_load_market_caps", return_value={"TCS": 1.0}), \
         mock.patch.object(fs, "_load_52w_change", return_value={}), \
         mock.patch.object(fs, "_load_trailing_pe", return_value={"TCS": 24.1}):
        return fs.screen_by_fundamentals([], session=_Session(), **kw)


def test_named_companies_are_kept_even_when_a_metric_is_missing():
    out = _compare(symbols=["tcs", "infy"], metrics=["pe", "roe"])
    sql = _Session.sql
    assert _Session.params["scope_syms"] == ["INFY", "TCS"]
    assert "LEFT JOIN m_roe" in sql and "\nJOIN m_" not in sql
    # an implausible value is shown as missing, never used to drop the row
    assert "CASE WHEN m_roe.v BETWEEN -200 AND 200 THEN m_roe.v END AS val_roe" in sql
    assert out["results"][0]["roe"] is None
    cols = [c["key"] for c in fs.screen_card_columns(out)]
    assert cols[:3] == ["market_cap_cr", "pe", "roe"]


def test_risk_values_read_a_compare_assets_table():
    table = {"results": {"tcs": {"sharpe": -1.3217, "max_drawdown_pct": float("nan")},
                         "bad": {"error": "Insufficient data"}}}
    assert _risk_values(table, ["sharpe", "max_drawdown"]) == {
        "TCS": {"sharpe": -1.32, "max_drawdown": None}}
