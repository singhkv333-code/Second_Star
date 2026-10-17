"""The public stock-page data: built from caches, never a price feed, and a
missing section never takes the page down."""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from backend.routers import public_company as pc
from backend.routers import financials as fin_router
from backend.routers import screener as sc
from backend.routers import stock_detail as sd
from backend.services import market_explore as mx


class _NoRedis:
    def get(self, _k):
        return None

    def set(self, *_a, **_k):
        return None


UNIVERSE = [
    {"symbol": "BIGOIL", "name": "Big Oil Limited", "sector": "energy", "sector_label": "Oil & Gas", "mcap_cr": 900},
    {"symbol": "SOFTCO", "name": "Soft Co Ltd", "sector": "it", "sector_label": "IT", "mcap_cr": 700},
    {"symbol": "MIDOIL", "name": "Mid Oil Limited", "sector": "energy", "sector_label": "Oil & Gas", "mcap_cr": 300},
    {"symbol": "TINYOIL", "name": "Tiny Oil Limited", "sector": "energy", "sector_label": "Oil & Gas", "mcap_cr": 10},
]
TECH = {s["symbol"]: {"close": 100.0, "ret_1d": 1.0, "ret_1y": 12.5} for s in UNIVERSE}


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    monkeypatch.setattr(pc, "redis_client", _NoRedis())
    pc._list_memo.update(at=0.0, body=None)
    monkeypatch.setattr(sc, "_market_metrics_cached", lambda: ({}, "warming"))
    monkeypatch.setattr(sc, "_technical_features_cached", lambda: (TECH, 20740, "charto"))
    monkeypatch.setattr(sc, "_fundamentals_map_cached", lambda: {"BIGOIL": {"pe": 18.0, "roe": 11.0}})
    monkeypatch.setattr(sc, "_full_universe", lambda: [])
    monkeypatch.setattr(mx, "reference", lambda: {})
    monkeypatch.setattr(mx, "ranked_universe", lambda *_a: list(UNIVERSE))
    monkeypatch.setattr(mx, "eps_map", lambda: {})
    # Any reach for a live price is a failure of this module's contract.
    import backend.kite.live_quote as lq
    monkeypatch.setattr(lq, "get_kite_quote", lambda *_a, **_k: pytest.fail("public page reached Kite"))
    yield


def _fin(available=True):
    return {
        "available": available,
        "company": {"name": "Big Oil Limited", "bse_code": "500001", "industry_slug": "refineries"},
        "latest": {"roe": {"value": 11.0, "period_label": "Mar 26", "period_end": "2026-03-31", "unit": "%",
                           "basis": "consolidated", "source": "moneycontrol", "line_item": "x"},
                   "roce": None},
        "history": {"revenue": [{"period_label": "Mar 26", "period_end": "2026-03-31", "value": 5000.0, "unit": "crore"},
                                {"period_label": "Mar 25", "period_end": "2025-03-31", "value": None, "unit": "crore"}]},
        "profile": {"name": "Big Oil Limited", "blurb": "Copied from somewhere else.", "sector": "Energy",
                    "industry": "Oil & Gas Refining", "website": "https://bigoil.example"},
    }


def test_the_directory_is_the_whole_universe_largest_first_on_one_basis():
    body = pc.get_companies()
    assert [c["symbol"] for c in body["companies"]] == ["BIGOIL", "SOFTCO", "MIDOIL", "TINYOIL"]
    assert [c["rank"] for c in body["companies"]] == [1, 2, 3, 4]
    assert body["companies"][0]["pe"] == 18.0
    assert body["as_of"]["prices"] == "eod"
    assert body["sectors"] == {"energy": "Oil & Gas", "it": "IT"}


def test_an_empty_universe_is_a_503_and_is_not_remembered(monkeypatch):
    monkeypatch.setattr(mx, "ranked_universe", lambda *_a: [])
    with pytest.raises(HTTPException) as e:
        pc.get_companies()
    assert e.value.status_code == 503
    monkeypatch.setattr(mx, "ranked_universe", lambda *_a: list(UNIVERSE))
    assert pc.get_companies()["count"] == 4


def test_a_stock_page_has_peers_from_its_own_sector_and_no_borrowed_summary(monkeypatch):
    monkeypatch.setattr(fin_router, "get_financials", lambda *_a, **_k: _fin())
    monkeypatch.setattr(sd, "get_quarters", lambda *_a, **_k: {"basis": "consolidated", "quarters": [
        {"period_label": "Jun 26", "revenue": 1300.0, "net_profit": 120.0, "extra": 1}]})
    monkeypatch.setattr(sd, "get_shareholding", lambda *_a, **_k: {
        "available": True, "quarter": "2026-06-30", "promoter_pct": 50.1, "pledge_pct": 0.0,
        "groups": [{"label": "Promoters", "pct": 50.1, "children": []}],
        "quarters": [{"quarter": "2026-06-30", "Promoters": 50.1}], "holders": [{"name": "X"}]})
    body = pc.get_stock("bigoil")
    assert body["symbol"] == "BIGOIL" and body["name"] == "Big Oil"
    assert [p["symbol"] for p in body["peers"]] == ["MIDOIL", "TINYOIL"]
    assert "blurb" not in (body["industry"] or "") and "Copied" not in str(body)
    assert body["latest"] == {"roe": {"value": 11.0, "period_label": "Mar 26", "period_end": "2026-03-31",
                                       "unit": "%", "basis": "consolidated", "source": "moneycontrol"}}
    assert body["history"]["revenue"] == [{"period_label": "Mar 26", "period_end": "2026-03-31",
                                           "value": 5000.0, "unit": "crore"}]
    assert body["quarters"]["quarters"][0]["revenue"] == 1300.0
    assert "extra" not in body["quarters"]["quarters"][0]
    assert body["shareholding"]["groups"] == [{"label": "Promoters", "pct": 50.1}]
    assert "holders" not in body["shareholding"]


def test_a_failing_section_is_left_out_not_fatal(monkeypatch):
    monkeypatch.setattr(fin_router, "get_financials", lambda *_a, **_k: _fin())

    def boom(*_a, **_k):
        raise RuntimeError("db down")

    monkeypatch.setattr(sd, "get_quarters", boom)
    monkeypatch.setattr(sd, "get_shareholding", boom)
    body = pc.get_stock("SOFTCO")
    assert body["quarters"] is None and body["shareholding"] is None
    assert body["market"]["price"] == 100.0


def test_unknown_and_malformed_symbols_are_404(monkeypatch):
    monkeypatch.setattr(fin_router, "get_financials", lambda *_a, **_k: _fin(available=False))
    for bad in ("NOSUCHCO", "../etc", "A" * 40):
        with pytest.raises(HTTPException) as e:
            pc.get_stock(bad)
        assert e.value.status_code == 404


def test_the_routes_answer_without_a_token(client):
    r = client.get("/api/public/companies")
    assert r.status_code == 200, r.text
    assert r.json()["count"] == 4
