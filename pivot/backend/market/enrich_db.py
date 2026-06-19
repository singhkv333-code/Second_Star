"""Read-only access layer for the yfinance-enriched `pivot_enrich` Postgres DB.

The DB is built by `scripts/enrich_company_profiles.py` from yfinance and is
logically distinct from both `pivot_db` and the Moneycontrol `financials` DB.
It holds one row per company (`enrich.company_profile`, keyed by the
Moneycontrol `sc_id`) and a curated read surface (`enrich.v_company_enriched`,
successful fetches only). It is joined back to the rest of the app by `sc_id`
or `ticker`.

Fields exposed: company profile (long_business_summary, website, employees,
location), sector division (sector/industry), and a promoter-holding **proxy**.

NOTE on promoter holding: yfinance has no true SEBI promoter field.
`promoter_holding_pct` is `heldPercentInsiders` (×100), the closest proxy. For
no-promoter widely-held names (e.g. HDFC Bank) it is correctly near-zero.

Design choices (mirrors backend/market/financials_db.py):
  - Never writes. The enrich DB is curated by the offline script.
  - Reads via `EnrichSessionLocal` so a slow query can't starve the
    operational `pivot_db` pool.
  - Raw SQL (text()); the `enrich.*` schema is owned by the script, not ORM.
  - Degrades gracefully: when ENRICH_DSN is unset (EnrichSessionLocal is
    None) every accessor returns None / [] instead of raising, so the chat
    and analysis paths keep working without enrichment.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Optional

from sqlalchemy import text

from backend.database import EnrichSessionLocal


@dataclass
class CompanyEnrichment:
    sc_id: str
    ticker: Optional[str]
    yf_symbol: Optional[str]
    company_name: Optional[str]
    long_name: Optional[str]
    sector: Optional[str]
    industry: Optional[str]
    long_business_summary: Optional[str]
    website: Optional[str]
    full_time_employees: Optional[int]
    city: Optional[str]
    state: Optional[str]
    country: Optional[str]
    market_cap: Optional[float]
    currency: Optional[str]
    promoter_holding_pct: Optional[float]      # proxy: yfinance heldPercentInsiders ×100
    institution_holding_pct: Optional[float]
    institutions_count: Optional[int]

    def as_dict(self) -> dict:
        return asdict(self)


_SELECT = """
    SELECT sc_id, ticker, yf_symbol, company_name, long_name,
           sector, industry, long_business_summary, website,
           full_time_employees, city, state, country, market_cap, currency,
           promoter_holding_pct, institution_holding_pct, institutions_count
    FROM enrich.v_company_enriched
"""


def _row_to_obj(row) -> CompanyEnrichment:
    m = row._mapping
    return CompanyEnrichment(
        sc_id=m["sc_id"],
        ticker=m["ticker"],
        yf_symbol=m["yf_symbol"],
        company_name=m["company_name"],
        long_name=m["long_name"],
        sector=m["sector"],
        industry=m["industry"],
        long_business_summary=m["long_business_summary"],
        website=m["website"],
        full_time_employees=m["full_time_employees"],
        city=m["city"],
        state=m["state"],
        country=m["country"],
        market_cap=float(m["market_cap"]) if m["market_cap"] is not None else None,
        currency=m["currency"],
        promoter_holding_pct=float(m["promoter_holding_pct"]) if m["promoter_holding_pct"] is not None else None,
        institution_holding_pct=float(m["institution_holding_pct"]) if m["institution_holding_pct"] is not None else None,
        institutions_count=m["institutions_count"],
    )


def is_enabled() -> bool:
    """True when the enrich DB is configured (ENRICH_DSN set)."""
    return EnrichSessionLocal is not None


def get_by_ticker(ticker: str) -> Optional[CompanyEnrichment]:
    """Enrichment for an NSE ticker (e.g. 'RELIANCE'). None if unknown/disabled.

    Match is case-insensitive on the bare ticker. If the source has duplicate
    sc_ids mapping to one ticker, the largest-market-cap row wins.
    """
    if EnrichSessionLocal is None or not ticker:
        return None
    db = EnrichSessionLocal()
    try:
        row = db.execute(
            text(_SELECT + " WHERE upper(ticker) = upper(:t) ORDER BY market_cap DESC NULLS LAST LIMIT 1"),
            {"t": ticker.strip()},
        ).first()
        return _row_to_obj(row) if row else None
    finally:
        db.close()


def get_by_sc_id(sc_id: str) -> Optional[CompanyEnrichment]:
    """Enrichment for a Moneycontrol sc_id. None if unknown/disabled."""
    if EnrichSessionLocal is None or not sc_id:
        return None
    db = EnrichSessionLocal()
    try:
        row = db.execute(
            text(_SELECT + " WHERE sc_id = :s LIMIT 1"), {"s": sc_id}
        ).first()
        return _row_to_obj(row) if row else None
    finally:
        db.close()
