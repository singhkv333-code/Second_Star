"""HTTP surface for company autosuggest.

GET /api/companies/search?q=&limit=
  Fuzzy lookup over the Moneycontrol `mc.companies` universe — powers the
  global search bar and the chart "Compare to…" box on the FE. Read-only.
  Auth follows the same dev-mode auto-fallback as the financials router so
  the search works without a login flow in development.
"""
from __future__ import annotations

import json
import os
from typing import Optional
import urllib.parse
import urllib.request

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel

from backend.config import settings
from backend.auth.jwt_handler import get_user_id_from_token
from backend.market import financials_db as fdb


router = APIRouter(prefix="/api/companies", tags=["Companies"])


def _auth(authorization: Optional[str]) -> int:
    if not authorization:
        if getattr(settings, "app_env", "development") == "development":
            return 1
        raise HTTPException(status_code=401, detail="Missing token")
    uid = get_user_id_from_token(authorization.replace("Bearer ", ""))
    if not uid:
        raise HTTPException(status_code=401, detail="Invalid token")
    return uid


class CompanySearchResult(BaseModel):
    symbol: str
    name: str
    sector: Optional[str] = None
    has_fundamentals: bool = False
    # Company logo URL (img.logo.dev), or null → FE renders a monogram.
    # Pulled from the precomputed mc.companies.logo_url column in the same
    # search query (no extra round-trip on the autosuggest hot path).
    logo_url: Optional[str] = None
    # Search-row market context. All are optional because finding an instrument
    # must still work when the live quote relay is unavailable.
    exchange: Optional[str] = None
    instrument_type: str = "Equity"
    price: Optional[float] = None
    change_pct: Optional[float] = None
    currency: Optional[str] = None
    quote_source: Optional[str] = None


class CompanySearchResponse(BaseModel):
    results: list[CompanySearchResult]


class CompanyLogosResponse(BaseModel):
    # symbol (UPPER) → img.logo.dev URL, or null when none is known. Callers
    # render a first-letter monogram for null entries.
    logos: dict[str, Optional[str]]


# Cap the batch so a crafted query can't fan out into an unbounded number of
# logo lookups. A single screener/portfolio table never shows this many rows.
_MAX_LOGO_SYMBOLS = 200
_CHARTO_INTERNAL_URL = os.getenv("CHARTO_INTERNAL_URL", "http://127.0.0.1:5174")


def _instrument_context(
    symbol: str, sector: Optional[str], name: Optional[str] = None
) -> tuple[str, str]:
    """Return the display type and venue from the canonical search metadata."""
    label = (sector or "").strip()
    clean_name = (name or "").upper()
    if label.startswith("ETF") or " ETF" in f" {clean_name}":
        return "ETF", "NSE"
    if label.startswith("Commodity"):
        return "Commodity", "MCX"
    if label == "Index":
        return "Index", "BSE" if symbol == "SENSEX" else "NSE"
    return "Equity", "NSE"


def _search_quote_preview(
    rows: list[CompanySearchResult],
) -> dict[str, dict]:
    """Best-effort batch prices for autosuggest rows.

    Kite remains primary. Charto's stored/live quote plane fills gaps and is
    explicitly labelled so a delayed relay is never presented as live.
    Search itself never fails when either source is unavailable.
    """
    if not rows:
        return {}
    out: dict[str, dict] = {}

    try:
        from backend.kite.live_quote import get_kite_quotes, kite_session_available

        if kite_session_available():
            keys = [f"{row.exchange or 'NSE'}:{row.symbol}" for row in rows]
            quotes = get_kite_quotes(keys)
            for row, key in zip(rows, keys):
                quote = quotes.get(key) or {}
                last = quote.get("last_price")
                if not last:
                    continue
                ohlc = quote.get("ohlc") or {}
                prev = quote.get("prev_close") or ohlc.get("close")
                change_pct = None
                if prev and float(prev) > 0:
                    change_pct = round(
                        (float(last) - float(prev)) / float(prev) * 100, 2
                    )
                out[row.symbol] = {
                    "price": round(float(last), 2),
                    "change_pct": change_pct,
                    "currency": "INR",
                    "quote_source": "kite",
                }
    except Exception:  # noqa: BLE001 — quote decoration cannot break search
        pass

    missing = [row.symbol for row in rows if row.symbol not in out]
    if not missing:
        return out
    try:
        query = urllib.parse.urlencode({"symbols": ",".join(missing)})
        url = f"{_CHARTO_INTERNAL_URL.rstrip('/')}/quotes?{query}"
        with urllib.request.urlopen(url, timeout=0.8) as response:  # noqa: S310
            payload = json.loads(response.read())
        for quote in payload.get("quotes") or []:
            symbol = str(quote.get("symbol") or "").upper()
            if symbol and quote.get("last") is not None:
                out[symbol] = {
                    "price": float(quote["last"]),
                    "change_pct": quote.get("change_pct"),
                    "currency": quote.get("currency"),
                    "quote_source": "charto_relay",
                }
    except Exception:  # noqa: BLE001 — nulls are the honest fallback
        pass
    return out


