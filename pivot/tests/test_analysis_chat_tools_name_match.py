"""Tests for the company-name plausibility guard in
``backend/services/analysis_chat_tools.py`` (``_names_plausibly_match`` /
``_enrich_row_symbol_verified``), used by ``_apply_enrichment`` to decide
whether a `pivot_enrich` DB row is safe to merge into a fundamentals
snapshot.

Root cause (found 2026-07-14): the offline script that builds
`pivot_enrich` keys its yfinance lookup off Moneycontrol's internal
`ticker`/`sc_id` shorthand, which is frequently a truncated/generic label
for the SAME company (e.g. sc_id 'HZ' company_name 'Hind Zinc' for the real
Hindustan Zinc Limited; sc_id 'BEL' company_name 'BLS E-Services' for the
real Bharat Electronics Limited) rather than a different company. A pure
name-string heuristic (substring/acronym) can't safely bridge those without
also reopening a genuine collision hole: sc_id 'BI' company_name
'Britannia' carries long_name 'Bilcare Limited' because 'BI' is
Moneycontrol's shorthand, not Britannia's real NSE ticker, so the
enrichment script's yfinance fetch landed on an unrelated company.

``_enrich_row_symbol_verified`` recovers the true-match cases via a
ticker-identity check instead of a name-string heuristic: if the enrich
row's own `ticker`/`yf_symbol` matches the symbol we resolved by, the
row's `long_name`/sector are guaranteed correct regardless of what
Moneycontrol's `company_name` field says. Britannia/Bilcare must keep
failing every one of these checks — that's the acid test for any future
change here.
"""
from __future__ import annotations

from backend.services.analysis_chat_tools import (
    _acronym,
    _apply_enrichment,
    _enrich_row_symbol_verified,
    _names_plausibly_match,
    _normalise_company_name,
)


# ── _acronym / _normalise_company_name ─────────────────────────────────────


def test_acronym_of_spelled_out_name():
    assert _acronym("Tata Consultancy Services") == "tcs"
    assert _acronym("Oil and Natural Gas Corporation") == "ongc"


def test_normalise_folds_accents():
    # Nestlé -> nestle (previously the é broke substring matching entirely).
    assert _normalise_company_name("Nestlé India Limited") == "nestleindia"
    assert _normalise_company_name("Nestle") == "nestle"


# ── _names_plausibly_match: true matches ───────────────────────────────────


def test_substring_match_passes():
    assert _names_plausibly_match("Reliance", "Reliance Industries Limited")


def test_acronym_match_passes():
    assert _names_plausibly_match("TCS", "Tata Consultancy Services Limited")
    assert _names_plausibly_match("ONGC", "Oil and Natural Gas Corporation")


def test_accent_folded_substring_passes():
    # Fixed by the accent-fold in _normalise_company_name.
    assert _names_plausibly_match("Nestle", "Nestlé India Limited")


# ── _names_plausibly_match: the Britannia/Bilcare acid test ───────────────


def test_genuine_collision_still_rejected():
    """Britannia's enrich row (sc_id 'BI') is a genuine ticker-shorthand
    collision carrying a DIFFERENT company's long_name. This must never
    start passing — that would mean attaching Bilcare's sector/summary to
    a Britannia lookup."""
    assert not _names_plausibly_match("Britannia", "Bilcare Limited")


def test_unrelated_root_sharing_names_still_rejected():
    # Shared word-root across genuinely different companies must not match.
    assert not _names_plausibly_match("Rishabh Yarn", "Rishabh Instruments Limited")


# ── _enrich_row_symbol_verified: the ticker-identity discriminator ────────


class _FakeRec:
    def __init__(self, ticker, yf_symbol):
        self.ticker = ticker
        self.yf_symbol = yf_symbol


def test_symbol_verified_true_when_ticker_matches_resolved_symbol():
    # HINDZINC: enrich row's own ticker is the real NSE symbol -> trust long_name
    # even though company_name ("Hind Zinc") doesn't string-match long_name
    # ("Hindustan Zinc Limited").
    out = {"symbol": "HINDZINC"}
    rec = _FakeRec(ticker="HINDZINC", yf_symbol="HINDZINC.NS")
    assert _enrich_row_symbol_verified(out, rec)


def test_symbol_verified_false_for_britannia_style_collision():
    # BRITANNIA: enrich row's ticker is 'BI' (MC internal shorthand), not the
    # real NSE symbol -> must NOT bypass the name check.
    out = {"symbol": "BRITANNIA"}
    rec = _FakeRec(ticker="BI", yf_symbol="BI.NS")
    assert not _enrich_row_symbol_verified(out, rec)


def test_symbol_verified_falls_back_to_yf_symbol_base():
    out = {"symbol": "DIVISLAB"}
    rec = _FakeRec(ticker=None, yf_symbol="DIVISLAB.BO")
    assert _enrich_row_symbol_verified(out, rec)


# ── _apply_enrichment end-to-end via a stubbed enrich_db row ──────────────


