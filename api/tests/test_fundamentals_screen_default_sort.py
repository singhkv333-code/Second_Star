"""Regression coverage for the bare-sector-ask default sort field.

Reported 2026-07-14 (live): "screen me the best bank stocks" (no metric,
no sort named) returned "Bank — ranked by ROE" — the screener silently
defaulted an open-ended sector ask to ROE desc, baking in a "quality"
investment opinion the user never asked for. Fixed in
`screen_by_fundamentals` (services/fundamentals_screen.py, the
`sort_field is None and sector` branch) to default to market cap
(a neutral size/recognizability ordering) instead.
"""
from __future__ import annotations

from backend.services import fundamentals_screen as fs
from backend.services.fundamentals_screen import screen_by_fundamentals


class _FakeResult:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    def mappings(self):
        return self

    def fetchall(self):
        return self._rows


class _FakeSession:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows
        self.last_sql: str = ""
        self.last_params: dict = {}

    def execute(self, sql, params=None):
        self.last_sql = str(sql)
        self.last_params = dict(params or {})
        return _FakeResult(self._rows)

    def close(self) -> None:
        pass


def _row(**val) -> dict:
    base = {
        "sc_id": "SC1", "company_name": "Test Bank", "nse_symbol": "TESTBANK",
        "ticker": "TESTBANK", "industry_slug": "banks",
    }
    base.update(val)
    return base


def test_bare_sector_ask_defaults_to_market_cap_not_roe(monkeypatch):
    # `_load_market_caps` hits a separate enrich-DB session the fake
    # session here doesn't stand in for — stub it directly so the
    # real market_cap sort path (not the "enrich DB down" fallback,
    # which itself reverts to roe) is what's actually exercised.
    monkeypatch.setattr(fs, "_load_market_caps", lambda: {"SC1": 50_000.0})
    rows = [_row(val_market_cap=50_000.0)]
    sess = _FakeSession(rows)

    out = screen_by_fundamentals(
        filters=[], sector="bank", session=sess,
    )

    assert "market cap" in out["note"].lower()
    assert "roe" not in out["note"].lower()
    # market_cap must be the actual ORDER BY, not just mentioned in the
    # note — the module's SELECT always carries a fixed display set of
    # metrics (roe included) regardless of what's sorted on.
    assert "order by" in sess.last_sql.lower()
    order_clause = sess.last_sql.lower().split("order by", 1)[1]
    assert "market_cap" in order_clause or "caps." in order_clause


def test_explicit_quality_sort_still_uses_roe():
    """An EXPLICIT ask for ROE must still work — only the silent bare-ask
    default changed, not the ability to sort by ROE on request."""
    rows = [_row(val_roe=22.0)]
    sess = _FakeSession(rows)

    out = screen_by_fundamentals(
        filters=[], sector="bank", sort_by={"field": "roe", "dir": "desc"},
        session=sess,
    )

    assert "roe" in sess.last_sql.lower()
    assert out["results"][0]["roe"] == 22.0