@router.get("/search", response_model=CompanySearchResponse)
def search_companies(
    q: str = Query(..., min_length=1, max_length=100),
    limit: int = Query(10, ge=1, le=50),
    authorization: Optional[str] = Header(None),
) -> CompanySearchResponse:
    """Autosuggest across the FULL instrument palette: companies (by name or
    trading symbol) plus ETFs, MCX commodities and indices, so the search bar
    surfaces everything Pivot offers, not just equities."""
    _auth(authorization)
    hits = fdb.search_companies(q, limit=limit)

    # Non-equity instruments (ETF / commodity / index). Strong hits (exact or
    # prefix symbol, exact keyword like "gold etf") outrank the fuzzy company
    # tail; weak substring hits append after companies. `sector` carries the
    # type label so the FE dropdown needs no changes.
    from backend.market.instrument_search import search_instruments

    instruments = search_instruments(q, limit=limit)
    strong = [i for i in instruments if i["_score"] <= 2]
    weak = [i for i in instruments if i["_score"] > 2]

    # Resolve every row through the shared resolver, which keys off the
    # company's REAL website domain (NOT the precomputed mc.companies.logo_url
    # column — those were guessed from the name and frequently pointed at a
    # different company's domain, e.g. Britannia -> bi.com). Redis-cached and
    # fail-safe; the result set is small (<= limit) so the autosuggest hot
    # path stays cheap after warm-up. Null -> FE renders a clean monogram.
    def _logo(h: "fdb.CompanyHit") -> Optional[str]:
        try:
            from backend.market.company_logos import get_logo_url
            return get_logo_url(h.symbol)
        except Exception:
            return None

    company_rows = [
        CompanySearchResult(
            symbol=h.symbol,
            name=h.name,
            sector=h.sector,
            has_fundamentals=h.has_fundamentals,
            logo_url=_logo(h),
            exchange=_instrument_context(h.symbol, h.sector, h.name)[1],
            instrument_type=_instrument_context(h.symbol, h.sector, h.name)[0],
        )
        for h in hits
    ]
    instrument_rows = {
        tier: [
            CompanySearchResult(
                symbol=i["symbol"], name=i["name"], sector=i["sector"],
                has_fundamentals=False, logo_url=None,
                instrument_type=_instrument_context(i["symbol"], i["sector"])[0],
                exchange=_instrument_context(i["symbol"], i["sector"])[1],
            )
            for i in group
        ]
        for tier, group in (("strong", strong), ("weak", weak))
    }
    merged: list[CompanySearchResult] = []
    seen: set[str] = set()
    for row in instrument_rows["strong"] + company_rows + instrument_rows["weak"]:
        if row.symbol not in seen:
            seen.add(row.symbol)
            merged.append(row)
    merged = merged[:limit]
    previews = _search_quote_preview(merged)
    enriched = [
        row.model_copy(update=previews.get(row.symbol, {}))
        for row in merged
    ]
    return CompanySearchResponse(results=enriched)


@router.get("/logos", response_model=CompanyLogosResponse)
def company_logos(
    symbols: str = Query(
        ...,
        max_length=4000,
        description="Comma-separated symbols/tickers (e.g. RELIANCE,TCS,INFY)",
    ),
    authorization: Optional[str] = Header(None),
) -> CompanyLogosResponse:
    """Batch logo lookup for list/table surfaces (screener, portfolio).

    Reuses the shared, Redis-cached, fail-safe ``get_logo_url`` resolver so the
    screener and holdings tables get the same logos as the stock detail page in
    one round-trip. Unknown symbols map to ``null`` → FE monogram fallback.
    """
    _auth(authorization)
    from backend.market.company_logos import get_logo_url

    # De-dupe (preserving order) and cap the batch.
    seen: dict[str, None] = {}
    for raw in symbols.split(","):
        sym = raw.strip().upper()
        if sym and sym not in seen:
            seen[sym] = None
        if len(seen) >= _MAX_LOGO_SYMBOLS:
            break

    out: dict[str, Optional[str]] = {}
    for sym in seen:
        try:
            out[sym] = get_logo_url(sym)
        except Exception:  # noqa: BLE001 — never let one bad symbol 500 the batch
            out[sym] = None

    return CompanyLogosResponse(logos=out)
