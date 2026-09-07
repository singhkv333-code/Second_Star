"""Exclusion-constraint coverage for the fundamentals screen
(services/fundamentals_screen.py).

Regression for the 2026-07-14 eval finding: "Create a basket for EV and
battery supply chain exposure that explicitly does NOT include Tata Motors
or any Adani Group company" routed to `screen_fundamentals` (not
`build_strategy`), and the tool had NO exclusion channel at all — the
returned ranking could never honour "does NOT include X" regardless of what
the model wanted to do. Fix: `screen_by_fundamentals` now accepts an
`exclude` list (named ticker/company, sector word, or "PSU") and hard-filters
the result post-ranking, disclosing any drop in `note` — mirroring
strategy_builder's `_apply_exclusions` so the two basket-shaped tools honour
the same vocabulary.

Uses the same stubbed-session pattern as test_fundamentals_screen_peg.py (the
module's raw SQL is Postgres-only) — no live DB required.
"""
from __future__ import annotations

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

    def execute(self, sql, params=None):
        return _FakeResult(self._rows)

    def close(self) -> None:
        pass


def _row(**val) -> dict:
    base = {
        "sc_id": "SC1", "company_name": "Test Co", "nse_symbol": "TESTCO",
        "ticker": "TESTCO", "industry_slug": "automobile",
    }
    base.update(val)
    return base


def test_exclude_drops_named_company_by_substring():
    """"does NOT include Tata Motors" — the row's company_name carries the
    full display name; a substring match must catch it even though the
    exclusion term is shorter/looser than the exact name."""
    rows = [
        _row(sc_id="SC1", nse_symbol="TATAMOTORS", ticker="TATAMOTORS",
             company_name="Tata Motors Limited", val_roce=20.0),
        _row(sc_id="SC2", nse_symbol="M&M", ticker="M&M",
             company_name="Mahindra & Mahindra Limited", val_roce=18.0),
    ]
    sess = _FakeSession(rows)
    out = screen_by_fundamentals(
        filters=[{"field": "roce", "op": ">", "value": 10}],
        session=sess,
        exclude=["Tata Motors"],
    )
    syms = {r["symbol"] for r in out["results"]}
    assert syms == {"M&M"}
    assert out["count"] == 1
    assert "excluded" in out["note"].lower()


def test_exclude_drops_every_name_in_a_named_group_by_substring():
    """"or any Adani Group company" — a single "Adani" term must catch every
    Adani-group listing (Green/Ports/Power/...), not just an exact ticker."""
    rows = [
        _row(sc_id="SC1", nse_symbol="ADANIGREEN", ticker="ADANIGREEN",
             company_name="Adani Green Energy Limited", val_roce=15.0),
        _row(sc_id="SC2", nse_symbol="ADANIPORTS", ticker="ADANIPORTS",
             company_name="Adani Ports and Special Economic Zone Limited", val_roce=17.0),
        _row(sc_id="SC3", nse_symbol="WAAREEENER", ticker="WAAREEENER",
             company_name="Waaree Energies Limited", val_roce=22.0),
    ]
    sess = _FakeSession(rows)
    out = screen_by_fundamentals(
        filters=[{"field": "roce", "op": ">", "value": 10}],
        session=sess,
        exclude=["Adani"],
    )
    syms = {r["symbol"] for r in out["results"]}
    assert syms == {"WAAREEENER"}


def test_exclude_psu_drops_non_bank_psus_by_membership_not_just_sector_name():
    """"PSU" must drop a govt-owned energy/metals name (ONGC/COALINDIA), not
    just names whose industry_slug happens to say "psu" — mirrors the same
    fix applied to strategy_builder._apply_exclusions."""
    rows = [
        _row(sc_id="SC1", nse_symbol="ONGC", ticker="ONGC",
             company_name="Oil & Natural Gas Corp", industry_slug="oilexploration", val_roce=14.0),
        _row(sc_id="SC2", nse_symbol="RELIANCE", ticker="RELIANCE",
             company_name="Reliance Industries", industry_slug="refineries", val_roce=12.0),
    ]
    sess = _FakeSession(rows)
    out = screen_by_fundamentals(
        filters=[{"field": "roce", "op": ">", "value": 10}],
        session=sess,
        exclude=["PSU"],
    )
    syms = {r["symbol"] for r in out["results"]}
    assert syms == {"RELIANCE"}


def test_no_exclude_terms_leaves_result_untouched():
    rows = [_row(sc_id="SC1", val_roce=20.0)]
    sess = _FakeSession(rows)
    out = screen_by_fundamentals(
        filters=[{"field": "roce", "op": ">", "value": 10}],
        session=sess,
        exclude=None,
    )
    assert out["count"] == 1
    assert "excluded per your stated preference" not in out["note"].lower()
