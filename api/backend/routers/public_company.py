"""Public, read-only company data for the pages search engines index.

`/stock/{symbol}` is a free page: anyone can read it without signing in, and
it is rendered on the server so a crawler sees the numbers, not a loading
spinner. These two routes are what that page renders from.

    GET /api/public/companies         every NSE company we cover, largest first
    GET /api/public/stock/{symbol}    one company's snapshot

Why a separate router rather than opening the existing ones:

* **Nothing here reaches a price feed.** Every number comes from a store or a
  cache that the Markets tab, the screener and the stock page already keep
  warm: the screener's universe and fundamentals maps, charto's end-of-day
  matrix, the financials payload, quarterly_metrics and the shareholding
  filings. A crawler walking three thousand pages must not turn into three
  thousand Kite calls; the authenticated `/api/markets/quote` would.
* **One price basis.** Prices, returns and the peer table all come from the
  same end-of-day session (`market_explore.compose` with the whole universe),
  so a page never ranks a live price beside a stale one.
* **No borrowed prose.** The enrichment profile's business summary is Yahoo's
  text; repeating it on thousands of public pages is duplicate content. The
  page writes its own summary from these numbers instead, so it is left out.

Every section is optional. A company with no shareholding filing still gets a
page with its price and financials; a missing section is `null`, never a guess.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from typing import Any, Callable, Optional

from fastapi import APIRouter, HTTPException

from backend.cache import redis_client

router = APIRouter(prefix="/api/public", tags=["Public"])
logger = logging.getLogger(__name__)

# Bump on any payload SHAPE change: the page reads these keys directly.
_PREFIX = "public:v1:"
_LIST_TTL = 30 * 60
_STOCK_TTL = 60 * 60
_SYMBOL = re.compile(r"^[A-Z0-9][A-Z0-9&_.\-]{0,24}$")

# What the directory, the sector pages and the peer table need, per company.
_LIST_FIELDS = (
    "symbol", "name", "sector", "mcap_cr", "price", "change",
    "ret_1w", "ret_1m", "ret_3m", "ret_1y", "pe", "roe",
    "dist_high", "dist_low", "rsi",
)

# The financial fields the page shows. The payload carries all ~40; a public
# page needs the headline ones and their units.
_LATEST_FIELDS = (
    "revenue", "net_profit", "operating_profit", "eps_basic", "roe", "roce",
    "roa", "debt_to_equity", "current_ratio", "interest_coverage",
    "net_profit_margin", "ebitda_margin", "operating_margin", "price_to_book",
    "ev_to_ebitda", "earnings_yield", "dividend_payout", "book_value_per_share",
    "total_debt", "total_equity", "cash_from_ops", "gross_npa_pct",
    "net_npa_pct", "net_interest_margin", "casa_pct",
)
_HISTORY_FIELDS = ("revenue", "operating_profit", "net_profit", "eps_basic",
                   "cash_from_ops", "total_debt", "total_equity")
_QUARTER_FIELDS = (
    "period_end", "period_label", "basis", "revenue", "ebitda", "net_profit",
    "eps_basic", "operating_margin_pct", "net_margin_pct", "revenue_yoy_pct",
    "net_profit_yoy_pct", "revenue_qoq_pct", "net_profit_qoq_pct", "rev_ttm",
    "np_ttm", "eps_ttm", "gross_npa_pct", "net_npa_pct",
)
_PEERS = 6

_list_lock = threading.Lock()
_list_memo: dict[str, Any] = {"at": 0.0, "body": None}


def _cache_get(key: str) -> Optional[dict]:
    try:
        raw = redis_client.get(_PREFIX + key)
        if raw:
            return json.loads(raw.decode() if isinstance(raw, (bytes, bytearray)) else raw)
    except Exception:  # noqa: BLE001 — the cache is an optimisation
        logger.debug("public cache read failed for %s", key, exc_info=True)
    return None


def _cache_set(key: str, body: dict, ttl: int) -> None:
    try:
        redis_client.set(_PREFIX + key, json.dumps(body), ex=ttl)
    except Exception:  # noqa: BLE001
        logger.debug("public cache write failed for %s", key, exc_info=True)


def _safe(label: str, fn: Callable[[], Any]) -> Any:
    """One section of the page. A failure leaves the section out; it never
    takes the whole page down."""
    try:
        return fn()
    except HTTPException:
        return None
    except Exception:  # noqa: BLE001
        logger.warning("public %s failed", label, exc_info=True)
        return None


def build_companies() -> dict:
    """The covered universe on one end-of-day basis, largest first."""
    from backend.routers import screener as sc
    from backend.services import market_explore as mx

    metrics, metrics_src = sc._market_metrics_cached()
    tech, tech_as_of, _src = sc._technical_features_cached()
    universe = mx.ranked_universe(sc._full_universe(), mx.reference(), metrics, tech)
    # The whole universe, not the top 500, so live coverage is always below
    # compose's threshold and every price is the same end-of-day session.
    composed = mx.compose(universe, sc._fundamentals_map_cached(), metrics,
                          metrics_src, tech, tech_as_of, {}, mx.eps_map(),
                          top_n=len(universe))
    rows = []
    for rank, s in enumerate(composed["stocks"], start=1):
        row = {k: s.get(k) for k in _LIST_FIELDS}
        row["rank"] = rank
        rows.append(row)
    used = {r["sector"] for r in rows}
    return {
        "as_of": composed["as_of"],
        "count": len(rows),
        "sectors": {k: v for k, v in mx.SECTORS.items() if k in used},
        "companies": rows,
    }


def companies() -> dict:
    """`build_companies`, memoised in-process for a minute and in Redis for
    half an hour. Empty is not cached: a cold screener cache must not pin an
    empty directory for thirty minutes."""
    now = time.time()
    if _list_memo["body"] is not None and now - _list_memo["at"] < 60:
        return _list_memo["body"]
    with _list_lock:
        if _list_memo["body"] is not None and now - _list_memo["at"] < 60:
            return _list_memo["body"]
        body = _cache_get("companies")
        if not body or not body.get("companies"):
            body = build_companies()
            if body.get("companies"):
                _cache_set("companies", body, _LIST_TTL)
        if body.get("companies"):
            _list_memo.update(at=now, body=body)
        return body


@router.get("/companies", summary="Every covered NSE company with its end-of-day numbers")
def get_companies() -> dict:
    body = companies()
    if not body.get("companies"):
        raise HTTPException(status_code=503, detail="company list is warming up")
    return body


def _peers(row: dict, rows: list[dict]) -> list[dict]:
    """The nearest companies by market cap in the same sector."""
    same = [r for r in rows if r["sector"] == row["sector"] and r["symbol"] != row["symbol"]]
    same.sort(key=lambda r: abs(r["rank"] - row["rank"]))
    picked = same[:_PEERS]
    picked.sort(key=lambda r: r["rank"])
    return picked


def _trim_financials(fin: Optional[dict]) -> dict:
    if not fin or not fin.get("available"):
        return {"latest": {}, "history": {}, "profile": None, "company": None}
    latest = {}
    for f in _LATEST_FIELDS:
        v = (fin.get("latest") or {}).get(f)
        if v and v.get("value") is not None:
            latest[f] = {k: v.get(k) for k in ("value", "period_label", "period_end", "unit", "basis", "source")}
    history = {}
    for f in _HISTORY_FIELDS:
        pts = [p for p in (fin.get("history") or {}).get(f) or [] if p.get("value") is not None]
        if pts:
            history[f] = [{k: p.get(k) for k in ("period_label", "period_end", "value", "unit")} for p in pts]
    prof = fin.get("profile") or {}
    comp = fin.get("company") or {}
    return {
        "latest": latest,
        "history": history,
        # The business summary is deliberately dropped (module docstring).
        "profile": {k: prof.get(k) for k in ("name", "sector", "industry", "website")} if prof else None,
        "company": {"name": comp.get("name"), "bse_code": comp.get("bse_code"),
                    "industry": comp.get("industry_slug")} if comp else None,
    }


def _trim_quarters(q: Optional[dict]) -> Optional[dict]:
    if not q or not q.get("quarters"):
        return None
    return {"basis": q.get("basis"),
            "quarters": [{k: r.get(k) for k in _QUARTER_FIELDS} for r in q["quarters"]]}


def _trim_shareholding(s: Optional[dict]) -> Optional[dict]:
    if not s or not s.get("available"):
        return None
    return {
        "quarter": s.get("quarter"),
        "promoter_pct": s.get("promoter_pct"),
        "pledge_pct": s.get("pledge_pct"),
        "groups": [{"label": g["label"], "pct": g["pct"]} for g in s.get("groups") or []],
        "quarters": (s.get("quarters") or [])[-8:],
    }


def build_stock(symbol: str) -> Optional[dict]:
    from backend.routers import financials as fin_router
    from backend.routers import stock_detail as sd

    listing = companies()
    rows = listing.get("companies") or []
    row = next((r for r in rows if r["symbol"] == symbol), None)
    fin = _safe("financials", lambda: fin_router.get_financials(
        symbol, authorization=fin_router._INTERNAL_TOOL_AUTH))
    if row is None and not (fin and fin.get("available")):
        return None

    def quarters() -> Optional[dict]:
        got = sd.get_quarters(symbol, basis="consolidated", limit=8,
                              authorization=sd._INTERNAL_TOOL_AUTH)
        if not got.get("quarters"):
            got = sd.get_quarters(symbol, basis="standalone", limit=8,
                                  authorization=sd._INTERNAL_TOOL_AUTH)
        return got

    trimmed = _trim_financials(fin)
    name = (row or {}).get("name") or (trimmed["profile"] or {}).get("name") \
        or (trimmed["company"] or {}).get("name") or symbol
    return {
        "symbol": symbol,
        "name": name,
        "legal_name": (trimmed["company"] or {}).get("name") or (trimmed["profile"] or {}).get("name"),
        "sector": (row or {}).get("sector"),
        "sector_label": listing.get("sectors", {}).get((row or {}).get("sector")),
        "industry": (trimmed["profile"] or {}).get("industry"),
        "website": (trimmed["profile"] or {}).get("website"),
        "bse_code": (trimmed["company"] or {}).get("bse_code"),
        "universe_size": listing.get("count"),
        "market": row,
        "as_of": listing.get("as_of"),
        "latest": trimmed["latest"],
        "history": trimmed["history"],
        "quarters": _trim_quarters(_safe("quarters", quarters)),
        "shareholding": _trim_shareholding(_safe("shareholding", lambda: sd.get_shareholding(
            symbol, authorization=sd._INTERNAL_TOOL_AUTH))),
        "peers": _peers(row, rows) if row else [],
        "sources": {
            "prices": "NSE end-of-day bars (Pivot's market data store)",
            "fundamentals": "Company filings via Moneycontrol",
            "shareholding": "Exchange shareholding-pattern filings",
        },
    }


@router.get("/stock/{symbol}", summary="One company's public snapshot")
def get_stock(symbol: str) -> dict:
    sym = (symbol or "").strip().upper()
    if not _SYMBOL.match(sym):
        raise HTTPException(status_code=404, detail="unknown symbol")
    cached = _cache_get(f"stock:{sym}")
    if cached:
        return cached
    body = build_stock(sym)
    if body is None:
        raise HTTPException(status_code=404, detail="unknown symbol")
    _cache_set(f"stock:{sym}", body, _STOCK_TTL)
    return body
