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
from typing import Any, Optional

from fastapi import APIRouter, Header, HTTPException, Query
from sqlalchemy import text

from backend.auth.jwt_handler import get_user_id_from_token
from backend.cache import redis_client
from backend.config import settings
from backend.database import EnrichSessionLocal, FinancialsSessionLocal, SessionLocal

router = APIRouter(prefix="/api/stock", tags=["Stock detail"])
logger = logging.getLogger(__name__)

# Bump the version whenever a payload SHAPE changes — a cached v1 body served
# to v2 client code is a KeyError, not a stale number.
_CACHE_PREFIX = "stockdetail:v3:"
_CACHE_TTL = 6 * 3600          # fundamentals move quarterly; 6h is generous
_MAX_QUARTERS = 40
_MAX_FACTS = 1200
_MAX_DOCS = 120


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