class _FakeCompanyEnrichment:
    """Minimal stand-in for enrich_db.CompanyEnrichment covering the fields
    _apply_enrichment reads."""

    def __init__(self, **kw):
        defaults = dict(
            sc_id=None, ticker=None, yf_symbol=None, company_name=None,
            long_name=None, sector=None, industry=None,
            long_business_summary=None, website=None,
            full_time_employees=None, promoter_holding_pct=None,
            institution_holding_pct=None,
        )
        defaults.update(kw)
        for k, v in defaults.items():
            setattr(self, k, v)


def _run_apply_enrichment(monkeypatch, out, rec):
    """Stub enrich_db so _apply_enrichment merges `rec` via the sc_id path,
    without touching the real Postgres DBs or yfinance."""
    from backend.services import analysis_chat_tools as act

    monkeypatch.setattr(act.enrich_db, "is_enabled", lambda: True)
    monkeypatch.setattr(act.enrich_db, "get_by_sc_id", lambda sc_id: rec)
    monkeypatch.setattr(act.enrich_db, "get_by_ticker", lambda ticker: None)
    _apply_enrichment(out)
    return out


def test_apply_enrichment_merges_hindzinc_style_truncated_name(monkeypatch):
    """sc_id-resolved row, company_name ('Hind Zinc') doesn't string-match
    long_name ('Hindustan Zinc Limited'), but ticker verifies -> merge."""
    out = {"symbol": "HINDZINC", "sc_id": "HZ", "name": "Hind Zinc", "sector": None}
    rec = _FakeCompanyEnrichment(
        sc_id="HZ", ticker="HINDZINC", yf_symbol="HINDZINC.NS",
        company_name="Hind Zinc", long_name="Hindustan Zinc Limited",
        sector="Basic Materials",
    )
    out = _run_apply_enrichment(monkeypatch, out, rec)
    assert out.get("enriched") is True
    assert out.get("long_name") == "Hindustan Zinc Limited"
    assert out.get("sector") == "Basic Materials"


def test_apply_enrichment_merges_bel_style_generic_company_name(monkeypatch):
    """BEL: company_name is generic/wrong-looking ('BLS E-Services') but the
    row's own ticker ('BEL') verifies against the resolved symbol -> merge
    (this is the same class of gap as HINDZINC/ACC/GAIL/HINDPETRO/DIVISLAB/
    NESTLEIND/HINDUNILVR)."""
    out = {"symbol": "BEL", "sc_id": "BEL", "name": "BLS E-Services", "sector": None}
    rec = _FakeCompanyEnrichment(
        sc_id="BEL", ticker="BEL", yf_symbol="BEL.NS",
        company_name="BLS E-Services", long_name="Bharat Electronics Limited",
        sector="Industrials",
    )
    out = _run_apply_enrichment(monkeypatch, out, rec)
    assert out.get("enriched") is True
    assert out.get("long_name") == "Bharat Electronics Limited"


def test_apply_enrichment_skips_britannia_bilcare_collision(monkeypatch):
    """Acid test: a genuine ticker-shorthand collision (sc_id 'BI') must
    stay rejected — never enrich Britannia with Bilcare's data."""
    out = {"symbol": "BRITANNIA", "sc_id": "BI", "name": "Britannia", "sector": None}
    rec = _FakeCompanyEnrichment(
        sc_id="BI", ticker="BI", yf_symbol="BI.NS",
        company_name="Britannia", long_name="Bilcare Limited",
        sector="Healthcare",
    )
    out = _run_apply_enrichment(monkeypatch, out, rec)
    assert not out.get("enriched")
    assert out.get("sector") is None
    assert out.get("long_name") is None


def test_apply_enrichment_skips_ticker_fallback_collision(monkeypatch):
    """The `_enrich_row_symbol_verified` bypass must be scoped to the sc_id
    path only. A row reached via the get_by_ticker() fallback is, by
    construction, always ticker-equal to the query (that's what the SQL
    filtered on) — so if the bypass were mistakenly applied there too, it
    would readmit exactly the wrong-company collisions this guard exists to
    catch. Simulate: no sc_id-keyed row, but a ticker-collision row surfaces
    via the fallback with a mismatched long_name -> must still be skipped."""
    from backend.services import analysis_chat_tools as act

    out = {"symbol": "BI", "sc_id": None, "name": "Britannia", "sector": None}
    rec = _FakeCompanyEnrichment(
        sc_id="BI", ticker="BI", yf_symbol="BI.NS",
        company_name="Britannia", long_name="Bilcare Limited",
        sector="Healthcare",
    )
    monkeypatch.setattr(act.enrich_db, "is_enabled", lambda: True)
    monkeypatch.setattr(act.enrich_db, "get_by_sc_id", lambda sc_id: None)
    monkeypatch.setattr(act.enrich_db, "get_by_ticker", lambda ticker: rec)
    _apply_enrichment(out)
    assert not out.get("enriched")
    assert out.get("sector") is None
