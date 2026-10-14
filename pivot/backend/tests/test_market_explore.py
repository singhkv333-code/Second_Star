"""The Markets page's arithmetic, on maps this file writes."""
from __future__ import annotations

from backend.services import market_explore as mx


def _universe() -> list[dict]:
    return [
        {"symbol": "BIGCO", "name": "Big Co Limited", "sector": "energy", "sector_label": "Energy", "mcap_cr": 900},
        {"symbol": "MIDCO", "name": "Mid Co Ltd", "sector": "energy", "sector_label": "Energy", "mcap_cr": 100},
        {"symbol": "TECHCO", "name": "Tech Co Limited", "sector": "technology", "sector_label": "Technology", "mcap_cr": 500},
        {"symbol": "NOCAP", "name": "No Cap", "sector": "technology", "sector_label": "Technology", "mcap_cr": None},
    ]


TECH = {
    "BIGCO": {"close": 100, "ret_1d": 2.0, "ret_1w": 3.0, "ret_1m": 5.0, "sma50_rel": 1, "sma200_rel": 4,
              "dist_52w_high": -0.5, "dist_52w_low": 40},
    "MIDCO": {"close": 50, "ret_1d": -1.0, "ret_1w": -1.0, "ret_1m": 1.0, "sma50_rel": -2, "sma200_rel": 1,
              "dist_52w_high": -20, "dist_52w_low": 0.5},
    "TECHCO": {"close": 200, "ret_1d": -4.0, "ret_1w": 1.0, "ret_1m": -3.0, "sma50_rel": -1, "sma200_rel": -6,
               "dist_52w_high": -30, "dist_52w_low": 10},
}


def _compose(metrics=None, src="none", eps=None):
    return mx.compose(_universe(), {"TECHCO": {"pe": 33.3}}, metrics or {}, src, TECH, 20740,
                      {}, eps, top_n=2)


def test_top_n_by_market_cap_and_names_shortened():
    d = _compose()
    assert [x["symbol"] for x in d["stocks"]] == ["BIGCO", "TECHCO"]
    assert d["stocks"][0]["name"] == "Big Co"


def test_end_of_day_basis_when_live_prices_are_thin():
    d = _compose(metrics={"BIGCO": {"price": 101, "change_pct": 3.0}}, src="kite")
    assert d["as_of"]["prices"] == "eod"
    assert d["stocks"][0]["change"] == 2.0          # the matrix, not the live quote
    assert d["as_of"]["eod_day"] == "2026-10-14"


def test_live_basis_when_coverage_is_enough():
    m = {"BIGCO": {"price": 101, "change_pct": 3.0}, "TECHCO": {"price": 190, "change_pct": -5.0}}
    d = _compose(metrics=m, src="yfinance")
    assert d["as_of"]["prices"] == "live"
    assert [x["change"] for x in d["stocks"]] == [3.0, -5.0]


def test_pe_from_trailing_eps_with_earnings_yield_fallback():
    d = _compose(eps={"BIGCO": 4.0})
    big, tech = d["stocks"]
    assert big["pe"] == 25.0                        # 100 / 4
    assert tech["pe"] == 33.3                       # no EPS: the 1/EY value


def test_breadth_counts_and_histogram():
    d = mx.compose(_universe(), {}, {}, "none", TECH, 20740, {}, None, top_n=3)
    b = d["breadth"]
    assert (b["advancers"], b["decliners"], b["total"]) == (1, 2, 3)
    assert b["above_sma200"] == 2 and b["near_high"] == 1 and b["near_low"] == 1
    assert sum(b["histogram"]["counts"]) == 3
    # -4 falls in (-5, -3], -1 in (-2, -1], +2 in (1, 2]
    assert b["histogram"]["counts"][1] == 1 and b["histogram"]["counts"][3] == 1 and b["histogram"]["counts"][6] == 1


def test_sector_change_is_cap_weighted():
    d = mx.compose(_universe(), {}, {}, "none", TECH, 20740, {}, None, top_n=3)
    energy = next(s for s in d["sectors"] if s["key"] == "energy")
    assert energy["change"] == round((900 * 2.0 + 100 * -1.0) / 1000, 2)
    assert energy["leaders"][0] == "BIGCO" and energy["best"] == "BIGCO" and energy["worst"] == "MIDCO"


def test_grounding_gate():
    facts = {"advancers": 300, "decliners": 180, "top_gainers": [{"name": "Big Co", "change_pct": 4.21}]}
    assert mx.grounded("Advancers led 300 to 180; Big Co rose +4.21%.", facts)
    assert mx.grounded("Big Co rose 4.2% and 52-week highs were few.", facts)
    assert not mx.grounded("Advancers led 310 to 180.", facts)
    assert not mx.grounded("NIFTY closed at 24,500.", facts)


def test_fallback_brief_uses_only_facts():
    d = mx.compose(_universe(), {}, {}, "none", TECH, 20740, {}, None, top_n=3)
    f = mx.brief_facts(d)
    text = mx.fallback_brief(f)
    assert mx.grounded(text, f)
    assert text.startswith("Decliners led 2 to 1")


def test_india_sectors_from_industry_slugs():
    assert mx.sector_of("SBIN", "bankspublicsector") == "banks"
    assert mx.sector_of("HINDUNILVR", "personalcare") == "fmcg"
    assert mx.sector_of("ITC", "itc") == "fmcg"                    # override
    assert mx.sector_of("DLF", "constructioncontractingrealestate") == "realty"
    assert mx.sector_of("LT", "infrastructuregeneral") == "infra"
    assert mx.sector_of("X", "medicalequipmentsuppliesaccessories") == "pharma"
    assert mx.sector_of("X", "powertransmissionequipment") == "capital_goods"
    assert mx.sector_of("X", "miscellaneous", "technology") == "it"   # yfinance fallback
    assert mx.sector_of("X", "miscellaneous") == "diversified"


def test_market_cap_from_shares_beats_a_missing_enrichment_row():
    ref = {"SBIN": {"name": "State Bank of India", "slug": "bankspublicsector", "shares": 9.23e9},
           "TINY": {"name": "Tiny Ltd", "slug": "trading", "shares": 1e6},
           "GOLDBEES": {"name": "Gold ETF", "slug": "etf", "etf": True, "shares": 1e9}}
    screener = [{"symbol": "TINY", "name": "Tiny", "sector": "other", "mcap_cr": 5}]
    tech = {"SBIN": {"close": 800.0}, "GOLDBEES": {"close": 60.0}}
    u = mx.ranked_universe(screener, ref, {}, tech)
    assert [r["symbol"] for r in u] == ["SBIN", "TINY"]
    assert u[0]["mcap_cr"] == round(800 * 9.23e9 / 1e7) and u[0]["sector_label"] == "Banks"
    assert u[1]["mcap_cr"] == 5                                     # enrichment fallback
