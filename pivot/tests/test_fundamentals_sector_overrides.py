"""Regression coverage for `_INDUSTRY_SLUG_OVERRIDES` — data-quality
corrections for known-wrong `mc.companies.industry_slug` values.

Reported 2026-07-14 (live eval): a pharma screen surfaced IGL
(Indraprastha Gas Ltd, a Delhi-NCR CNG/PNG city-gas distributor) tagged
as pharma. Verified via direct DB query: IGL's real industry_slug is
'hospitalsmedicalservices' — an upstream (moneycontrol) data error, not
a bug in Pivot's sector-mapping rules (which correctly map that slug to
"pharma", given that input). The fix corrects the slug for known-bad
rows before both the SQL sector FILTER and the displayed sector LABEL
see it, so the two can never disagree.
"""
from __future__ import annotations

from backend.services.fundamentals_screen import (
    _corrected_industry_slug_sql,
    _INDUSTRY_SLUG_OVERRIDES,
    _sector_for_slug,
)


def test_igl_raw_slug_would_have_mapped_to_pharma():
    """Sanity check that this is a DATA problem, not a rule problem —
    the sector-mapping rules are doing exactly what they're supposed to
    with IGL's (wrong) raw slug."""
    assert _sector_for_slug("hospitalsmedicalservices") == "pharma"


def test_igl_override_corrects_to_energy():
    assert "IGL" in _INDUSTRY_SLUG_OVERRIDES
    corrected = _INDUSTRY_SLUG_OVERRIDES["IGL"]
    assert _sector_for_slug(corrected) == "energy"


def test_corrected_sql_expr_cases_on_known_overrides():
    sql = _corrected_industry_slug_sql()
    assert "c.nse_symbol" in sql
    for sym, slug in _INDUSTRY_SLUG_OVERRIDES.items():
        assert repr(sym) in sql
        assert repr(slug) in sql
    # Falls back to the raw column for every symbol not in the override map.
    assert "ELSE c.industry_slug END" in sql
