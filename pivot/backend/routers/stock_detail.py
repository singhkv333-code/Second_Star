"""Deep sections of the stock detail page — the data added after the page was built.

The existing `/api/financials/{symbol}` router serves the top of the page
(overview, metrics, financial summary) and is untouched. This module serves
everything BELOW it, spread across three databases:

    pivot_db      quarterly_metrics      ~50 precomputed columns per quarter
    financials    filings.facts          annual-report facts, page-cited
    pivot_enrich  company_profile        summary, ownership
                  tijori_enrichment      revenue mix with a series per segment
                  company_documents      results / reports / concalls / decks

Two rules the whole module runs on.

**Coverage is a first-class response.** These assets range from 99% of the
universe down to 12%, so `/sections` reports what actually exists for a symbol
before the page renders anything. A tab that would be empty is never drawn —
an empty panel reads as a broken product, not as missing data.

**ISIN is the join key, not sc_id.** Measured 2026-08-07: joining
quarterly_metrics on ISIN reaches 3,798 companies, on `mc_sc_id` only 3,225.
`mc_sc_id` is a Moneycontrol alias — it both misses companies and, worse, can
resolve to a DIFFERENT company's numbers. So ISIN is tried first everywhere it
exists, and sc_id is a fallback rather than an equal alternative. Never `OR`
them together: that silently re-admits the wrong-company failure this ordering
exists to prevent.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
from typing import Any, Optional

from fastapi import APIRouter, Header, HTTPException, Query
from sqlalchemy import text

from backend.auth.jwt_handler import get_user_id_from_token
from backend.cache import redis_client
from backend.config import settings
from backend.database import EnrichSessionLocal, FinancialsSessionLocal, SessionLocal
from backend.market import financials_db as fdb

router = APIRouter(prefix="/api/stock", tags=["Stock detail"])
logger = logging.getLogger(__name__)

# Bump the version whenever a payload SHAPE changes — a cached v1 body served
# to v2 client code is a KeyError, not a stale number.
_CACHE_PREFIX = "stockdetail:v3:"
_CACHE_TTL = 6 * 3600          # fundamentals move quarterly; 6h is generous
_MAX_QUARTERS = 40
_MAX_FACTS = 1200
_MAX_DOCS = 120

_PEER_FIELDS: dict[str, dict[str, str]] = {
    "market_cap": {"label": "Market cap", "unit": "inr"},
    "revenue": {"label": "Revenue", "unit": "crore"},
    "net_profit": {"label": "Net profit", "unit": "crore"},
    "roe": {"label": "ROE", "unit": "percent"},
    "roce": {"label": "ROCE", "unit": "percent"},
    "net_profit_margin": {"label": "Net margin", "unit": "percent"},
    "debt_to_equity": {"label": "Debt / equity", "unit": "multiple"},
    "price_to_book": {"label": "Price / book", "unit": "multiple"},
    "ev_to_ebitda": {"label": "EV / EBITDA", "unit": "multiple"},
    "current_ratio": {"label": "Current ratio", "unit": "multiple"},
    "interest_coverage": {"label": "Interest coverage", "unit": "multiple"},
    "dividend_payout": {"label": "Dividend payout", "unit": "percent"},
    "eps_basic": {"label": "EPS", "unit": "rupee"},
    "book_value_per_share": {"label": "Book value / share", "unit": "rupee"},
    "gross_npa_pct": {"label": "Gross NPA", "unit": "percent"},
    "net_npa_pct": {"label": "Net NPA", "unit": "percent"},
    "net_interest_margin": {"label": "NIM", "unit": "percent"},
}
_PEER_DEFAULT_FIELDS = ("market_cap", "roe", "net_profit_margin", "debt_to_equity", "price_to_book")


# ── peer price, returns and technicals ──────────────────────────────────────
# The peer table answers three questions — how big/profitable (fundamentals,
# above), how it has TRADED (returns), and where it sits technically. Only the
# first came from the filings database; the other two are price facts, so they
# are computed here from the same one-year daily history the technicals panel
# already uses rather than invented or left blank.
#
# Everything below is arithmetic on closes. Nothing is estimated: a window with
# too few bars returns None for that window and the cell prints an em-dash.

def _pct(a: float, b: float) -> Optional[float]:
    """b → a as a percentage move, guarding the zero/absent denominators that
    a thin or newly listed series produces."""
    if not a or not b or b <= 0:
        return None
    return (a - b) / b * 100.0


def _rsi14(closes: list[float]) -> Optional[float]:
    """Wilder's RSI, the smoothing every charting package means by "RSI(14)".

    A simple mean of gains and losses is a DIFFERENT indicator that happens to
    share the name — it reacts faster and prints materially different numbers,
    which is how a peer table ends up disagreeing with the chart beside it.
    """
    if len(closes) < 15:
        return None
    gains = losses = 0.0
    for i in range(1, 15):                     # seed on the first 14 changes
        d = closes[i] - closes[i - 1]
        gains += max(d, 0.0)
        losses += max(-d, 0.0)
    avg_g, avg_l = gains / 14.0, losses / 14.0
    for i in range(15, len(closes)):           # then Wilder-smooth the rest
        d = closes[i] - closes[i - 1]
        avg_g = (avg_g * 13 + max(d, 0.0)) / 14.0
        avg_l = (avg_l * 13 + max(-d, 0.0)) / 14.0
    if avg_l == 0:
        return 100.0 if avg_g > 0 else 50.0
    rs = avg_g / avg_l
    return 100.0 - (100.0 / (1.0 + rs))


def _sma(closes: list[float], n: int) -> Optional[float]:
    return sum(closes[-n:]) / n if len(closes) >= n else None


def _peer_price_block(symbol: str) -> dict:
    """Price, trailing returns and technicals for one peer. Never raises."""
    empty: dict[str, Optional[float]] = {
        "price": None, "change_pct": None,
        "ret_1m": None, "ret_3m": None, "ret_6m": None, "ret_1y": None,
        "rsi14": None, "vs_50dma": None, "vs_200dma": None, "from_52w_high": None,
    }
    try:
        from backend.market.yfinance_service import fetch_price_history
        bars = fetch_price_history(symbol, "1y", "1d")
    except Exception:  # noqa: BLE001 — a dead price feed must not 500 the table
        logger.debug("peer price history failed for %s", symbol, exc_info=True)
        return empty
    closes = [float(b["close"]) for b in bars
              if b.get("close") not in (None, "") and float(b["close"]) > 0]
    if len(closes) < 2:
        return empty

    last = closes[-1]
    # ~21 trading days a month. Indexing back by trading days rather than by
    # calendar date is what keeps a holiday-heavy month from silently becoming
    # a five-week window.
    def back(n: int) -> Optional[float]:
        if len(closes) > n:
            return _pct(last, closes[-(n + 1)])
        # A "1y" request returns ~250 sessions and the 1y window wants 251, so
        # the exact-index version printed nothing for the one column readers
        # look at first. Where the series covers nearly the whole window, its
        # oldest close IS the window's open — but only nearly: a stock listed
        # three months ago must not report a one-year return from its IPO.
        if len(closes) >= int(n * 0.92):
            return _pct(last, closes[0])
        return None

    high52 = max(closes)
    s50, s200 = _sma(closes, 50), _sma(closes, 200)
    return {
        "price": round(last, 2),
        "change_pct": round(_pct(last, closes[-2]) or 0.0, 2) if len(closes) >= 2 else None,
        "ret_1m": _round(back(21)),
        "ret_3m": _round(back(63)),
        "ret_6m": _round(back(126)),
        "ret_1y": _round(back(250)),
        "rsi14": _round(_rsi14(closes)),
        "vs_50dma": _round(_pct(last, s50) if s50 else None),
        "vs_200dma": _round(_pct(last, s200) if s200 else None),
        "from_52w_high": _round(_pct(last, high52)),
    }


def _round(v: Optional[float]) -> Optional[float]:
    return None if v is None else round(v, 2)


def _peer_price_cached(symbol: str) -> dict:
    """`_peer_price_block` behind the cache, with one rule the generic
    `_cached` cannot have: a FAILURE IS NOT CACHED.

    The generic helper stores whatever build() returned, which is right for a
    query against our own database — that either works or raises. This block
    reaches an external price feed and degrades to a dict of nulls instead of
    raising, so caching the result blindly pins a transient outage into every
    response for the next six hours. (Measured: one call landed mid-reload,
    caught the partially-initialised import, and every peer read "—" long
    after the feed was healthy again.)
    """
    key = _CACHE_PREFIX + f"peerprice:{symbol}"
    try:
        hit = redis_client.get(key)
        if hit:
            return json.loads(hit)
    except Exception:  # noqa: BLE001
        logger.debug("peer price cache read failed for %s", symbol, exc_info=True)
    block = _peer_price_block(symbol)
    if block.get("price") is None:
        return block          # nothing worth keeping — try again next request
    try:
        redis_client.setex(key, _CACHE_TTL, json.dumps(block))
    except Exception:  # noqa: BLE001
        logger.debug("peer price cache write failed for %s", symbol, exc_info=True)
    return block


def _auth(authorization: Optional[str]) -> int:
    """Same dev-mode fallback as the financials router, so the page works
    without a login in development."""
    if not authorization:
        if getattr(settings, "app_env", "development") == "development":
            return 1
        raise HTTPException(status_code=401, detail="Missing token")
    uid = get_user_id_from_token(authorization.replace("Bearer ", ""))
    if not uid:
        raise HTTPException(status_code=401, detail="Invalid token")
    return uid


def _cached(key: str, build):
    """Read-through cache. A cache miss or a dead Redis both just build."""
    full = _CACHE_PREFIX + key
    try:
        hit = redis_client.get(full)
        if hit:
            return json.loads(hit)
    except Exception:  # noqa: BLE001 — cache is a convenience, never a contract
        logger.debug("stock_detail cache read failed for %s", key, exc_info=True)
    val = build()
    try:
        redis_client.setex(full, _CACHE_TTL, json.dumps(val, default=str))
    except Exception:  # noqa: BLE001
        logger.debug("stock_detail cache write failed for %s", key, exc_info=True)
    return val


# ── identity ────────────────────────────────────────────────────────────────

def _resolve(symbol: str) -> dict:
    """symbol → {isin, sc_id, name, bse_scripcode}.

    `verified_symbol` is NOT unique: RELIANCE returns four rows (the operating
    company, two unrelated Reliance entities and a rights entitlement) sharing
    one ISIN. `mc_is_primary` cuts that to one. Three symbols in the universe
    still tie after that filter, so the order-by makes the winner deterministic
    rather than whatever the planner returned first — the page must not show a
    different company on a refresh.
    """
    sym = (symbol or "").upper().strip()
    if not sym:
        raise HTTPException(status_code=400, detail="symbol required")

    with SessionLocal() as db:
        row = db.execute(text("""
            SELECT isin, mc_sc_id, verified_name, verified_bse_code
              FROM company_identity
             WHERE verified_symbol = :s AND mc_is_primary
          ORDER BY mc_metric_count DESC NULLS LAST, mc_sc_id
             LIMIT 1"""), {"s": sym}).first()
        if row is None:
            # Not every listed symbol is mapped as primary; fall back rather
            # than 404 a company we can still partly serve.
            row = db.execute(text("""
                SELECT isin, mc_sc_id, verified_name, verified_bse_code
                  FROM company_identity
                 WHERE verified_symbol = :s
              ORDER BY mc_metric_count DESC NULLS LAST, mc_sc_id
                 LIMIT 1"""), {"s": sym}).first()
    if row is None:
        raise HTTPException(status_code=404, detail=f"unknown symbol {sym}")

    isin, sc_id, name, bse = row
    # company_documents keys on the BSE scripcode, which company_identity
    # mostly does not carry — bse_map is the bridge, and it keys on ISIN.
    if not bse and isin and EnrichSessionLocal is not None:
        with EnrichSessionLocal() as db:
            got = db.execute(text(
                "SELECT bse_scripcode FROM enrich.bse_map WHERE isin = :i LIMIT 1"),
                {"i": isin}).first()
            bse = got[0] if got else None

    return {"symbol": sym, "isin": isin, "sc_id": sc_id,
            "name": name, "bse_scripcode": str(bse) if bse else None}


@router.get("/{symbol}/peers")
def get_peers(
    symbol: str,
    fields: str = Query("", description="comma-separated supported metric ids"),
    # Six, not eight. The table is read by comparing rows against each other,
    # and past about half a dozen names the reader stops comparing and starts
    # scrolling. Ranked by market cap, so the six are the six that matter.
    limit: int = Query(6, ge=4, le=12),
    authorization: Optional[str] = Header(None),
) -> dict:
    """Prominent same-sector companies with comparable reported metrics.

    Sector, display name and market cap come from the enrichment database;
    statement/ratio values come from Moneycontrol's financials database. The
    two datasets are joined only by ``sc_id``. Missing values remain null.
    """
    _auth(authorization)
    who = _resolve(symbol)
    requested = [f.strip() for f in fields.split(",") if f.strip()]
    chosen = requested or list(_PEER_DEFAULT_FIELDS)
    unknown = [f for f in chosen if f not in _PEER_FIELDS]
    if unknown:
        raise HTTPException(status_code=400, detail=f"unsupported peer fields: {', '.join(unknown)}")
    chosen = list(dict.fromkeys(chosen))[:8]
    if EnrichSessionLocal is None:
        return {"symbol": who["symbol"], "available": False, "sector": None,
                "fields": [], "catalog": _PEER_FIELDS, "peers": []}

    with EnrichSessionLocal() as db:
        target = db.execute(text("""
            SELECT sc_id, sector FROM enrich.v_company_enriched
             WHERE upper(ticker) = :s
          ORDER BY market_cap DESC NULLS LAST LIMIT 1"""), {"s": who["symbol"]}).first()
        if not target or not target[1]:
            return {"symbol": who["symbol"], "available": False, "sector": None,
                    "fields": [], "catalog": _PEER_FIELDS, "peers": []}
        sector = target[1]
        rows = db.execute(text("""
            SELECT DISTINCT ON (upper(ticker)) sc_id, upper(ticker) symbol,
                   coalesce(long_name, company_name, ticker) name, market_cap
              FROM enrich.v_company_enriched
             WHERE sector = :sector AND ticker IS NOT NULL
          ORDER BY upper(ticker), market_cap DESC NULLS LAST"""), {"sector": sector}).mappings().all()
        ranked = sorted(rows, key=lambda r: float(r["market_cap"] or 0), reverse=True)
        selected = ranked[:limit]
        if not any(r["symbol"] == who["symbol"] for r in selected):
            own = next((r for r in ranked if r["symbol"] == who["symbol"]), None)
            if own:
                selected = selected[:-1] + [own]

    metric_fields = [f for f in chosen if f != "market_cap"]
    peers = []
    with FinancialsSessionLocal() as db:
        for company in selected:
            # The enrich database can retain an older/shell sc_id for a valid
            # ticker (RELIANCE is RIL08 there while the canonical filings row
            # resolves elsewhere). Resolve the trading symbol inside the
            # financials database before reading statements; never assume the
            # cross-database sc_id is canonical.
            financials_sc_id = fdb.resolve_symbol(company["symbol"], session=db)
            latest, _ = fdb.get_company_fundamentals_bulk(
                financials_sc_id, fields=metric_fields, session=db,
            ) if financials_sc_id else ({field: None for field in metric_fields}, {})
            # Some legacy tickers resolve to a newer canonical row that is
            # still statement-empty while the enrich-linked legacy row holds
            # the filings (OIL is one example). Compare coverage and retain
            # the better sourced row rather than blindly preferring either DB.
            if financials_sc_id != company["sc_id"]:
                enrich_latest, _ = fdb.get_company_fundamentals_bulk(
                    company["sc_id"], fields=metric_fields, session=db,
                )
                canonical_fill = sum(v is not None and v.value_numeric is not None for v in latest.values())
                enrich_fill = sum(v is not None and v.value_numeric is not None for v in enrich_latest.values())
                if enrich_fill > canonical_fill:
                    financials_sc_id, latest = company["sc_id"], enrich_latest
            values: dict[str, Optional[float]] = {
                "market_cap": float(company["market_cap"]) if company["market_cap"] is not None else None,
            }
            periods: dict[str, Optional[str]] = {}
            for field in metric_fields:
                got = latest.get(field)
                values[field] = float(got.value_numeric) if got and got.value_numeric is not None else None
                periods[field] = got.period_label if got else None
            peers.append({"sc_id": financials_sc_id or company["sc_id"], "symbol": company["symbol"],
                          "name": company["name"], "is_current": company["symbol"] == who["symbol"],
                          "values": values, "periods": periods})

    # Price facts last, and cached with the rest of the body: this is the only
    # part of the payload that reaches a price feed, and it does so once per
    # peer. Six symbols of one-year daily history is seconds on a cold call and
    # nothing at all for the next six hours.
    for peer in peers:
        peer["price"] = _peer_price_cached(peer["symbol"])

    return {"symbol": who["symbol"], "available": bool(peers), "sector": sector,
            "fields": [{"id": f, **_PEER_FIELDS[f]} for f in chosen],
            "catalog": [{"id": k, **v} for k, v in _PEER_FIELDS.items()],
            "peers": peers, "source": "Moneycontrol filings + company profile database"}


# ── coverage ────────────────────────────────────────────────────────────────

@router.get("/{symbol}/sections")
def get_sections(symbol: str, authorization: Optional[str] = Header(None)) -> dict:
    """What this company actually has, so the page can decide what to render.

    Every count here is cheap (indexed EXISTS/COUNT), and it is the one call
    the page makes before painting. Sections whose count is 0 are not drawn.
    """
    _auth(authorization)
    who = _resolve(symbol)

    def build() -> dict:
        cov: dict[str, Any] = {}

        with SessionLocal() as db:
            q = db.execute(text("""
                SELECT count(*) n, max(period_end)::text latest, count(DISTINCT basis) bases
                  FROM quarterly_metrics WHERE isin = :i"""), {"i": who["isin"]}).first()
            n, latest, bases = (q or (0, None, 0))
            if not n and who["sc_id"]:
                q = db.execute(text("""
                    SELECT count(*) n, max(period_end)::text latest, count(DISTINCT basis) bases
                      FROM quarterly_metrics WHERE sc_id = :s"""), {"s": who["sc_id"]}).first()
                n, latest, bases = (q or (0, None, 0))
            cov["quarters"] = {"count": int(n or 0), "latest": latest,
                               "bases": int(bases or 0)}

        with FinancialsSessionLocal() as db:
            f = db.execute(text("""
                SELECT count(*) n, count(DISTINCT task) tasks
                  FROM filings.facts WHERE symbol = :s AND status = 'reported'"""),
                {"s": who["symbol"]}).first()
            d = db.execute(text("""
                SELECT count(*) n, max(period) p
                  FROM filings.documents
                 WHERE symbol = :s AND state = 'done'"""), {"s": who["symbol"]}).first()
            cov["annual_report"] = {"count": int((f or (0, 0))[0] or 0),
                                    "tasks": int((f or (0, 0))[1] or 0),
                                    "documents": int((d or (0, None))[0] or 0),
                                    "latest_period": (d or (0, None))[1]}

        cov["revenue_mix"] = {"count": 0}
        cov["ownership"] = {"count": 0}
        cov["documents"] = {"count": 0}
        if EnrichSessionLocal is not None:
            with EnrichSessionLocal() as db:
                if who["sc_id"]:
                    t = db.execute(text("""
                        SELECT count(*) FILTER (WHERE has_revenue_mix) mix,
                               count(*) FILTER (WHERE has_market_share) share
                          FROM enrich.tijori_enrichment WHERE sc_id = :s"""),
                        {"s": who["sc_id"]}).first() or (0, 0)
                    cov["revenue_mix"] = {"count": int(t[0] or 0),
                                          "market_share": int(t[1] or 0)}
                    p = db.execute(text("""
                        SELECT count(held_percent_institutions)
                          FROM enrich.company_profile WHERE sc_id = :s"""),
                        {"s": who["sc_id"]}).first() or (0,)
                    cov["ownership"] = {"count": int(p[0] or 0)}
                if who["bse_scripcode"]:
                    c = db.execute(text("""
                        SELECT count(*) FROM enrich.company_documents
                         WHERE bse_scripcode = :b"""),
                        {"b": who["bse_scripcode"]}).first() or (0,)
                    cov["documents"] = {"count": int(c[0] or 0)}

        return {**who, "coverage": cov}

    return _cached(f"sections:{who['symbol']}", build)


# ── quarters ────────────────────────────────────────────────────────────────

_Q_COLS = (
    "period_end", "period_label", "basis", "revenue", "total_income", "other_income",
    "ebitda", "ebit", "depreciation", "interest", "employee_cost", "raw_material",
    "other_expenses", "provisions", "exceptional", "pbt", "tax", "net_profit",
    "eps_basic", "eps_diluted", "operating_margin_pct", "ebitda_margin_pct",
    "net_margin_pct", "pbt_margin_pct", "tax_rate_pct", "interest_coverage",
    "revenue_yoy_pct", "net_profit_yoy_pct", "ebitda_yoy_pct", "revenue_qoq_pct",
    "net_profit_qoq_pct", "operating_margin_yoy_bps", "net_margin_yoy_bps",
    "rev_ttm", "np_ttm", "eps_ttm", "rev_ttm_yoy_pct", "np_ttm_yoy_pct",
    "gross_npa_pct", "net_npa_pct", "roa_pct",
)


@router.get("/{symbol}/quarters")
def get_quarters(
    symbol: str,
    basis: str = Query("consolidated"),
    limit: int = Query(20, ge=1, le=_MAX_QUARTERS),
    authorization: Optional[str] = Header(None),
) -> dict:
    """Recent quarters, newest first.

    Nothing is computed here. `quarterly_metrics` already holds margins, YoY,
    QoQ and TTM as columns — recomputing them in the API would give the page a
    second, silently different set of numbers from the one the rest of the
    system reads.
    """
    _auth(authorization)
    who = _resolve(symbol)

    def build() -> dict:
        cols = ", ".join(_Q_COLS)
        with SessionLocal() as db:
            # ISIN first, sc_id only if ISIN finds nothing — never OR'd; see
            # the module docstring.
            rows = db.execute(text(f"""
                SELECT {cols} FROM quarterly_metrics
                 WHERE isin = :i AND basis = :b
              ORDER BY period_end DESC LIMIT :n"""),
                {"i": who["isin"], "b": basis, "n": limit}).mappings().all()
            used = "isin"
            if not rows and who["sc_id"]:
                rows = db.execute(text(f"""
                    SELECT {cols} FROM quarterly_metrics
                     WHERE sc_id = :s AND basis = :b
                  ORDER BY period_end DESC LIMIT :n"""),
                    {"s": who["sc_id"], "b": basis, "n": limit}).mappings().all()
                used = "sc_id"
            avail = db.execute(text("""
                SELECT DISTINCT basis FROM quarterly_metrics WHERE isin = :i"""),
                {"i": who["isin"]}).scalars().all()
        return {"symbol": who["symbol"], "basis": basis, "matched_on": used,
                "bases_available": sorted(b for b in avail if b),
                "quarters": [dict(r) for r in rows]}

    return _cached(f"q:{who['symbol']}:{basis}:{limit}", build)


# ── annual-report facts ─────────────────────────────────────────────────────

# Display order and human names for the extraction tasks. Ordered by how much
# a reader gets out of them, not by how many rows each produces.
_TASK_LABELS: dict[str, str] = {
    "segments": "Segments",
    "geography": "Geography",
    "special_metrics": "Operating metrics",
    "contingent": "Contingent liabilities",
    "related_party": "Related-party transactions",
    "audit": "Key audit matters",
    "strategy": "Strategy & outlook",
    "cost_structure": "Cost structure",
    "debt_terms": "Debt terms",
    "schedule3_ratios": "Schedule III ratios",
    "receivables_ageing": "Receivables ageing",
    "cwip_ageing": "CWIP ageing",
    "workforce": "Workforce",
    "regulatory_flags": "Regulatory flags",
    "forex_earned_outgo": "Forex earned / outgo",
    "credit_rating": "Credit rating",
}


@router.get("/{symbol}/annual-report")
def get_annual_report(symbol: str, authorization: Optional[str] = Header(None)) -> dict:
    """Facts extracted from the annual report, grouped by task then group.

    Every fact keeps its `page`, `quote` and `grounding` — that provenance is
    the entire point of the section, so nothing here is aggregated away. Facts
    whose unit could not be confirmed (`unit_agrees = false`) are returned WITH
    that flag rather than dropped: the page shows the doubt.
    """
    _auth(authorization)
    who = _resolve(symbol)

    def build() -> dict:
        with FinancialsSessionLocal() as db:
            docs = db.execute(text("""
                SELECT sha256, title, period, filed_at, url, pages
                  FROM filings.documents
                 WHERE symbol = :s AND state = 'done'
              ORDER BY period DESC NULLS LAST"""),
                {"s": who["symbol"]}).mappings().all()
            facts = db.execute(text("""
                SELECT task, grp, label, value_text, unit_text, value_crore,
                       period, basis, page, quote, grounding, unit_agrees,
                       rollup, note, doc_sha
                  FROM filings.facts
                 WHERE symbol = :s AND status = 'reported'
              ORDER BY task, grp, period DESC NULLS LAST, label
                 LIMIT :n"""), {"s": who["symbol"], "n": _MAX_FACTS}).mappings().all()

        groups: dict[str, dict[str, list]] = {}
        for f in facts:
            groups.setdefault(f["task"], {}).setdefault(f["grp"] or "—", []).append(dict(f))

        tasks = [
            {"task": t, "label": _TASK_LABELS.get(t, t.replace("_", " ").title()),
             "count": sum(len(v) for v in groups[t].values()),
             "groups": [{"grp": g, "facts": v} for g, v in groups[t].items()]}
            for t in sorted(groups, key=lambda x: list(_TASK_LABELS).index(x)
                            if x in _TASK_LABELS else 99)
        ]
        return {"symbol": who["symbol"],
                "documents": [dict(d) for d in docs],
                "tasks": tasks,
                "truncated": len(facts) >= _MAX_FACTS}

    return _cached(f"ar:{who['symbol']}", build)


# ── revenue mix ─────────────────────────────────────────────────────────────

@router.get("/{symbol}/mix")
def get_mix(symbol: str, authorization: Optional[str] = Header(None)) -> dict:
    """Segment splits — a current snapshot AND a series per segment.

    `revenue_mix` is a LIST of breakdowns, not one. Reliance carries seven:
    product-wise, location-wise, operating-profit-wise, capex, assets, plus
    two nested inside Organized Retail. Reading only the first — which is the
    obvious mistake — throws away most of what makes this section worth
    building, so every block is returned and the page offers the choice.

    Two key names to know, both non-obvious: the block's title is `breakdown`,
    and a segment's name is `fieldname`.
    """
    _auth(authorization)
    who = _resolve(symbol)
    if EnrichSessionLocal is None or not who["sc_id"]:
        return {"symbol": who["symbol"], "available": False, "charts": []}

    def build() -> dict:
        with EnrichSessionLocal() as db:
            row = db.execute(text("""
                SELECT revenue_mix, market_share, tijori_name
                  FROM enrich.tijori_enrichment
                 WHERE sc_id = :s AND has_revenue_mix LIMIT 1"""),
                {"s": who["sc_id"]}).mappings().first()
        if not row:
            return {"symbol": who["symbol"], "available": False, "charts": []}

        charts = []
        for block in (row["revenue_mix"] or []):
            if not isinstance(block, dict):
                continue
            # `> 0` is not the right floor. The source carries values like
            # 1.42e-14 — floating-point residue from a share that is actually
            # zero — which passes a bare positivity test and then renders as
            # "Others 0.0%", a segment that does not exist. A tenth of a
            # percent is below anything a reader can act on anyway.
            current = [{"name": n, "pct": round(float(p), 2)}
                       for n, p in (block.get("current") or [])
                       if p is not None and float(p) >= 0.05]
            series = []
            for seg in (block.get("segments") or []):
                pts = [{"t": int(t), "pct": round(float(v), 2)}
                       for t, v in (seg.get("series") or []) if v is not None]
                # A band that is zero at every point is an empty legend entry
                # and an invisible layer — drop the series, not just its label.
                if pts and any(p["pct"] >= 0.05 for p in pts):
                    series.append({"name": seg.get("fieldname") or "—", "points": pts})
            if current or series:
                charts.append({
                    "id": block.get("chart_id"),
                    "title": block.get("breakdown") or "Revenue mix",
                    "current": current, "series": series})

        shares = [{"name": m.get("name"),
                   "points": [{"t": int(t), "pct": float(v)}
                              for t, v in (m.get("series") or []) if v is not None]}
                  for m in (row["market_share"] or []) if isinstance(m, dict)]
        return {"symbol": who["symbol"], "available": bool(charts),
                "source_name": row["tijori_name"], "charts": charts,
                "market_share": [s for s in shares if s["points"]]}

    return _cached(f"mix:{who['symbol']}", build)


# ── ownership & profile ─────────────────────────────────────────────────────

@router.get("/{symbol}/ownership")
def get_ownership(symbol: str, authorization: Optional[str] = Header(None)) -> dict:
    _auth(authorization)
    who = _resolve(symbol)
    if EnrichSessionLocal is None or not who["sc_id"]:
        return {"symbol": who["symbol"], "available": False}

    def build() -> dict:
        with EnrichSessionLocal() as db:
            row = db.execute(text("""
                SELECT long_business_summary, website, full_time_employees,
                       held_percent_institutions, held_percent_insiders,
                       institutions_count, institutions_float_percent,
                       sector, industry, city, state, country, exchange
                  FROM enrich.company_profile WHERE sc_id = :s LIMIT 1"""),
                {"s": who["sc_id"]}).mappings().first()
        if not row:
            return {"symbol": who["symbol"], "available": False}
        d = {k: (float(v) if hasattr(v, "quantize") else v) for k, v in dict(row).items()}
        return {"symbol": who["symbol"], "available": True, **d}

    return _cached(f"own:{who['symbol']}", build)


# ── documents ───────────────────────────────────────────────────────────────

@router.get("/{symbol}/documents")
def get_documents(
    symbol: str,
    doc_type: str = Query("", description="filter to one type"),
    limit: int = Query(60, ge=1, le=_MAX_DOCS),
    authorization: Optional[str] = Header(None),
) -> dict:
    _auth(authorization)
    who = _resolve(symbol)
    # `doc_type` is a str under FastAPI but arrives as the Query default when
    # this function is called directly (tests, scripts) — normalise once so a
    # truthiness check can't smuggle a Query object into the SQL parameters.
    doc_type = doc_type if isinstance(doc_type, str) else ""
    if EnrichSessionLocal is None or not who["bse_scripcode"]:
        return {"symbol": who["symbol"], "available": False, "documents": [], "types": []}

    def build() -> dict:
        with EnrichSessionLocal() as db:
            types = db.execute(text("""
                SELECT doc_type, count(*) n FROM enrich.company_documents
                 WHERE bse_scripcode = :b GROUP BY 1 ORDER BY 2 DESC"""),
                {"b": who["bse_scripcode"]}).mappings().all()
            clause = "AND doc_type = :t" if doc_type else ""
            args: dict[str, Any] = {"b": who["bse_scripcode"], "n": limit}
            if doc_type:
                args["t"] = doc_type
            rows = db.execute(text(f"""
                SELECT doc_type, category, subcategory, title, doc_date,
                       fin_year, quarter, url, attach_size
                  FROM enrich.company_documents
                 WHERE bse_scripcode = :b {clause}
              ORDER BY doc_date DESC NULLS LAST LIMIT :n"""), args).mappings().all()
        return {"symbol": who["symbol"], "available": bool(rows),
                "types": [dict(t) for t in types],
                "documents": [dict(r) for r in rows]}

    return _cached(f"docs:{who['symbol']}:{doc_type}:{limit}", build)


# ── shareholding ────────────────────────────────────────────────────────────
#
# The XBRL shareholding store (`shp.*`) is filed quarterly and arrives as a
# TREE, not a flat list: `InstitutionsForeignMember` is the parent of the two
# FPI categories, `NonInstitutionsMember` the parent of the two retail slabs.
# Summing every row therefore double-counts to ~200%.
#
# So the categories are split into a non-overlapping TOP level, which is what
# the stacked chart and the totals use, and a CHILD level per parent, which is
# what the breakdown lists. A category we do not recognise is dropped rather
# than bucketed into "other": a mis-parented row is worse than a missing one,
# because it silently moves a percentage the reader is trying to trust.

_SHP_TOP: dict[str, str] = {
    "ShareholdingOfPromoterAndPromoterGroupMember": "Promoters",
    "InstitutionsForeignMember": "Foreign institutions",
    "InstitutionsDomesticMember": "Domestic institutions",
    "NonInstitutionsMember": "Non-institutions",
    "SharesHeldByNonPromoterNonPublicShareholdersMember": "Non-promoter non-public",
}

# Parent → {member: label}. The source spells "Category" two ways (a genuine
# typo that reached the taxonomy, "Catergory"), and mutual funds two ways, so
# both spellings map to one label rather than showing up as two slices.
_SHP_CHILD: dict[str, dict[str, str]] = {
    "Foreign institutions": {
        "InstitutionsForeignPortfolioInvestorCategoryOneMember": "FPI category I",
        "InstitutionsForeignPortfolioInvestorCatergoryOneMember": "FPI category I",
        "InstitutionsForeignPortfolioInvestorCategoryTwoMember": "FPI category II",
        "InstitutionsForeignPortfolioInvestorCatergoryTwoMember": "FPI category II",
        "ForeignCompaniesMember": "Foreign companies",
        "OtherInstitutionsForeignMember": "Other foreign",
    },
    "Domestic institutions": {
        "MutualFundsOrUTIMember": "Mutual funds",
        "MutualFundsOrUtiMember": "Mutual funds",
        "InsuranceCompaniesMember": "Insurance",
        "ProvidentFundsOrPensionFundsMember": "Pension funds",
        "BanksMember": "Banks",
        "AlternativeInvestmentFundsMember": "AIFs",
        "NBFCsRegisteredWithRBIMember": "NBFCs",
        "OtherFinancialInstitutionsMember": "Other financial",
    },
    "Non-institutions": {
        "ResidentIndividualShareholdersHoldingNominalShareCapitalUpToRsTwoLakhMember": "Retail up to ₹2 lakh",
        "ResidentIndividualShareholdersHoldingNominalShareCapitalInExcessOfRsTwoLakhMember": "Retail above ₹2 lakh",
        "BodiesCorporateMember": "Bodies corporate",
        "NonResidentIndiansMember": "NRIs",
        "OtherNonInstitutionsMember": "Other non-institutions",
    },
}

_SHP_TOP_ORDER = ("Promoters", "Foreign institutions", "Domestic institutions",
                  "Non-institutions", "Non-promoter non-public")


def _f(v: Any) -> Optional[float]:
    """Numeric → float, rounded to the two decimals the source is filed at.

    Every percentage here arrives as a Decimal that carries float noise from
    the XBRL parse (30.620000000000005). Rounding at the boundary keeps that
    out of the payload rather than out of the component."""
    if v is None:
        return None
    try:
        return round(float(v), 2)
    except (TypeError, ValueError):
        return None


@router.get("/{symbol}/shareholding")
def get_shareholding(symbol: str, authorization: Optional[str] = Header(None)) -> dict:
    """Who owns the company, quarter by quarter, plus promoter pledge.

    Three payloads in one call because the panel draws them together: the
    top-level series (the stacked chart), the latest two-level breakdown (the
    bars), and the named holders above 1% (the table).
    """
    _auth(authorization)
    who = _resolve(symbol)

    def build() -> dict:
        with FinancialsSessionLocal() as db:
            filings = db.execute(text("""
                SELECT id, quarter_end::text q, promoter_pct, public_pct,
                       promoter_encumbered_pct, promoter_pledged, total_shares
                  FROM shp.filings
                 WHERE symbol = :s
              ORDER BY quarter_end DESC
                 LIMIT :n"""), {"s": who["symbol"], "n": _MAX_QUARTERS}).mappings().all()
            if not filings:
                return {"symbol": who["symbol"], "available": False,
                        "quarters": [], "latest": None, "holders": []}

            ids = [f["id"] for f in filings]
            cats = db.execute(text("""
                SELECT filing_id, category, pct, shareholders
                  FROM shp.category
                 WHERE filing_id = ANY(:ids)"""), {"ids": ids}).mappings().all()

            latest_id = filings[0]["id"]
            holders = db.execute(text("""
                SELECT name, bucket, pct, shares
                  FROM shp.holder
                 WHERE filing_id = :f AND pct IS NOT NULL
              ORDER BY pct DESC
                 LIMIT 20"""), {"f": latest_id}).mappings().all()

        # filing_id → {category: pct}
        by_filing: dict[int, dict[str, float]] = {}
        for c in cats:
            pct = _f(c["pct"])
            if pct is None:
                continue
            by_filing.setdefault(c["filing_id"], {})[c["category"]] = pct

        # ── the stacked series, oldest → newest so the chart reads left to right
        quarters = []
        for f in reversed(filings):
            got = by_filing.get(f["id"], {})
            row: dict[str, Any] = {"quarter": f["q"],
                                   "pledge_pct": _f(f["promoter_encumbered_pct"])}
            for member, label in _SHP_TOP.items():
                if member in got:
                    row[label] = got[member]
            # Promoter percentage is also carried on the filing header, and it
            # is populated for ~35k filings where the category row is not.
            if "Promoters" not in row and f["promoter_pct"] is not None:
                row["Promoters"] = _f(f["promoter_pct"])
            quarters.append(row)

        # ── the latest breakdown, two levels deep
        got = by_filing.get(latest_id, {})
        groups = []
        for label in _SHP_TOP_ORDER:
            member = next((m for m, lab in _SHP_TOP.items() if lab == label), None)
            pct = got.get(member) if member else None
            if pct is None and label == "Promoters":
                pct = _f(filings[0]["promoter_pct"])
            if pct is None:
                continue
            children = []
            for cm, clabel in _SHP_CHILD.get(label, {}).items():
                cp = got.get(cm)
                if cp is None:
                    continue
                # Both spellings map to one label; keep the larger rather than
                # emitting the same slice twice.
                prior = next((c for c in children if c["label"] == clabel), None)
                if prior:
                    prior["pct"] = max(prior["pct"], cp)
                else:
                    children.append({"label": clabel, "pct": cp})
            children.sort(key=lambda c: c["pct"], reverse=True)
            groups.append({"label": label, "pct": pct, "children": children})

        return {
            "symbol": who["symbol"],
            "available": True,
            "quarter": filings[0]["q"],
            "quarters": quarters,
            "groups": groups,
            "pledge_pct": _f(filings[0]["promoter_encumbered_pct"]),
            "promoter_pct": _f(filings[0]["promoter_pct"]),
            "holders": [{"name": h["name"], "bucket": h["bucket"],
                         "pct": _f(h["pct"]), "shares": _f(h["shares"])}
                        for h in holders if (_f(h["pct"]) or 0) >= 0.5],
        }

    return _cached(f"shp:{who['symbol']}", build)


# ── the flows layer (charto's local store) ──────────────────────────────────
#
# Delivery percentage, futures open interest and bulk/block deals live in
# charto's SQLite rather than Postgres, because charto's request path is
# stdlib-only and offline by design. This module opens that file READ-ONLY and
# never writes to it, so the two services cannot fight over a lock.
#
# The file is optional. A deployment without charto attached simply reports
# `available: false` and the page draws nothing — the same coverage rule the
# rest of this module follows.

_CHARTO_DB = os.environ.get(
    "CHARTO_DB",
    os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))))), "charto", "data", "charto_bars.db"),
)


def _charto(sql: str, args: tuple = ()) -> list[dict]:
    """One read-only query against the charto store, or [] if it is absent.

    Opened per call with `mode=ro` and a short timeout: these are indexed
    lookups over a file another process is actively writing, so holding a
    connection open across requests would be the only way to get a lock error.
    """
    if not os.path.exists(_CHARTO_DB):
        return []
    try:
        conn = sqlite3.connect(f"file:{_CHARTO_DB}?mode=ro", uri=True, timeout=3.0)
        conn.row_factory = sqlite3.Row
        try:
            return [dict(r) for r in conn.execute(sql, args)]
        finally:
            conn.close()
    except sqlite3.Error:
        logger.debug("charto read failed", exc_info=True)
        return []


@router.get("/{symbol}/flows")
def get_flows(symbol: str, days: int = Query(180, ge=20, le=750),
              authorization: Optional[str] = Header(None)) -> dict:
    """Delivery percentage and futures open interest — did ownership move?

    Delivery answers whether a day's volume was real transfer or intraday
    churn; open interest answers whether a move was fresh positioning or an
    unwind. Neither is visible in price, which is the whole reason the section
    exists.

    Open interest is summed ACROSS EXPIRIES per date. A single expiry's OI
    falls to zero as it rolls, so charting one contract would show an unwind
    every month that never happened.
    """
    _auth(authorization)
    who = _resolve(symbol)
    sym = who["symbol"]

    def build() -> dict:
        delivery = _charto(
            "SELECT d, close, qty, deliv_qty, deliv_per, trades FROM delivery "
            "WHERE symbol = ? ORDER BY d DESC LIMIT ?", (sym, days))
        oi = _charto(
            "SELECT d, SUM(oi) oi, SUM(oi_chg) oi_chg FROM fut_oi "
            "WHERE symbol = ? GROUP BY d ORDER BY d DESC LIMIT ?", (sym, days))
        if not delivery and not oi:
            return {"symbol": sym, "available": False,
                    "delivery": [], "oi": [], "summary": None}

        delivery.reverse()
        oi.reverse()

        pcts = [r["deliv_per"] for r in delivery if r.get("deliv_per") is not None]
        latest = delivery[-1] if delivery else None
        median = None
        if len(pcts) >= 5:
            tail = sorted(pcts[-20:])
            median = round(tail[len(tail) // 2], 2)

        latest_oi = oi[-1] if oi else None
        summary = {
            "date": latest["d"] if latest else (latest_oi["d"] if latest_oi else None),
            "delivery_pct": round(latest["deliv_per"], 2) if latest and latest.get("deliv_per") is not None else None,
            "delivery_median_20d": median,
            "volume": latest["qty"] if latest else None,
            "delivered": latest["deliv_qty"] if latest else None,
            "trades": latest["trades"] if latest else None,
            "oi": latest_oi["oi"] if latest_oi else None,
            "oi_chg": latest_oi["oi_chg"] if latest_oi else None,
            "close": latest["close"] if latest else None,
        }
        return {"symbol": sym, "available": True, "summary": summary,
                "delivery": delivery, "oi": oi}

    return _cached(f"flows:{sym}:{days}", build)


@router.get("/{symbol}/deals")
def get_deals(symbol: str, limit: int = Query(60, ge=5, le=200),
              authorization: Optional[str] = Header(None)) -> dict:
    """Bulk and block deals, with the counterparty named.

    The exchanges publish the client name on every deal above the reporting
    threshold, which makes this the only public surface that says WHO traded
    size rather than that size traded. Buy and sell legs of one block are two
    separate rows in the source and are kept that way — collapsing them would
    hide which side a named fund was on.
    """
    _auth(authorization)
    who = _resolve(symbol)
    sym = who["symbol"]

    def build() -> dict:
        rows = _charto(
            "SELECT d, kind, client, side, qty, price FROM deals "
            "WHERE symbol = ? ORDER BY d DESC, qty DESC LIMIT ?", (sym, limit))
        for r in rows:
            q, p = r.get("qty"), r.get("price")
            r["value"] = round(q * p, 2) if q and p else None
        return {"symbol": sym, "available": bool(rows), "deals": rows}

    return _cached(f"deals:{sym}:{limit}", build)


@router.get("/{symbol}/patterns")
def get_patterns(symbol: str, interval: str = Query("1d"),
                 horizon: int = Query(20),
                 authorization: Optional[str] = Header(None)) -> dict:
    """Pattern hit rates measured against a matched control.

    These are UNIVERSE statistics, not a reading of this symbol's chart: every
    row is the same pattern measured across 500 Indian equities, with the
    control being the base rate of the same directional move on bars where the
    pattern did NOT fire. `edge_pp` is the difference, and it is the only
    number here worth acting on — a 58% hit rate against a 57% base rate is
    noise wearing a pattern's name.

    Rows are returned in both directions, including negative edges, because a
    pattern that reliably fails is as useful as one that works and is the part
    every other product leaves out.
    """
    _auth(authorization)
    _resolve(symbol)

    def build() -> dict:
        rows = _charto(
            "SELECT kind, family, interval, horizon, n, n_symbols, "
            "       with_direction_rate_pct rate, control_base_rate_pct control, "
            "       edge_pp edge, edge_se_pp se, avg_move_pct move "
            "  FROM pattern_stats "
            " WHERE scope = 'equity_in' AND interval = ? AND horizon = ? "
            "   AND n >= 200 "
            " ORDER BY edge_pp DESC", (interval, horizon))
        opts = _charto(
            "SELECT DISTINCT interval, horizon FROM pattern_stats "
            " WHERE scope = 'equity_in' ORDER BY interval, horizon")
        return {"available": bool(rows), "interval": interval, "horizon": horizon,
                "options": opts, "patterns": rows}

    return _cached(f"patterns:{interval}:{horizon}", build)
