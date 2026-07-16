"""Cross-sectional fundamental SCREEN over the Moneycontrol `financials` DB.

The "replace screener.in for the basics" feature. Where
`backend.market.financials_db` answers *one company, one metric* questions,
this module answers *which companies pass these constraints* questions:

    screen_by_fundamentals(
        [{"field": "roe", "op": ">", "value": 18}],
        sector="pharma",
        sort_by={"field": "roe", "dir": "desc"},
        limit=15,
    )

Design choices (mirrors backend/market/financials_db.py)
  - Never writes. Reads through `FinancialsSessionLocal` so a slow
    cross-sectional scan can't starve the operational `pivot_db` pool.
  - Raw SQL via `text()` — the `mc.*` schema is owned by the scraper, not us.
  - Reuses the FIELD_MAP synonyms + the `pe = 1 / earnings_yield` trick from
    financials_db so the screener and the single-company path agree on what a
    metric *means*.
  - Cross-section is built one CTE per referenced metric. Each CTE picks the
    LATEST period per sc_id (DISTINCT ON), preferring the `consolidated`
    basis but falling back to `standalone` (which is where most of MC's
    ratio coverage actually lives — ROE is standalone-only in this DB).
  - Recency floor: latest-per-sc_id alone surfaces long-dormant/delisted
    shells whose newest row is from 2006-2012 with absurd ratios. A default
    floor (`MIN_PERIOD_END`, ~2 fiscal years back) keeps the screen to
    companies with recent filings. Override via `min_period_end=None` to
    disable.

Environment reality (audited 2026-05, do not silently paper over):
  - mc.companies.sector and .market_cap are 100% NULL — we DO NOT read them.
    Sector is derived from `industry_slug` via SLUG_SECTOR_ALIASES.
  - mc.companies.nse_symbol is populated on only ~10 of 11,256 rows, so the
    display `symbol` falls back to `ticker` (~3,020 populated) then sc_id.
  - There is no point-in-time market-cap line item in mc.statement_lines, so
    a `market_cap` filter cannot be served from this DB — it is reported in
    the result `note` and skipped, mirroring financials_db's `mcap -> None`.
"""
from __future__ import annotations

import logging
import re
import time
from datetime import date

from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.database import FinancialsSessionLocal
from backend.market.financials_db import FIELD_MAP
from backend.services.sector_universe import _UNIVERSE as _SECTOR_UNIVERSE

# Curated cap tiers (P5, 2026-05-29 retail eval). The financials DB has NO
# usable market_cap column (mc.companies.market_cap is 100% NULL), so a
# "large cap" screen would otherwise rank an 11K-row micro-cap universe by
# ROE and surface artifacts (Kimia Bio ROE 96%). We restrict cap-qualified
# screens to the curated ~130-name sector_universe by approximate ₹-crore
# market cap. Narrower than NIFTY100/500 but recognizable + safe; disclosed
# in the result note.
_LARGE_CAP_SYMS: frozenset = frozenset(
    e.symbol.upper() for e in _SECTOR_UNIVERSE if e.mcap_cr >= 50000
)
_MID_CAP_SYMS: frozenset = frozenset(
    e.symbol.upper() for e in _SECTOR_UNIVERSE if 20000 <= e.mcap_cr < 50000
)
_CAP_TIER_ALIASES = {
    "largecap": "large", "large-cap": "large", "large": "large",
    "bluechip": "large", "blue chip": "large", "blue-chip": "large", "big": "large",
    "midcap": "mid", "mid-cap": "mid", "mid": "mid",
    "smallcap": "small", "small-cap": "small", "small": "small",
}

logger = logging.getLogger(__name__)


# ── Real market cap from the enrich DB ─────────────────────────────────────
# The financials DB has no market-cap column, but the enrich DB
# (enrich.company_profile.market_cap, ~5k names, raw rupees) does, keyed by the
# SAME sc_id as mc.companies. We snapshot the whole cap map once (cheap; ~5k
# rows) and derive tier/floor membership in memory, so a cap-tiered or
# recognizable-names screen restricts to REAL market caps across the full
# universe instead of the ~80-name curated whitelist (which, at the strict
# recency floor, left "large-cap auto" returning a single name). Fails open: if
# enrich is down the caller falls back to the curated whitelist.
_MCAP_CACHE: dict[str, object] = {"ts": 0.0, "map": {}}
_MCAP_TTL_S = 3600  # market caps drift slowly; refresh hourly per process

# Real trailing P/E (live price ÷ TTM EPS) from the enrich DB, keyed by the same
# sc_id as mc.companies. The mc financials DB stores only Earnings Yield rounded
# to 2 dp, so P/E derived as 1/EY snaps onto a coarse grid (25.00, 16.67, 12.50…)
# — the artifact the screen showed. enrich.company_profile.raw_info->'trailingPE'
# carries the un-quantized real value for ~4k names, so we PREFER it and fall
# back to 1/EY only where enrich lacks the name. Same cache/fail-open contract as
# the market-cap snapshot above.
_PE_CACHE: dict[str, object] = {"ts": 0.0, "map": {}}
_PE_TTL_S = 3600

# 1-year price return (%) from enrich raw_info '52WeekChange' (a fraction) —
# a CONTEXT column on every screen row, same loader pattern as caps/P/E.
_YR1_CACHE: dict[str, object] = {"ts": 0.0, "map": {}}
_YR1_TTL_S = 3600

# tier -> (min_cr inclusive, max_cr exclusive), ₹ crore. Matches sector_universe.
_CAP_TIER_RANGES: dict[str, tuple[float | None, float | None]] = {
    "large": (50_000, None),
    "mid": (20_000, 50_000),
    "small": (None, 20_000),
}
# Floor (₹ crore) applied to a BARE sector ranking (a "best/cheapest in <sector>"
# ask with no explicit numeric filter and no cap word) so obscure micro-cap
# names don't dominate the ranking — the user means recognizable companies.
_DEFAULT_SECTOR_FLOOR_CR = 3_000

# Fixed metric priority for the default rank when the caller named no sort AND no
# market_cap filter — so the chosen ranking never depends on the order the caller
# listed the filters in. Growth and quality metrics lead (they read as the user's
# likely intent); valuation ratios rank last (a screen rarely means "rank by the
# cheapest" unless it said so).
_DEFAULT_SORT_PRIORITY: tuple[str, ...] = (
    "revenue_growth", "net_profit_growth", "eps_growth",
    "roe", "roce", "roic", "roa",
    "net_profit_margin", "operating_margin", "ebitda_margin", "gross_margin",
    "payout", "interest_coverage", "current_ratio", "quick_ratio",
    "pe", "peg", "price_to_book", "ev_to_ebitda", "de",
)


def _load_market_caps() -> dict[str, float]:
    """{sc_id: market_cap_in_crore} from enrich.company_profile, cached ~1h.
    Fails open to the last snapshot (or {}) so a cap filter degrades to a no-op
    rather than erroring the screen."""
    now = time.time()
    cached: dict[str, float] = _MCAP_CACHE["map"]  # type: ignore[assignment]
    if cached and now - float(_MCAP_CACHE["ts"]) < _MCAP_TTL_S:
        return cached
    out: dict[str, float] = {}
    try:
        from backend.market.enrich_db import EnrichSessionLocal, is_enabled

        if is_enabled():
            s = EnrichSessionLocal()
            try:
                rows = s.execute(
                    text(
                        "SELECT sc_id, market_cap FROM enrich.company_profile "
                        "WHERE market_cap IS NOT NULL AND market_cap > 0"
                    )
                ).fetchall()
            finally:
                s.close()
            for sc_id, mc in rows:
                if sc_id is not None and mc:
                    out[str(sc_id)] = float(mc) / 1e7  # rupees -> ₹ crore
    except Exception as exc:  # noqa: BLE001
        logger.warning("[screen] market-cap load failed: %s", exc)
        return cached or {}
    if out:
        _MCAP_CACHE["map"] = out
        _MCAP_CACHE["ts"] = now
    return out or cached or {}


def _load_trailing_pe() -> dict[str, float]:
    """{sc_id: trailing_pe} from enrich.company_profile.raw_info, cached ~1h.
    Only rows with a numeric trailingPE in (0, 500] (a plausibility bound that
    drops junk casts). Fails open to the last snapshot (or {}) so the screen
    degrades to the Earnings-Yield fallback rather than erroring."""
    now = time.time()
    cached: dict[str, float] = _PE_CACHE["map"]  # type: ignore[assignment]
    if cached and now - float(_PE_CACHE["ts"]) < _PE_TTL_S:
        return cached
    out: dict[str, float] = {}
    try:
        from backend.market.enrich_db import EnrichSessionLocal, is_enabled

        if is_enabled():
            s = EnrichSessionLocal()
            try:
                rows = s.execute(
                    text(
                        "SELECT sc_id, (raw_info->>'trailingPE')::float AS pe "
                        "FROM enrich.company_profile "
                        "WHERE raw_info->>'trailingPE' ~ '^-?[0-9]+(\\.[0-9]+)?$' "
                        "AND (raw_info->>'trailingPE')::float > 0 "
                        "AND (raw_info->>'trailingPE')::float <= 500"
                    )
                ).fetchall()
            finally:
                s.close()
            for sc_id, pe in rows:
                if sc_id is not None and pe:
                    out[str(sc_id)] = float(pe)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[screen] trailing-P/E load failed: %s", exc)
        return cached or {}
    if out:
        _PE_CACHE["map"] = out
        _PE_CACHE["ts"] = now
    return out or cached or {}


def _load_52w_change() -> dict[str, float]:
    """{sc_id: one_year_return_pct} from enrich raw_info '52WeekChange'
    (stored as a fraction; ×100 here), cached ~1h, fail-open like the caps
    and trailing-P/E loaders. Context-only — never filters or sorts."""
    now = time.time()
    cached: dict[str, float] = _YR1_CACHE["map"]  # type: ignore[assignment]
    if cached and now - float(_YR1_CACHE["ts"]) < _YR1_TTL_S:
        return cached
    out: dict[str, float] = {}
    try:
        from backend.market.enrich_db import EnrichSessionLocal, is_enabled

        if is_enabled():
            s = EnrichSessionLocal()
            try:
                rows = s.execute(
                    text(
                        "SELECT sc_id, (raw_info->>'52WeekChange')::float "
                        "FROM enrich.company_profile "
                        "WHERE raw_info->>'52WeekChange' ~ "
                        "'^-?[0-9]+(\\.[0-9]+)?([eE][+-]?[0-9]+)?$' "
                        "AND (raw_info->>'52WeekChange')::float BETWEEN -0.99 AND 20"
                    )
                ).fetchall()
            finally:
                s.close()
            for sc_id, frac in rows:
                if sc_id is not None and frac is not None:
                    out[str(sc_id)] = float(frac) * 100.0
    except Exception as exc:  # noqa: BLE001
        logger.warning("[screen] 52w-change load failed: %s", exc)
        return cached or {}
    if out:
        _YR1_CACHE["map"] = out
        _YR1_CACHE["ts"] = now
    return out or cached or {}


def _sc_ids_in_cap_range(
    min_cr: float | None, max_cr: float | None
) -> list[str]:
    """sc_ids whose enrich market cap (₹ crore) is in [min_cr, max_cr). Empty
    list means "enrich has no usable caps" — caller decides the fallback."""
    caps = _load_market_caps()
    out: list[str] = []
    for sc_id, cr in caps.items():
        if min_cr is not None and cr < min_cr:
            continue
        if max_cr is not None and cr >= max_cr:
            continue
        out.append(sc_id)
    return out


# ── Enrich-backed SECTOR screen (clean sectors + real P/E) ─────────────────
# The Moneycontrol `mc` DB classifies sectors via a scraped `industry_slug`
# that is badly polluted for recognizable names — Tata Motors sits under the
# junk slug `tatamotorscom` (symbol TMCV), Eicher/Ashok Leyland have impostor
# rows tagged `financeinvestments`, and its P/E source (Earnings Yield) is
# sparse for large caps. So "best P/E in auto" over mc returned obscure micro-
# cap ancillaries with no OEMs. The enrich DB (yfinance) has CLEAN industry
# labels + real trailing P/E (4.1k names) + real market cap, so a SECTOR screen
# is served from enrich instead. Coarse sector -> yfinance industry names:
_ENRICH_SECTOR_INDUSTRIES: dict[str, list[str]] = {
    "auto": ["Auto Manufacturers", "Auto & Truck Dealerships",
             "Recreational Vehicles"],
    "autoancillary": ["Auto Parts"],
    "bank": ["Banks - Regional", "Banks - Diversified"],
    "pharma": ["Drug Manufacturers - Specialty & Generic",
               "Drug Manufacturers - General", "Biotechnology",
               "Pharmaceutical Retailers", "Medical Devices",
               "Medical Care Facilities", "Diagnostics & Research",
               "Healthcare Plans", "Medical Instruments & Supplies"],
    "it": ["Information Technology Services", "Software - Application",
           "Software - Infrastructure"],
    "energy": ["Oil & Gas Refining & Marketing", "Oil & Gas Integrated",
               "Oil & Gas E&P", "Oil & Gas Equipment & Services",
               "Thermal Coal", "Utilities - Renewable", "Solar",
               "Utilities - Independent Power Producers",
               "Utilities - Regulated Electric"],
    "metal": ["Steel", "Aluminum", "Other Industrial Metals & Mining",
              "Copper", "Gold", "Coking Coal"],
    "finance": ["Credit Services", "Capital Markets", "Asset Management",
                "Mortgage Finance", "Insurance - Life",
                "Insurance - Property & Casualty", "Insurance - Diversified",
                "Financial Data & Stock Exchanges"],
    "chemicals": ["Specialty Chemicals", "Chemicals", "Agricultural Inputs"],
    "fmcg": ["Packaged Foods", "Confectioners", "Household & Personal Products",
             "Beverages - Non-Alcoholic", "Beverages - Wineries & Distilleries",
             "Farm Products", "Food Distribution", "Tobacco"],
    "infra": ["Engineering & Construction", "Building Materials",
              "Building Products & Equipment", "Real Estate - Development",
              "Infrastructure Operations"],
    "textiles": ["Textile Manufacturing", "Apparel Manufacturing",
                 "Footwear & Accessories"],
}

# Screen field -> (enrich raw_info key, scale). yfinance stores ROE/payout/ROA as
# fractions (0.18) so ×100 to a percent; P/E, P/B and EV/EBITDA are already
# ratios. de/roce have no clean enrich key, so a screen referencing them stays
# on the mc path. price_to_book/ev_to_ebitda/roa also exist as direct ratio
# line items in the MC ratios table (see FIELD_MAP), but that table's company
# coverage is sparse and skews toward smaller/less-liquid names (e.g. TCS,
# ICICIBANK, HDFCBANK, INFY all lack a populated 'ratios' row there) — the
# yfinance-backed enrich DB has much better coverage for exactly the
# large/liquid names a sector screen is usually asked about, so these three
# are served from enrich whenever the sector qualifies, same as pe/roe/payout.
_ENRICH_METRIC_KEYS: dict[str, tuple[str, float]] = {
    "pe": ("trailingPE", 1.0),
    "roe": ("returnOnEquity", 100.0),
    "payout": ("payoutRatio", 100.0),
    "price_to_book": ("priceToBook", 1.0),
    "ev_to_ebitda": ("enterpriseToEbitda", 1.0),
    "roa": ("returnOnAssets", 100.0),
}


def _enrich_plausible(field: str, col: str) -> str:
    """SQL plausibility bound for `col` (excludes data-quality outliers)."""
    if field == "pe":
        return f"{col} > 0 AND {col} <= 500"
    if field == "roe":
        return f"{col} BETWEEN -200 AND 200"
    if field == "payout":
        return f"{col} BETWEEN 0 AND 100"
    if field == "price_to_book":
        return f"{col} > 0 AND {col} <= 100"
    if field == "ev_to_ebitda":
        return f"{col} > -100 AND {col} <= 500"
    if field == "roa":
        return f"{col} BETWEEN -100 AND 100"
    return "TRUE"


def _enrich_can_serve(sector: str | None, metric_fields: set[str]) -> bool:
    """True when a sector screen's every referenced metric is one enrich serves
    cleanly — so we route to the clean-sector/real-P/E enrich path."""
    if not sector:
        return False
    if sector.strip().lower() not in _ENRICH_SECTOR_INDUSTRIES:
        return False
    return all(m in _ENRICH_METRIC_KEYS for m in metric_fields if m)


def _enrich_metric_sql(field: str) -> str:
    """A guarded numeric extraction of an enrich raw_info metric (NULL when the
    key is absent or non-numeric, so a bad value can't error the cast)."""
    key, scale = _ENRICH_METRIC_KEYS[field]
    expr = (
        f"CASE WHEN raw_info->>'{key}' ~ '^-?[0-9]+(\\.[0-9]+)?$' "
        f"THEN (raw_info->>'{key}')::float * {scale} END"
    )
    return expr


def screen_from_enrich(
    *,
    sector: str,
    valid_filters: list[dict],
    sort_field: str | None,
    sort_dir: str,
    tier: str | None,
    apply_default_floor: bool,
    limit: int,
    notes: list[str],
) -> dict | None:
    """Serve a SECTOR screen from the enrich (yfinance) DB — clean industry
    labels + real trailing P/E + real market cap. Returns the standard screen
    dict, or None to fall back to the mc path (enrich disabled / no rows)."""
    industries = _ENRICH_SECTOR_INDUSTRIES.get(sector.strip().lower())
    if not industries:
        return None
    try:
        from backend.market.enrich_db import EnrichSessionLocal, is_enabled

        if not is_enabled():
            return None
    except Exception:  # noqa: BLE001
        return None

    metric_fields = list({f["field"] for f in valid_filters} | ({sort_field} if sort_field else set()))
    if not metric_fields:
        metric_fields = ["roe"]
    # Build the SELECT with a guarded numeric column per metric.
    select_metrics = ", ".join(f"{_enrich_metric_sql(m)} AS val_{m}" for m in metric_fields)

    where: list[str] = ["industry = ANY(:inds)", "ticker IS NOT NULL"]
    params: dict = {"inds": industries, "lim": max(1, min(int(limit), 100))}

    # Cap constraint: real market cap (rupees) via tier or the bare-sector floor.
    if tier == "large":
        where.append("market_cap >= :cap_lo")
        params["cap_lo"] = 50_000 * 1e7
    elif tier == "mid":
        where.append("market_cap >= :cap_lo AND market_cap < :cap_hi")
        params["cap_lo"], params["cap_hi"] = 20_000 * 1e7, 50_000 * 1e7
    elif tier == "small":
        where.append("(market_cap IS NULL OR market_cap < :cap_hi)")
        params["cap_hi"] = 20_000 * 1e7
    elif apply_default_floor:
        where.append("market_cap >= :cap_lo")
        params["cap_lo"] = _DEFAULT_SECTOR_FLOOR_CR * 1e7
        notes.append(
            f"showing names above ~₹{_DEFAULT_SECTOR_FLOOR_CR:,} Cr market cap "
            "(say 'include small caps' to widen)"
        )

    # Dedup impostor rows: one row per ticker, best-matched name wins.
    inner = f"""
        SELECT DISTINCT ON (UPPER(ticker))
               ticker, COALESCE(long_name, company_name) AS name, industry,
               market_cap, {select_metrics},
               (CASE WHEN raw_info->>'52WeekChange' ~
                     '^-?[0-9]+(\\.[0-9]+)?([eE][+-]?[0-9]+)?$'
                     THEN (raw_info->>'52WeekChange')::float * 100 END
               ) AS ctx_yr1_pct
        FROM enrich.company_profile
        WHERE {" AND ".join(where)}
        ORDER BY UPPER(ticker), match_score DESC NULLS LAST, market_cap DESC NULLS LAST
    """

    outer_where: list[str] = []
    for f in valid_filters:
        m = f["field"]
        params_key = f"f_{m}"
        outer_where.append(f"val_{m} {f['op']} :{params_key}")
        params[params_key] = f["value"]
    # Plausibility bounds on every metric in play.
    for m in metric_fields:
        outer_where.append(_enrich_plausible(m, f"val_{m}"))
    sf = sort_field if sort_field in metric_fields else sorted(metric_fields)[0]
    order = "ASC" if sort_dir == "asc" else "DESC"
    # `ticker` is the final tiebreak so tied metric values order deterministically
    # (without it, parallel plans reorder equal-valued rows run-to-run).
    sql = f"""
        SELECT ticker, name, industry, market_cap,
               {", ".join(f"val_{m}" for m in metric_fields)}, ctx_yr1_pct
        FROM ({inner}) t
        {"WHERE " + " AND ".join(outer_where) if outer_where else ""}
        ORDER BY val_{sf} {order} NULLS LAST, ticker ASC
        LIMIT :lim
    """
    s = EnrichSessionLocal()
    try:
        rows = s.execute(text(sql), params).fetchall()
    except Exception as exc:  # noqa: BLE001
        logger.warning("[screen] enrich sector path failed, falling back: %s", exc)
        return None
    finally:
        s.close()

    metric_idx = {m: 4 + i for i, m in enumerate(metric_fields)}
    results: list[dict] = []
    for r in rows:
        rec: dict = {
            "symbol": r[0],
            "name": r[1],
            "sector": sector.strip().lower(),
            "market_cap_cr": round(float(r[3]) / 1e7) if r[3] else None,
        }
        for m in metric_fields:
            v = r[metric_idx[m]]
            rec[m] = round(float(v), 2) if v is not None else None
        if r[-1] is not None:  # ctx_yr1_pct — appended last in the SELECT
            rec["one_year_pct"] = round(float(r[-1]), 1)
        results.append(rec)

    notes.append("sector + P/E from company profiles (yfinance) — not the MC ratios DB")
    return {
        "count": len(results),
        "results": results,
        "applied_filters": [dict(f) for f in valid_filters],
        "sorted_by": {"field": sf, "dir": sort_dir},
        "note": "; ".join(notes),
    }


# ── Field resolution ──────────────────────────────────────────────────────
# Public screen field -> (line_item synonyms, kind).
#   kind="direct"   : compare against value_numeric of the line item directly.
#   kind="pe_from_ey": stored as Earnings Yield (a fraction); PE = 1 / EY, so
#                      the comparison operator must be inverted (see _OP_INVERT).
#   kind="unsupported": no point-in-time value in this DB (e.g. market_cap).
#
# Synonyms are reused verbatim from financials_db.FIELD_MAP so the screener
# and the single-company lookup never disagree on what a field resolves to.
_FIELD_DEFS: dict[str, dict] = {
    "roe": {
        "kind": "direct",
        "items": list(FIELD_MAP["roe"][1]),
        "label": "ROE %",
    },
    "roce": {
        "kind": "direct",
        "items": list(FIELD_MAP["roce"][1]),
        "label": "ROCE %",
    },
    "de": {
        "kind": "direct",
        "items": list(FIELD_MAP["debt_to_equity"][1]),
        "label": "Debt/Equity",
    },
    "payout": {
        "kind": "direct",
        "items": list(FIELD_MAP["dividend_payout"][1]),
        "label": "Dividend Payout %",
    },
    "pe": {
        # MC publishes Earnings Yield (= E/P, a fraction), not a P/E line.
        # We screen on PE = 1/EY and invert the operator at SQL-build time.
        "kind": "pe_from_ey",
        "items": list(FIELD_MAP["earnings_yield"][1]),
        "label": "P/E",
    },
    # PEG = trailing P/E ÷ trailing YoY EPS growth (%, as a plain number, e.g.
    # PE=20 growth=20% -> PEG=1.0). Was previously UNSUPPORTED (not a field,
    # not expressible via custom_ratios either — custom_ratios only combines
    # two raw line items, and "growth" is a paired-period derivation, not a
    # line item) — so a "PEG < 1" constraint got silently left out of the
    # tool call entirely upstream (2026-07-14 eval finding #11: 3-constraint
    # screen ran with only 2 filters, no disclosure). Both P/E (pe_from_ey)
    # and EPS growth (growth) are already computed elsewhere in this module;
    # PEG composes those same two CTE shapes (see the "peg" kind below), so
    # this is a genuine derivation, not an approximation.
    "peg": {
        "kind": "peg",
        "items": [],
        "pe_items": list(FIELD_MAP["earnings_yield"][1]),
        "growth_items": list(FIELD_MAP["eps_basic"][1]),
        "label": "PEG",
    },
    "market_cap": {
        # mc.statement_lines has no market-cap line item and mc.companies.
        # market_cap is 100% NULL — but the enrich DB (enrich.company_profile.
        # market_cap, keyed by the SAME sc_id) does, and we already snapshot it
        # in `_load_market_caps()` for cap tiers. So market_cap is a REAL numeric
        # field: injected as an in-memory `caps` CTE (unnest of two arrays) and
        # filtered/sorted like any metric. Unit is ₹ crore. (2026-07-11: closed
        # the wiring gap that made this "unsupported" and produced the misleading
        # "not available in financials DB" note the Screener TAB never showed.)
        "kind": "market_cap",
        "items": [],
        "label": "Market Cap",
    },
    # ── Growth metrics (YoY over the two latest ANNUAL periods) ──────────────
    # The DB stores only annual periods (period_kind='annual'); we pair each
    # sc_id's two most-recent annual filings of the SAME basis (consolidated
    # preferred) and compute (latest−prior)/|prior|×100. This is what lets the
    # screener honour "positive revenue growth" / "growing profits" asks that
    # previously had NO representable field and were silently dropped.
    "revenue_growth": {
        "kind": "growth",
        "items": list(FIELD_MAP["revenue"][1]),
        "label": "Rev Growth %",
    },
    "net_profit_growth": {
        "kind": "growth",
        "items": list(FIELD_MAP["net_profit"][1]),
        "label": "Profit Growth %",
    },
    "eps_growth": {
        "kind": "growth",
        "items": list(FIELD_MAP["eps_basic"][1]),
        "label": "EPS Growth %",
    },
    # The rest of this block was a wiring gap, not a data gap: MC publishes
    # these as direct ratio line items in the SAME `ratios` table as
    # roe/roce/de/payout above (see FIELD_MAP) — they just were never added
    # to this screener's field set (2026-07-08).
    "price_to_book": {
        "kind": "direct",
        "items": list(FIELD_MAP["price_to_book"][1]),
        "label": "P/B",
    },
    "ev_to_ebitda": {
        "kind": "direct",
        "items": list(FIELD_MAP["ev_to_ebitda"][1]),
        "label": "EV/EBITDA",
    },
    "roa": {
        "kind": "direct",
        "items": list(FIELD_MAP["roa"][1]),
        "label": "ROA %",
    },
    "current_ratio": {
        "kind": "direct",
        "items": list(FIELD_MAP["current_ratio"][1]),
        "label": "Current Ratio",
    },
    "quick_ratio": {
        "kind": "direct",
        "items": list(FIELD_MAP["quick_ratio"][1]),
        "label": "Quick Ratio",
    },
    "interest_coverage": {
        "kind": "direct",
        "items": list(FIELD_MAP["interest_coverage"][1]),
        "label": "Interest Coverage",
    },
    "net_profit_margin": {
        "kind": "direct",
        "items": list(FIELD_MAP["net_profit_margin"][1]),
        "label": "Net Profit Margin %",
    },
    "ebitda_margin": {
        "kind": "direct",
        "items": list(FIELD_MAP["ebitda_margin"][1]),
        "label": "EBITDA Margin %",
    },
    "asset_turnover": {
        "kind": "direct",
        "items": list(FIELD_MAP["asset_turnover"][1]),
        "label": "Asset Turnover",
    },
    # Extended ratio set (scraped where present + pivot-derived backfill).
    "roic": {
        "kind": "direct",
        "items": list(FIELD_MAP["roic"][1]),
        "label": "ROIC %",
    },
    "operating_margin": {
        "kind": "direct",
        "items": list(FIELD_MAP["operating_margin"][1]),
        "label": "Op Margin %",
    },
    "gross_margin": {
        "kind": "direct",
        "items": list(FIELD_MAP["gross_margin"][1]),
        "label": "Gross Margin %",
    },
    "inventory_turnover": {
        "kind": "direct",
        "items": list(FIELD_MAP["inventory_turnover"][1]),
        "label": "Inventory Turnover",
    },
    "receivables_turnover": {
        "kind": "direct",
        "items": list(FIELD_MAP["receivables_turnover"][1]),
        "label": "Receivables Turnover",
    },
}

# ── Full-DB coverage: every remaining FIELD_MAP line item as a screenable field ──
# The user should be able to screen on ANY metric the DB carries, not just the
# curated ratio set above. These are RAW statement line items (₹ crore for
# absolute values, ₹ for per-share, ×/% for the rest) resolved the same way as
# `direct` ratios: latest annual value per sc_id, basis-preferred. `unit` drives
# display formatting. Anything already defined above (roe/roce/pe/…) is skipped.
_RAW_ITEM_FIELDS: dict[str, tuple[str, str]] = {
    # field name: (label, unit)  — unit ∈ {"cr","rs","x","pct"}
    "revenue": ("Revenue", "cr"),
    "net_profit": ("Net Profit", "cr"),
    "operating_profit": ("Operating Profit", "cr"),
    "eps_basic": ("EPS (Basic)", "rs"),
    "eps_diluted": ("EPS (Diluted)", "rs"),
    "interest_expense": ("Interest Expense", "cr"),
    "total_debt": ("Total Debt", "cr"),
    "total_equity": ("Total Equity", "cr"),
    "reserves": ("Reserves", "cr"),
    "cash_from_ops": ("Cash from Ops", "cr"),
    "book_value_per_share": ("Book Value/Share", "rs"),
    "enterprise_value_cr": ("Enterprise Value", "cr"),
}
for _rf, (_lbl, _unit) in _RAW_ITEM_FIELDS.items():
    if _rf in _FIELD_DEFS or _rf not in FIELD_MAP:
        continue
    _FIELD_DEFS[_rf] = {
        "kind": "direct",
        "items": list(FIELD_MAP[_rf][1]),
        "label": _lbl,
        "unit": _unit,
    }

# Units for the ratio/growth fields so the renderer formats them correctly.
# (Absolute-value raw items carry their own `unit`; everything else is a plain
# number or a percent.)
_PCT_UNIT_FIELDS: frozenset[str] = frozenset({
    "roe", "roce", "payout", "roa", "net_profit_margin", "ebitda_margin",
    "revenue_growth", "net_profit_growth", "eps_growth",
    "roic", "operating_margin", "gross_margin",
})

# Accept a few common aliases the agent/LLM may emit for the public fields.
_FIELD_ALIASES: dict[str, str] = {
    "p/e": "pe",
    "pe_ratio": "pe",
    "price_to_earnings": "pe",
    "peg_ratio": "peg",
    "peg ratio": "peg",
    "price/earnings to growth": "peg",
    "peg (pe/growth)": "peg",
    "d/e": "de",
    "debt_to_equity": "de",
    "debt_equity": "de",
    "return_on_equity": "roe",
    "return_on_capital_employed": "roce",
    "dividend_payout": "payout",
    "payout_ratio": "payout",
    "mcap": "market_cap",
    "marketcap": "market_cap",
    "p/b": "price_to_book",
    "pb": "price_to_book",
    "pb_ratio": "price_to_book",
    "price_to_book_value": "price_to_book",
    "price/book": "price_to_book",
    "ev/ebitda": "ev_to_ebitda",
    "ev_ebitda": "ev_to_ebitda",
    "return_on_assets": "roa",
    "net_margin": "net_profit_margin",
    "profit_margin": "net_profit_margin",
    # extended ratio synonyms
    "return_on_invested_capital": "roic",
    "roce_invested": "roic",
    "ebit_margin": "operating_margin",
    "pbit_margin": "operating_margin",
    "operating_profit_margin": "operating_margin",
    "gross_profit_margin": "gross_margin",
    "inventory_turnover_ratio": "inventory_turnover",
    "stock_turnover": "inventory_turnover",
    "debtors_turnover": "receivables_turnover",
    "receivable_turnover": "receivables_turnover",
    # growth synonyms
    "revenue_growth_yoy": "revenue_growth",
    "sales_growth": "revenue_growth",
    "revenue_growth_pct": "revenue_growth",
    "topline_growth": "revenue_growth",
    "profit_growth": "net_profit_growth",
    "net_profit_growth_yoy": "net_profit_growth",
    "earnings_growth": "net_profit_growth",
    "pat_growth": "net_profit_growth",
    "eps_growth_yoy": "eps_growth",
    # raw-item synonyms
    "sales": "revenue",
    "total_revenue": "revenue",
    "net_income": "net_profit",
    "pat": "net_profit",
    "ebitda": "operating_profit",
    "eps": "eps_basic",
    "bvps": "book_value_per_share",
    "book_value": "book_value_per_share",
    "ev": "enterprise_value_cr",
    "enterprise_value": "enterprise_value_cr",
}

_ALLOWED_OPS: frozenset[str] = frozenset({"<", "<=", ">", ">=", "="})

# When screening PE via 1/EY we compare against EY directly with the operator
# flipped: PE > k  <=>  1/EY > k  <=>  EY < 1/k  (EY assumed positive). Equality
# maps to equality. This avoids a per-row division and keeps it index-friendly.
_OP_INVERT: dict[str, str] = {"<": ">", "<=": ">=", ">": "<", ">=": "<=", "=": "="}


# ── Sector mapping over industry_slug ───────────────────────────────────────
# mc.companies.sector is NULL, so we derive a coarse sector from the
# 100%-populated industry_slug. Each entry is (regex, sector). First match
# wins; ordering matters (more specific patterns first). Used both to attach a
# `sector` label to every result and to honour the optional `sector=` filter.
_SLUG_SECTOR_RULES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"^banksprivatesector|^bankspublicsector|^banks"), "bank"),
    (re.compile(r"^pharmaceuticals|^healthcare|^hospital|^diagnostics"), "pharma"),
    (re.compile(r"^computerssoftware|^itconsulting|^itenabledservices|^itnetworking"), "it"),
    (re.compile(r"^refineries|^oildrilling|^oilexploration|^gasdistribution|^powergeneration"), "energy"),
    # Ancillary rule FIRST (more specific) so parts makers aren't mislabeled
    # "auto"; OEM makers (cars / 2-3 wheelers / tractors / CVs) are the "auto"
    # sector the user means by "automobile sector".
    (re.compile(r"^autoancillar"), "auto ancillary"),
    (re.compile(r"^automobile|^auto23wheelers|^autocarsjeeps|^autotractors|^autolcvshcvs|^auto"), "auto"),
    (re.compile(r"^steel|^metalsnonferrous|^metalsferrous|^mining|^aluminium|^castingsforgings"), "metal"),
    (re.compile(r"^finance"), "finance"),
    (re.compile(r"^chemicals|^fertilisers|^pesticidesagrochemicals"), "chemicals"),
    (re.compile(r"^fmcg|^foodprocessing|^personalcare|^cigarettes|^breweriesdistilleries"), "fmcg"),
    (re.compile(r"^cement|^construction|^realestate|^infrastructure"), "infra"),
    (re.compile(r"^textiles"), "textiles"),
]

# Canonical sector -> the regex patterns whose slugs belong to it, expressed
# as a SQL ILIKE-prefix list so the WHERE clause stays parameterised. Built
# from the rules above so the two never drift.
_SECTOR_SLUG_PREFIXES: dict[str, list[str]] = {
    "bank": ["banksprivatesector%", "bankspublicsector%", "banks%"],
    "pharma": ["pharmaceuticals%", "healthcare%", "hospital%", "diagnostics%"],
    "it": ["computerssoftware%", "itconsulting%", "itenabledservices%", "itnetworking%"],
    "energy": ["refineries%", "oildrilling%", "oilexploration%", "gasdistribution%", "powergeneration%"],
    # "auto" = OEM vehicle makers (cars / 2-3 wheelers / tractors / CVs) — what
    # a user means by "the automobile sector". Auto-ANCILLARY parts makers (155
    # names, mostly micro-cap) are a SEPARATE key so "best P/E in auto" surfaces
    # Maruti/M&M/Tata Motors/Bajaj/Hero/Eicher, not obscure parts suppliers.
    "auto": ["automobile%", "auto23wheelers%", "autocarsjeeps%",
             "autotractors%", "autolcvshcvs%"],
    "autoancillary": ["autoancillaries%", "autoancillar%"],
    "metal": ["steel%", "metalsnonferrous%", "metalsferrous%", "mining%", "aluminium%", "castingsforgings%"],
    "finance": ["finance%"],
    "chemicals": ["chemicals%", "fertilisers%", "pesticidesagrochemicals%"],
    "fmcg": ["fmcg%", "foodprocessing%", "personalcare%", "cigarettes%", "breweriesdistilleries%"],
    "infra": ["cement%", "construction%", "realestate%", "infrastructure%"],
    "textiles": ["textiles%"],
}

# mc.companies.industry_slug is scraped/derived upstream and is occasionally
# wrong at the source — verified 2026-07-14: IGL (Indraprastha Gas Ltd, a
# Delhi-NCR CNG/PNG city-gas distributor) carries "hospitalsmedicalservices",
# which the (otherwise-correct) rules above map to "pharma" — so a pharma
# screen surfaced IGL, and the display label agreed with that wrong tag.
# This is a data-quality correction for known-bad rows, not a change to the
# sector-mapping rules themselves — add more entries here as they're found.
_INDUSTRY_SLUG_OVERRIDES: dict[str, str] = {
    "IGL": "gasdistribution",
}


def _corrected_industry_slug_sql() -> str:
    """SQL expression for `industry_slug` with `_INDUSTRY_SLUG_OVERRIDES`
    applied, so the sector FILTER (SQL-side) and the displayed sector
    LABEL (Python-side, via `_sector_for_slug`) never disagree."""
    if not _INDUSTRY_SLUG_OVERRIDES:
        return "c.industry_slug"
    cases = " ".join(
        f"WHEN {sym!r} THEN {slug!r}"
        for sym, slug in _INDUSTRY_SLUG_OVERRIDES.items()
    )
    return f"(CASE c.nse_symbol {cases} ELSE c.industry_slug END)"


# Default recency floor: keep the cross-section to companies whose latest
# filing is within ~2 fiscal years. Without it, dormant shells with stale
# 2006-2012 rows (and nonsense ratios) leak into every result.
_RECENCY_YEARS = 2
# The DISPLAY screen uses a laxer 3-year floor: many large caps' earnings-yield
# snapshot (from which P/E is derived) lags their balance-sheet by a year, so a
# 2-year floor silently drops most recognizable names (e.g. only 1 large-cap
# auto name survived). 3 years recovers them while still excluding dead shells.
_SCREEN_RECENCY_YEARS = 3


def _default_min_period_end() -> date:
    today = date.today()
    return date(today.year - _RECENCY_YEARS, 1, 1)


def _screen_min_period_end() -> date:
    today = date.today()
    return date(today.year - _SCREEN_RECENCY_YEARS, 1, 1)


def _normalise_field(field: str) -> str:
    f = (field or "").strip().lower()
    return _FIELD_ALIASES.get(f, f)


def _sector_for_slug(slug: str | None) -> str | None:
    if not slug:
        return None
    for pat, sector in _SLUG_SECTOR_RULES:
        if pat.match(slug):
            return sector
    return None


def _resolve_item_key(tok: str) -> str | None:
    """Resolve a user token to a FIELD_MAP raw line-item key (for custom ratios).
    Accepts FIELD_MAP keys, screener-field aliases, and a few ratio→source maps."""
    t = (tok or "").strip().lower()
    if not t:
        return None
    if t in FIELD_MAP:
        return t
    t2 = _FIELD_ALIASES.get(t, t)
    if t2 in FIELD_MAP:
        return t2
    t2 = {"de": "debt_to_equity", "payout": "dividend_payout",
          "pe": "earnings_yield"}.get(t2, t2)
    return t2 if t2 in FIELD_MAP else None


def _matches_exclude_term(row: dict, term: str) -> bool:
    """Coarse, conservative match of one user-stated carve-out against a
    screen result row — mirrors strategy_builder._apply_exclusions so a raw
    screen and a build_strategy basket honour the same "exclude X"
    vocabulary. Matches an exact ticker, a substring of the company name
    (catches "Adani" -> every Adani-group name), a sector-name substring, or
    (for the literal "psu" term) the shared PSU membership set — since real
    PSUs span energy/metals/defence sectors, not just a "psu" sector label.
    """
    t = (term or "").strip().lower()
    if not t:
        return False
    sym = str(row.get("symbol") or "").strip().lower()
    name = str(row.get("name") or "").strip().lower()
    sec = str(row.get("sector") or "").strip().lower()
    if t == sym or (name and t in name):
        return True
    if t == "psu":
        from backend.services.sector_universe import is_psu
        if is_psu(row.get("symbol")):
            return True
    if sec and t in sec:
        return True
    return False


def _apply_exclude(result: dict, exclude: list[str] | None) -> dict:
    """Hard-filter a screen result against user-stated carve-outs (a named
    ticker/company, a sector word, or "PSU"). Returns ``result`` unchanged
    when there's nothing to exclude or nothing was dropped; otherwise a
    shallow copy with `results`/`count` trimmed and the drop disclosed in
    `note` (never a silent filter)."""
    terms = [str(t) for t in (exclude or []) if str(t).strip()]
    if not terms or not result.get("results"):
        return result
    kept = [r for r in result["results"] if not any(_matches_exclude_term(r, t) for t in terms)]
    dropped = len(result["results"]) - len(kept)
    if not dropped:
        return result
    out = dict(result)
    out["results"] = kept
    out["count"] = len(kept)
    excl_note = f"excluded per your stated preference: dropped {dropped} name(s) matching {', '.join(terms)}"
    out["note"] = f"{result.get('note')}; {excl_note}" if result.get("note") else excl_note
    return out


def screen_by_fundamentals(
    filters: list[dict],
    sector: str | None = None,
    sort_by: dict | None = None,
    limit: int = 15,
    *,
    market_cap_tier: str | None = None,
    custom_ratios: list[dict] | None = None,
    min_period_end: date | None | str = "default",
    exclude: list[str] | None = None,
    session: Session | None = None,
) -> dict:
    """Return companies passing every fundamental constraint in `filters`.

    Parameters
    ----------
    filters
        List of `{"field", "op", "value"}`. `field` is any metric the DB
        carries — the ratio set (pe/peg/roe/roce/de/payout/price_to_book/
        ev_to_ebitda/roa/current_ratio/quick_ratio/interest_coverage/
        net_profit_margin/ebitda_margin/asset_turnover), the growth set
        (revenue_growth/net_profit_growth/eps_growth), the raw line items
        (revenue/net_profit/operating_profit/eps_basic/total_debt/
        total_equity/reserves/cash_from_ops/book_value_per_share/
        enterprise_value_cr), market_cap (₹ crore, enrich-backed), or a
        `custom_ratios` name. Aliases accepted. `op` ∈ < <= > >= =.
    sector
        Optional coarse sector ("pharma", "bank", "it", "energy", "auto",
        "metal", "finance", ...). Matched against industry_slug prefixes
        because mc.companies.sector is NULL.
    sort_by
        `{"field": <screen field>, "dir": "asc"|"desc"}`. Defaults to the
        first filter field, descending. The sort field is automatically
        included in the SELECT so it can be ordered on.
    limit
        Max rows returned (1..100). Honour the user's "top N" here.
    custom_ratios
        Optional list of `{"name", "numerator", "denominator"}` defining
        derived metrics (num/den are raw line items). Each becomes a field
        usable in `filters`/`sort_by` by its `name` — e.g.
        `{"name":"debt_ebitda","numerator":"total_debt","denominator":"operating_profit"}`.
    min_period_end
        Recency floor on the latest filing period. "default" -> ~2 fiscal
        years back; None -> no floor (includes dormant shells); or pass an
        explicit `date`.
    exclude
        Optional carve-outs (named ticker/company, sector word, or "PSU")
        hard-filtered out of the result AFTER ranking — see
        :func:`_apply_exclude`. Never silently dropped; a drop is disclosed
        in the returned `note`.

    Returns
    -------
    dict with keys: count, results, applied_filters, note. Each result row
    carries symbol, name, sector, and every screened/sorted metric value.
    Never fabricates: a metric the DB can't serve is dropped from the filter
    set and explained in `note`.
    """
    limit = max(1, min(int(limit), 100))
    tier = _CAP_TIER_ALIASES.get((market_cap_tier or "").strip().lower())

    if min_period_end == "default":
        floor: date | None = _screen_min_period_end()
    elif isinstance(min_period_end, str):
        floor = date.fromisoformat(min_period_end)
    else:
        floor = min_period_end  # date or None

    notes: list[str] = []

    # ── 0. Register custom ratios as first-class fields for this call ────
    # A per-call copy of the field table so custom ratios (num/den over raw
    # line items) are validated, CTE-built, filtered and sorted exactly like
    # a built-in metric — this is the "make your own ratio" capability.
    field_defs: dict[str, dict] = dict(_FIELD_DEFS)
    for cr in custom_ratios or []:
        name = _normalise_field(cr.get("name") or "")
        num = _resolve_item_key(cr.get("numerator") or "")
        den = _resolve_item_key(cr.get("denominator") or "")
        if not name or not num or not den:
            notes.append(
                f"custom ratio {cr.get('name')!r} skipped (need name + "
                "numerator/denominator that resolve to DB line items)"
            )
            continue
        if name in field_defs and field_defs[name].get("kind") != "ratio":
            notes.append(f"custom ratio name {name!r} clashes a built-in — skipped")
            continue
        field_defs[name] = {
            "kind": "ratio",
            "num_items": list(FIELD_MAP[num][1]),
            "den_items": list(FIELD_MAP[den][1]),
            "label": (cr.get("name") or name).strip(),
            "unit": "x",
        }

    # ── 1. Validate + normalise filters ─────────────────────────────────
    valid_filters: list[dict] = []
    for raw in filters or []:
        field = _normalise_field(raw.get("field", ""))
        op = (raw.get("op") or "").strip()
        if field not in field_defs:
            notes.append(f"unknown field {raw.get('field')!r} skipped")
            continue
        if op not in _ALLOWED_OPS:
            notes.append(f"unsupported op {raw.get('op')!r} on {field} skipped")
            continue
        defn = field_defs[field]
        if defn["kind"] == "unsupported":
            notes.append(f"{field} not available in this DB — filter skipped")
            continue
        # Field-vs-field comparison ("operating cash flow exceeds net
        # profit"): `value_field` names the right-hand metric instead of
        # a number. Both sides build their metric CTE like any filter.
        vf_raw = raw.get("value_field")
        if vf_raw:
            vf = _normalise_field(str(vf_raw))
            if vf not in field_defs or field_defs[vf]["kind"] == "unsupported":
                notes.append(f"unknown comparison field {vf_raw!r} skipped")
                continue
            valid_filters.append({"field": field, "op": op, "value_field": vf})
            continue
        try:
            value = float(raw.get("value"))
        except (TypeError, ValueError):
            notes.append(f"non-numeric value for {field} skipped")
            continue
        valid_filters.append({"field": field, "op": op, "value": value})

    # ── 2. Determine the sort field. Supports a SORT-ONLY screen where
    # the user named no hard threshold — we RANK instead of hard-filter:
    #   "cheap banking stocks"      -> sector=bank, sort pe asc
    #   "best dividend payers"      -> sort payout desc
    #   "highest quality IT names"  -> sort roe desc
    # so a vague-but-real ask returns a list instead of a clarifier.
    sort_field = None
    sort_dir = "desc"
    if sort_by:
        sf = _normalise_field(sort_by.get("field", ""))
        sd = (sort_by.get("dir") or "desc").strip().lower()
        if sf in field_defs and field_defs[sf]["kind"] != "unsupported":
            sort_field = sf
            sort_dir = "asc" if sd == "asc" else "desc"
        elif sf:
            notes.append(f"cannot sort by {sf!r}")
    if sort_field is None and valid_filters:
        # DETERMINISTIC default rank, INDEPENDENT of the order the caller listed
        # the filters in. Previously this took `valid_filters[0]` — so the SAME
        # three-filter screen ranked differently depending only on the order the
        # LLM happened to emit the filters, which is a chunk of the "same prompt,
        # three different answers" the user reported. Rule: rank by market cap
        # when it's one of the filters (a neutral size ordering), else by a fixed
        # metric priority, else alphabetically — never by emission order.
        ff = {f["field"] for f in valid_filters}
        if "market_cap" in ff:
            sort_field = "market_cap"
        else:
            sort_field = next(
                (m for m in _DEFAULT_SORT_PRIORITY if m in ff), sorted(ff)[0]
            )
        sort_dir = "desc"
        notes.append(
            f"no sort given — ranked by {_METRIC_LABELS.get(sort_field, sort_field)} "
            "(largest/highest first)"
        )
    if sort_field is None and sector:
        # A bare sector ask with no metric/sort named ("best bank
        # stocks") used to default to ROE desc — that silently bakes in
        # a "quality" opinion the user never asked for (reported
        # 2026-07-14). Market cap is a neutral, non-judgmental ordering
        # (size/recognizability, not a ratio-based investment call) —
        # the same floor `apply_default_floor` already uses to surface
        # recognizable large-caps for exactly this kind of bare ask.
        # An explicit quality ask ("highest quality IT names") still
        # sorts by ROE via the `sort_by` param above — untouched.
        sort_field = "market_cap"
        sort_dir = "desc"
        notes.append("no metric given — ranked by market cap (largest first)")

    if not valid_filters and sort_field is None:
        return {
            "count": 0,
            "results": [],
            "applied_filters": [],
            "note": "; ".join(notes)
            or "give me a metric (PE/ROE/ROCE/D-E/payout) or a sector to screen",
        }

    metric_fields = list(
        {f["field"] for f in valid_filters}
        | {f["value_field"] for f in valid_filters if f.get("value_field")}
        | {sort_field}
    )

    # ── 2b. Route SECTOR screens to the enrich DB (clean sectors + real P/E) ─
    # when every referenced metric is one enrich serves cleanly (pe/roe/payout).
    # The mc `industry_slug` is too polluted for a recognizable sector list
    # (see _ENRICH_SECTOR_INDUSTRIES). A bare sector ranking (no explicit numeric
    # filter, no cap word) gets a recognizable-name floor so micro-caps don't
    # dominate. Falls through to the mc path on any enrich miss.
    if (_enrich_can_serve(sector, {m for m in metric_fields if m})
            and not any(f.get("value_field") for f in valid_filters)):
        apply_default_floor = (not valid_filters) and (tier is None)
        enr = screen_from_enrich(
            sector=sector,  # type: ignore[arg-type]
            valid_filters=valid_filters,
            sort_field=sort_field,
            sort_dir=sort_dir,
            tier=tier,
            apply_default_floor=apply_default_floor,
            limit=limit,
            notes=list(notes),
        )
        if enr is not None and enr.get("results"):
            return _apply_exclude(enr, exclude)

    # ── 3. Build one CTE per metric (branch by kind) ─────────────────────
    params: dict = {"floor": floor}
    cte_sqls: list[str] = []
    select_cols: list[str] = []
    join_sqls: list[str] = []
    val_expr: dict[str, str] = {}  # metric field -> SQL value expression

    # Real market caps (₹ crore) injected as an in-memory CTE via unnest of two
    # arrays, so market_cap filters/sorts like any numeric metric — same enrich
    # source the Screener TAB uses.
    if "market_cap" in metric_fields:
        caps = _load_market_caps()
        if caps:
            params["cap_ids"] = list(caps.keys())
            params["cap_crs"] = [float(v) for v in caps.values()]
            cte_sqls.append(
                "caps AS (SELECT sc_id, cr FROM "
                "unnest(:cap_ids ::text[], :cap_crs ::float8[]) AS t(sc_id, cr))"
            )
            join_sqls.append("JOIN caps ON caps.sc_id = c.sc_id")
            select_cols.append("caps.cr AS val_market_cap")
            val_expr["market_cap"] = "caps.cr"
        else:
            notes.append("market cap unavailable (enrich DB down) — cap constraint skipped")
            valid_filters = [f for f in valid_filters if f["field"] != "market_cap"]
            if sort_field == "market_cap":
                sort_field = valid_filters[0]["field"] if valid_filters else "roe"
            metric_fields = list(
                {f["field"] for f in valid_filters}
                | {f["value_field"] for f in valid_filters if f.get("value_field")}
                | {sort_field}
            )
    else:
        # Market cap isn't screened here — still surface it as a CONTEXT
        # column (LEFT JOIN, never filters or reorders): size is the one
        # number that gives the reader an instant gist of every row.
        caps = _load_market_caps()
        if caps:
            params["cap_ids"] = list(caps.keys())
            params["cap_crs"] = [float(v) for v in caps.values()]
            cte_sqls.append(
                "caps AS (SELECT sc_id, cr FROM "
                "unnest(:cap_ids ::text[], :cap_crs ::float8[]) AS t(sc_id, cr))"
            )
            join_sqls.append("LEFT JOIN caps ON caps.sc_id = c.sc_id")
            select_cols.append("caps.cr AS ctx_mcap_cr")

    # 1-year return as a second CONTEXT column (LEFT JOIN, never filters).
    yr1 = _load_52w_change()
    if yr1:
        params["yr1_ids"] = list(yr1.keys())
        params["yr1_pcts"] = [float(v) for v in yr1.values()]
        cte_sqls.append(
            "yr1 AS (SELECT sc_id, pct FROM "
            "unnest(:yr1_ids ::text[], :yr1_pcts ::float8[]) AS t(sc_id, pct))"
        )
        join_sqls.append("LEFT JOIN yr1 ON yr1.sc_id = c.sc_id")
        select_cols.append("yr1.pct AS ctx_yr1_pct")
        notes.append("1-year return from the yfinance enrichment snapshot (may lag)")

    # Real trailing P/E (enrich) injected as an in-memory CTE and PREFERRED over
    # the 1/Earnings-Yield derivation (which quantizes because MC stores EY at
    # 2 dp). LEFT JOIN so it supplies a value only where enrich has the name; the
    # Earnings-Yield CTE below stays the fallback for the rest.
    pe_real_available = False
    if "pe" in metric_fields:
        pe_map = _load_trailing_pe()
        if pe_map:
            params["pe_ids"] = list(pe_map.keys())
            params["pe_vals"] = [float(v) for v in pe_map.values()]
            cte_sqls.append(
                "pe_real AS (SELECT sc_id, pe FROM "
                "unnest(:pe_ids ::text[], :pe_vals ::float8[]) AS t(sc_id, pe))"
            )
            join_sqls.append("LEFT JOIN pe_real ON pe_real.sc_id = c.sc_id")
            pe_real_available = True

    for i, mf in enumerate(metric_fields):
        if mf == "market_cap":
            continue  # handled above
        defn = field_defs[mf]
        kind = defn["kind"]
        cte_name = f"m_{mf}"

        if kind == "growth":
            # YoY over the two latest ANNUAL periods of the SAME basis
            # (consolidated preferred). The recency floor gates the LATEST
            # period only (the prior year is allowed to precede it).
            # NOTE: no `period_kind='annual'` filter — 100% of statement_lines
            # rows are annual, so that predicate is a no-op that (because
            # period_kind isn't in the covering index) forced a 42k-block heap
            # scan and made growth screens take ~43s. Dropping it lets the
            # query run index-only off statement_lines_screen_cov_idx.
            items_key = f"items_{i}"
            params[items_key] = defn["items"]
            cte_sqls.append(
                f"""{cte_name} AS MATERIALIZED (
                    WITH base_raw AS (
                        SELECT DISTINCT ON (sl.sc_id, sl.basis, sl.period_end)
                               sl.sc_id, sl.basis, sl.value_numeric AS v,
                               sl.period_end
                        FROM mc.statement_lines sl
                        WHERE sl.line_item = ANY(:{items_key})
                          AND sl.value_numeric IS NOT NULL
                          AND sl.period_end IS NOT NULL
                          AND (sl.statement <> 'ratios' OR sl.source IN ('mc_html', 'mc_api'))
                        ORDER BY sl.sc_id, sl.basis, sl.period_end DESC,
                                 array_position(CAST(:{items_key} AS text[]), sl.line_item)
                    ),
                    base AS (
                        SELECT sc_id, basis, v, period_end,
                               row_number() OVER (
                                   PARTITION BY sc_id, basis
                                   ORDER BY period_end DESC NULLS LAST) AS rn
                        FROM base_raw
                    ),
                    paired AS (
                        SELECT b1.sc_id, b1.basis, b1.period_end AS latest_end,
                               CASE WHEN b2.v <> 0
                                    THEN (b1.v - b2.v) / abs(b2.v) * 100.0 END AS g
                        FROM base b1 JOIN base b2
                          ON b1.sc_id = b2.sc_id AND b1.basis = b2.basis
                         AND b1.rn = 1 AND b2.rn = 2
                    )
                    SELECT DISTINCT ON (sc_id) sc_id, g AS v
                    FROM paired
                    WHERE g IS NOT NULL
                      AND (:floor IS NULL OR latest_end >= :floor)
                    ORDER BY sc_id, (basis = 'consolidated') DESC
                )"""
            )
            join_sqls.append(f"JOIN {cte_name} ON {cte_name}.sc_id = c.sc_id")
            select_cols.append(f"{cte_name}.v AS val_{mf}")
            val_expr[mf] = f"{cte_name}.v"

        elif kind == "peg":
            # PEG = trailing P/E ÷ trailing YoY EPS growth (% as a plain
            # number). Composes a "pe_from_ey"-shaped CTE with a
            # "growth"-shaped CTE (own copy, not shared, so this addition
            # can't regress the standalone growth kind above) — see those
            # patterns for the query-shape rationale. Only defined when
            # growth is positive (PEG on flat/declining earnings isn't a
            # meaningful ratio) and P/E is positive. Dedupes to ONE row per
            # (sc_id, basis, period_end) before pairing periods, same as
            # `financials_db.get_fundamental_history`'s synonym-priority
            # tiebreak, so a company with multiple EPS-line synonyms for the
            # same year can't get paired across two different line items.
            pe_key, gr_key = f"peg_pe_{i}", f"peg_gr_{i}"
            params[pe_key] = defn["pe_items"]
            params[gr_key] = defn["growth_items"]
            pe_cte, gr_cte = f"{cte_name}_pe", f"{cte_name}_gr"
            cte_sqls.append(
                f"""{pe_cte} AS MATERIALIZED (
                    SELECT DISTINCT ON (sl.sc_id)
                           sl.sc_id, sl.value_numeric AS v
                    FROM mc.statement_lines sl
                    WHERE sl.line_item = ANY(:{pe_key})
                      AND sl.value_numeric IS NOT NULL
                      AND sl.value_numeric > 0 AND sl.value_numeric <= 1.0
                      AND (:floor IS NULL OR sl.period_end >= :floor)
                      AND (sl.statement <> 'ratios' OR sl.source IN ('mc_html', 'mc_api'))
                    ORDER BY sl.sc_id,
                             (sl.basis = 'consolidated') DESC,
                             sl.period_end DESC NULLS LAST,
                             sl.availability_date DESC NULLS LAST
                )"""
            )
            cte_sqls.append(
                f"""{gr_cte} AS MATERIALIZED (
                    WITH base_raw AS (
                        SELECT DISTINCT ON (sl.sc_id, sl.basis, sl.period_end)
                               sl.sc_id, sl.basis, sl.value_numeric AS v,
                               sl.period_end
                        FROM mc.statement_lines sl
                        WHERE sl.line_item = ANY(:{gr_key})
                          AND sl.value_numeric IS NOT NULL
                          AND sl.period_end IS NOT NULL
                          AND (sl.statement <> 'ratios' OR sl.source IN ('mc_html', 'mc_api'))
                        ORDER BY sl.sc_id, sl.basis, sl.period_end DESC,
                                 array_position(CAST(:{gr_key} AS text[]), sl.line_item)
                    ),
                    base AS (
                        SELECT sc_id, basis, v, period_end,
                               row_number() OVER (
                                   PARTITION BY sc_id, basis
                                   ORDER BY period_end DESC NULLS LAST) AS rn
                        FROM base_raw
                    ),
                    paired AS (
                        SELECT b1.sc_id, b1.basis, b1.period_end AS latest_end,
                               CASE WHEN b2.v <> 0
                                    THEN (b1.v - b2.v) / abs(b2.v) * 100.0 END AS g
                        FROM base b1 JOIN base b2
                          ON b1.sc_id = b2.sc_id AND b1.basis = b2.basis
                         AND b1.rn = 1 AND b2.rn = 2
                    )
                    SELECT DISTINCT ON (sc_id) sc_id, g AS v
                    FROM paired
                    WHERE g IS NOT NULL
                      AND (:floor IS NULL OR latest_end >= :floor)
                    ORDER BY sc_id, (basis = 'consolidated') DESC
                )"""
            )
            cte_sqls.append(
                f"""{cte_name} AS MATERIALIZED (
                    SELECT pe.sc_id,
                           CASE WHEN pe.v > 0 AND gr.v > 0
                                THEN (1.0 / pe.v) / gr.v END AS v
                    FROM {pe_cte} pe JOIN {gr_cte} gr ON gr.sc_id = pe.sc_id
                )"""
            )
            join_sqls.append(f"JOIN {cte_name} ON {cte_name}.sc_id = c.sc_id")
            select_cols.append(f"{cte_name}.v AS val_{mf}")
            val_expr[mf] = f"{cte_name}.v"
            notes.append(
                "PEG = trailing P/E ÷ trailing YoY EPS growth (historical "
                "MC filings, not forward estimates) — undefined/excluded "
                "when EPS growth isn't positive"
            )

        elif kind == "ratio":
            # Custom ratio = latest numerator ÷ latest denominator per sc_id.
            num_key, den_key = f"num_{i}", f"den_{i}"
            params[num_key] = defn["num_items"]
            params[den_key] = defn["den_items"]
            cte_sqls.append(
                f"""{cte_name} AS MATERIALIZED (
                    SELECT n.sc_id, (n.v / NULLIF(d.v, 0)) AS v
                    FROM (SELECT DISTINCT ON (sl.sc_id) sl.sc_id, sl.value_numeric AS v
                          FROM mc.statement_lines sl
                          WHERE sl.line_item = ANY(:{num_key})
                            AND sl.value_numeric IS NOT NULL
                            AND (:floor IS NULL OR sl.period_end >= :floor)
                            AND (sl.statement <> 'ratios' OR sl.source IN ('mc_html', 'mc_api'))
                          ORDER BY sl.sc_id, (sl.basis='consolidated') DESC,
                                   sl.period_end DESC NULLS LAST) n
                    JOIN (SELECT DISTINCT ON (sl.sc_id) sl.sc_id, sl.value_numeric AS v
                          FROM mc.statement_lines sl
                          WHERE sl.line_item = ANY(:{den_key})
                            AND sl.value_numeric IS NOT NULL
                            AND (:floor IS NULL OR sl.period_end >= :floor)
                            AND (sl.statement <> 'ratios' OR sl.source IN ('mc_html', 'mc_api'))
                          ORDER BY sl.sc_id, (sl.basis='consolidated') DESC,
                                   sl.period_end DESC NULLS LAST) d
                      ON d.sc_id = n.sc_id
                )"""
            )
            join_sqls.append(f"JOIN {cte_name} ON {cte_name}.sc_id = c.sc_id")
            select_cols.append(f"{cte_name}.v AS val_{mf}")
            val_expr[mf] = f"{cte_name}.v"

        else:
            # direct ratio / raw line item, OR pe_from_ey: latest annual value.
            items_key = f"items_{i}"
            params[items_key] = defn["items"]
            extra = ""
            if kind == "pe_from_ey":
                extra = "AND sl.value_numeric > 0 AND sl.value_numeric <= 1.0"
            cte_sqls.append(
                f"""{cte_name} AS MATERIALIZED (
                    SELECT DISTINCT ON (sl.sc_id)
                           sl.sc_id, sl.value_numeric AS v
                    FROM mc.statement_lines sl
                    WHERE sl.line_item = ANY(:{items_key})
                      AND sl.value_numeric IS NOT NULL
                      {extra}
                      AND (:floor IS NULL OR sl.period_end >= :floor)
                      AND (sl.statement <> 'ratios' OR sl.source IN ('mc_html', 'mc_api'))
                    ORDER BY sl.sc_id,
                             (sl.basis = 'consolidated') DESC,
                             sl.period_end DESC NULLS LAST,
                             sl.availability_date DESC NULLS LAST,
                             array_position(CAST(:{items_key} AS text[]), sl.line_item)
                )"""
            )
            join_sqls.append(f"JOIN {cte_name} ON {cte_name}.sc_id = c.sc_id")
            if kind == "pe_from_ey":
                ey_pe = f"(CASE WHEN {cte_name}.v <> 0 THEN 1.0/{cte_name}.v END)"
                pe_val = f"COALESCE(pe_real.pe, {ey_pe})" if pe_real_available else ey_pe
                select_cols.append(f"{pe_val} AS val_{mf}")
                val_expr[mf] = pe_val
            else:
                select_cols.append(f"{cte_name}.v AS val_{mf}")
                val_expr[mf] = f"{cte_name}.v"

    # ── 4. WHERE clause from the filters ─────────────────────────────────
    where_parts: list[str] = ["c.is_active"]
    for j, f in enumerate(valid_filters):
        mf = f["field"]
        defn = field_defs[mf]
        val_param = f"val_{j}"
        if f.get("value_field"):
            # Field-vs-field: compare the two metric expressions directly.
            # A P/E side gets a > 0 guard so negative-earnings names can't
            # slip through comparisons like "P/E below growth rate".
            lhs, rhs = val_expr[mf], val_expr[f["value_field"]]
            cond = f"{lhs} {f['op']} {rhs}"
            if defn["kind"] == "pe_from_ey":
                cond = f"{lhs} > 0 AND {cond}"
            if field_defs[f["value_field"]]["kind"] == "pe_from_ey":
                cond = f"{rhs} > 0 AND {cond}"
            where_parts.append(cond)
            continue
        if defn["kind"] == "pe_from_ey":
            # Filter on the SAME P/E value we display and rank (real trailing P/E
            # where enrich has it, else 1/EY) so the filter and the shown number
            # can never disagree at the boundary. `> 0` guards the derived-null.
            if f["value"] <= 0:
                notes.append("pe comparison against a non-positive value skipped")
                continue
            params[val_param] = f["value"]
            pe_val = val_expr[mf]
            where_parts.append(f"{pe_val} > 0 AND {pe_val} {f['op']} :{val_param}")
            if pe_real_available and not any("P/E is trailing" in n for n in notes):
                notes.append(
                    "P/E is trailing (live price ÷ TTM EPS) where available, "
                    "else derived from MC Earnings Yield"
                )
            elif not pe_real_available and not any("P/E derived" in n for n in notes):
                notes.append(
                    "P/E derived from MC Earnings Yield (2-dp) — values are "
                    "quantized and may display on the filter boundary"
                )
        else:
            params[val_param] = f["value"]
            where_parts.append(f"{val_expr[mf]} {f['op']} :{val_param}")

    # ── 5. Sector filter via industry_slug prefixes ─────────────────────
    if sector:
        sec = sector.strip().lower()
        prefixes = _SECTOR_SLUG_PREFIXES.get(sec)
        if prefixes:
            params["sector_prefixes"] = prefixes
            where_parts.append(
                f"{_corrected_industry_slug_sql()} ILIKE ANY(:sector_prefixes)"
            )
        else:
            notes.append(
                f"unknown sector {sector!r} (known: "
                f"{', '.join(sorted(_SECTOR_SLUG_PREFIXES))}) — sector filter ignored"
            )

    # ── 5a. Market-cap tier / floor via REAL caps from the enrich DB ─────
    # Restrict by actual market cap (enrich.company_profile.market_cap, keyed by
    # the same sc_id) so "large cap" surfaces every genuine large cap — not just
    # the ~80-name curated whitelist (which, at the recency floor, left
    # "large-cap auto" returning ONE name). Three ways a cap constraint applies:
    #   (1) explicit tier word (large/mid/small) → that tier's ₹-cr range;
    #   (2) a BARE sector ranking (sector + sort, no numeric filter, no tier) →
    #       a recognizable-name floor so micro-caps don't dominate the ranking;
    #   (3) otherwise → no cap constraint (an explicit "PE < 25" wants all matches).
    cap_applied = False
    if tier:
        lo, hi = _CAP_TIER_RANGES[tier]
        cap_sc_ids = _sc_ids_in_cap_range(lo, hi)
        if cap_sc_ids:
            params["cap_sc_ids"] = cap_sc_ids
            where_parts.append("c.sc_id = ANY(:cap_sc_ids)")
            cap_applied = True
            rng = (
                f"≥ ₹{lo:,.0f} Cr" if hi is None
                else (f"< ₹{hi:,.0f} Cr" if lo is None
                      else f"₹{lo:,.0f}–{hi:,.0f} Cr")
            )
            notes.append(f"restricted to {tier}-cap ({rng} market cap, ~{len(cap_sc_ids)} names)")
        elif tier in ("large", "mid"):
            # Enrich unavailable → fall back to the curated symbol whitelist.
            syms = _LARGE_CAP_SYMS if tier == "large" else (_LARGE_CAP_SYMS | _MID_CAP_SYMS)
            params["cap_syms"] = list(syms)
            where_parts.append("UPPER(COALESCE(c.nse_symbol, c.ticker)) = ANY(:cap_syms)")
            cap_applied = True
            notes.append(f"restricted to curated {tier}-cap universe (~{len(syms)} names)")
        else:
            notes.append("small-cap filter is approximate — cap not strictly enforced")
    if (not cap_applied and sector and not valid_filters):
        # Bare "best/cheapest in <sector>" ask — floor out the micro-caps the
        # user didn't mean, so recognizable names lead. Disclosed in the note.
        floor_ids = _sc_ids_in_cap_range(_DEFAULT_SECTOR_FLOOR_CR, None)
        if floor_ids:
            params["cap_sc_ids"] = floor_ids
            where_parts.append("c.sc_id = ANY(:cap_sc_ids)")
            notes.append(
                f"showing names above ~₹{_DEFAULT_SECTOR_FLOOR_CR:,} Cr market cap "
                "(say 'include small caps' to widen)"
            )

    # ── 5b. Plausibility bounds — exclude data-quality artifacts ─────────
    # Tiny-equity firms report nonsense ratios (ROE 666%, etc.) that
    # otherwise dominate the ORDER BY and make the screen look broken to
    # a retail user. Bound EVERY metric in play (filter ∪ sort field),
    # not just the explicitly-filtered ones. Ranges are generous so
    # legitimately high-quality names survive; for an explicit large/mid-cap
    # screen we tighten ROE/ROCE further (a genuine large-cap almost never
    # sustains >80% ROE — anything higher is a residual data artifact).
    _PLAUSIBLE = {
        "roe":    "BETWEEN -200 AND 200",
        "roce":   "BETWEEN -200 AND 200",
        "de":     "BETWEEN 0 AND 50",
        "payout": "BETWEEN 0 AND 100",
        # Ratios/margins — tiny-revenue shells report absurd values (net margin
        # 5975%, operating margin 51154%); bound EVERY ratio so a data artifact
        # can't dominate the ORDER BY. Generous so legit outliers survive.
        "roa":                  "BETWEEN -100 AND 100",
        "roic":                 "BETWEEN -100 AND 200",
        # A reported "margin" above 100% (profit > revenue) is essentially always
        # a data artifact — a tiny-revenue shell whose non-operating income
        # dwarfs sales. Cap the upper end so those never top a margin screen.
        "net_profit_margin":    "BETWEEN -100 AND 100",
        "operating_margin":     "BETWEEN -100 AND 100",
        "ebitda_margin":        "BETWEEN -100 AND 100",
        "gross_margin":         "BETWEEN -100 AND 100",
        "current_ratio":        "BETWEEN 0 AND 100",
        "quick_ratio":          "BETWEEN 0 AND 100",
        "interest_coverage":    "BETWEEN -100 AND 2000",
        "asset_turnover":       "BETWEEN 0 AND 50",
        "inventory_turnover":   "BETWEEN 0 AND 3000",
        "receivables_turnover": "BETWEEN 0 AND 3000",
    }
    if tier in ("large", "mid"):
        _PLAUSIBLE = {**_PLAUSIBLE, "roe": "BETWEEN -50 AND 80",
                      "roce": "BETWEEN -50 AND 80"}
    for mf in metric_fields:
        kind = field_defs[mf]["kind"]
        if kind == "pe_from_ey":
            # keep the displayed P/E (real-or-derived) in (0, 500].
            pe_val = val_expr[mf]
            where_parts.append(f"{pe_val} > 0 AND {pe_val} <= 500")
        elif kind == "growth":
            # Base-effect artifacts on tiny prior-year values produce absurd
            # "growth" (a ₹2 Cr → ₹20 Cr shell reads +900%). A real company
            # rarely grows a line item more than ~300% YoY, so cap there — this
            # keeps aggressive-but-plausible growers and drops the shells that
            # otherwise dominate a "fastest-growing" rank.
            where_parts.append(f"{val_expr[mf]} BETWEEN -100 AND 300")
        elif kind == "peg":
            # already >0 by construction (CASE guards pe>0 and growth>0); cap
            # the upper end so a near-zero-growth denominator can't produce an
            # absurd PEG that dominates the ORDER BY.
            where_parts.append(f"{val_expr[mf]} <= 50")
        elif mf in _PLAUSIBLE:
            where_parts.append(f"{val_expr[mf]} {_PLAUSIBLE[mf]}")
    notes.append("data-quality bounds applied (extreme outliers excluded)")

    # ── 5c. Symbol-collision dedup (P5 follow-up, 2026-05-29) ────────────
    # The DB has impostor rows: e.g. "Reliance Infra"/"Reliance Info" carry
    # nse_symbol=NULL but ticker="RELIANCE", so COALESCE(nse_symbol,ticker)
    # makes them display as RELIANCE and (ranked by ROE) surface ABOVE the
    # real Reliance Industries (sc_id RI, nse_symbol="RELIANCE", P/E ~25) as
    # "RELIANCE P/E 2.08" — badly misleading. Keep a row only when it owns a
    # real nse_symbol OR its ticker does NOT impersonate some other company's
    # real nse_symbol. Prefers the canonical nse_symbol holder; legit
    # ticker-only names (no collision) are untouched.
    where_parts.append(
        "(c.nse_symbol IS NOT NULL OR c.ticker NOT IN "
        "(SELECT nse_symbol FROM mc.companies WHERE nse_symbol IS NOT NULL))"
    )

    # ── 6. Assemble + run ────────────────────────────────────────────────
    order_dir = "DESC" if sort_dir == "desc" else "ASC"
    params["lim"] = limit

    sql = f"""
    WITH {", ".join(cte_sqls)}
    SELECT c.sc_id, c.company_name, c.nse_symbol, c.ticker, c.industry_slug,
           {", ".join(select_cols)}
    FROM mc.companies c
    {" ".join(join_sqls)}
    WHERE {" AND ".join(where_parts)}
    ORDER BY val_{sort_field} {order_dir} NULLS LAST, c.sc_id ASC
    LIMIT :lim
    """

    owns = session is None
    s = session or FinancialsSessionLocal()
    try:
        # `.mappings()` → access by column alias, robust to select-col ordering
        # (market_cap's caps CTE is prepended, so positional indexing would drift).
        rows = s.execute(text(sql), params).mappings().fetchall()
    finally:
        if owns:
            s.close()

    # ── 7. Shape results ─────────────────────────────────────────────────
    results: list[dict] = []
    for row in rows:
        nse_symbol = row["nse_symbol"]
        ticker = row["ticker"]
        symbol = nse_symbol or ticker or row["sc_id"]
        _slug = _INDUSTRY_SLUG_OVERRIDES.get(
            (nse_symbol or "").upper(), row["industry_slug"],
        )
        rec: dict = {
            "symbol": symbol,
            "name": row["company_name"],
            "sector": _sector_for_slug(_slug),
        }
        ctx_mcap = row.get("ctx_mcap_cr")
        if ctx_mcap is not None:
            rec["market_cap_cr"] = round(float(ctx_mcap))
        ctx_yr1 = row.get("ctx_yr1_pct")
        if ctx_yr1 is not None:
            rec["one_year_pct"] = round(float(ctx_yr1), 1)
        for mf in metric_fields:
            v = row.get(f"val_{mf}")
            if v is None:
                rec[mf] = None
                continue
            if mf == "market_cap":
                rec["market_cap_cr"] = round(float(v))
                rec["market_cap"] = round(float(v))
            elif field_defs[mf].get("unit") == "cr":
                rec[mf] = round(float(v))  # ₹-crore absolutes: no decimals
            else:
                rec[mf] = round(float(v), 2)
        results.append(rec)

    if floor is not None:
        notes.append(f"latest filing on/after {floor.isoformat()} (recency floor)")
    notes.append("basis: consolidated preferred, else standalone")

    if tier in ("large", "mid") and not results:
        notes.append(
            f"no {tier}-cap names passed these constraints — try relaxing the "
            f"thresholds or dropping the {tier}-cap filter (not degrading to "
            "micro-caps)"
        )

    return _apply_exclude({
        "count": len(results),
        "results": results,
        "applied_filters": valid_filters,
        "sorted_by": {"field": sort_field, "dir": sort_dir},
        "note": "; ".join(notes),
    }, exclude)


# ── Deterministic screen reply (skips the LLM narration hop) ────────────────
# The chat loop's second LLM hop on a screen turn is a ~43k-token re-prefill
# whose only job is to restate the tool's rows as a markdown table — it was
# measured at ~7s warm (and the whole cold-cache tail on a bad day). The rows
# are already exact tool values, so the reply is rendered HERE, verbatim and
# deterministic; chat_service returns it directly and skips the hop.

# Short table-header labels for every screenable field (drives the
# deterministic renderer). Growth/market-cap/raw items are first-class so a
# "positive revenue growth" or "market cap above ₹20,000 Cr" screen renders a
# clean table instead of falling back to the LLM narration hop.
_METRIC_LABELS: dict[str, str] = {
    "pe": "P/E", "peg": "PEG", "roe": "ROE", "roce": "ROCE", "de": "D/E", "payout": "Payout",
    "price_to_book": "P/B", "ev_to_ebitda": "EV/EBITDA", "roa": "ROA",
    "current_ratio": "Current Ratio", "quick_ratio": "Quick Ratio",
    "interest_coverage": "Interest Cover", "net_profit_margin": "Net Margin",
    "ebitda_margin": "EBITDA Margin", "asset_turnover": "Asset Turns",
    "revenue_growth": "Revenue Growth", "net_profit_growth": "Profit Growth",
    "eps_growth": "EPS Growth", "market_cap": "Market Cap",
    "roic": "ROIC", "operating_margin": "Op. Margin", "gross_margin": "Gross Margin",
    "inventory_turnover": "Inventory Turns", "receivables_turnover": "Receivable Turns",
    "revenue": "Revenue", "net_profit": "Net Profit",
    "operating_profit": "Operating Profit", "eps_basic": "EPS", "eps_diluted": "EPS (Diluted)",
    "book_value_per_share": "Book Value/Sh", "enterprise_value_cr": "Enterprise Value",
    "total_debt": "Total Debt", "total_equity": "Total Equity",
    "cash_from_ops": "Cash from Ops", "reserves": "Reserves", "interest_expense": "Interest Exp.",
}
# Full-word labels for the section HEADING (the table columns stay short — a
# heading like "Information Technology — ranked by Return on Equity" reads far
# better than "It — ranked by ROE").
_METRIC_FULL_LABELS: dict[str, str] = {
    "pe": "Price-to-Earnings", "peg": "PEG (Growth-Adjusted P/E)",
    "roe": "Return on Equity", "roce": "Return on Capital Employed",
    "de": "Debt-to-Equity", "payout": "Dividend Payout",
    "price_to_book": "Price-to-Book", "ev_to_ebitda": "EV/EBITDA",
    "roa": "Return on Assets", "current_ratio": "Current Ratio",
    "quick_ratio": "Quick Ratio", "interest_coverage": "Interest Coverage",
    "net_profit_margin": "Net Profit Margin", "ebitda_margin": "EBITDA Margin",
    "asset_turnover": "Asset Turnover", "revenue_growth": "Revenue Growth",
    "net_profit_growth": "Net Profit Growth", "eps_growth": "EPS Growth",
    "market_cap": "Market Capitalisation", "roic": "Return on Invested Capital",
    "operating_margin": "Operating Margin", "gross_margin": "Gross Margin",
    "inventory_turnover": "Inventory Turnover",
    "receivables_turnover": "Receivables Turnover", "revenue": "Revenue",
    "net_profit": "Net Profit", "operating_profit": "Operating Profit",
    "eps_basic": "Earnings per Share", "eps_diluted": "Diluted EPS",
    "book_value_per_share": "Book Value per Share",
    "enterprise_value_cr": "Enterprise Value", "total_debt": "Total Debt",
    "total_equity": "Total Equity", "cash_from_ops": "Cash from Operations",
    "reserves": "Reserves", "interest_expense": "Interest Expense",
}
# Proper display names for the coarse sector slugs (headings, not slugs).
_SECTOR_DISPLAY: dict[str, str] = {
    "auto": "Automobiles", "auto ancillary": "Auto Ancillaries",
    "bank": "Banking", "chemicals": "Chemicals", "energy": "Energy",
    "finance": "Financial Services", "fmcg": "FMCG", "infra": "Infrastructure",
    "it": "Information Technology", "metal": "Metals & Mining",
    "pharma": "Pharmaceuticals", "textiles": "Textiles",
}
# Fields displayed as a percent.
_PCT_METRICS = frozenset({
    "roe", "roce", "payout", "roa", "net_profit_margin", "ebitda_margin",
    "revenue_growth", "net_profit_growth", "eps_growth",
    "roic", "operating_margin", "gross_margin",
})
# Fields displayed as ₹-crore absolutes.
_CR_METRICS = frozenset({
    "market_cap", "revenue", "net_profit", "operating_profit", "total_debt",
    "total_equity", "cash_from_ops", "reserves", "enterprise_value_cr",
    "interest_expense",
})

# (field, dir) -> (headline word for the #1 row, one-line framing).
_RANK_FRAMES: dict[tuple[str, str], tuple[str, str]] = {
    ("pe", "asc"): ("Cheapest", "This is a valuation screen, not a quality "
                    "ranking — a lower P/E only means cheaper on earnings, "
                    "not automatically better."),
    ("pe", "desc"): ("Richest-valued", "Ranked most-expensive-first on "
                     "earnings — a high P/E can mean growth expectations or "
                     "overvaluation."),
    ("peg", "asc"): ("Cheapest on growth-adjusted P/E", "PEG = trailing P/E "
                     "÷ trailing YoY EPS growth — below 1 is the classic "
                     "'cheap relative to growth' heuristic, not a buy list."),
    ("peg", "desc"): ("Richest on growth-adjusted P/E", "PEG = trailing P/E "
                      "÷ trailing YoY EPS growth, ranked highest first."),
    ("roe", "desc"): ("Highest ROE", "Ranked by return on equity — a "
                      "profitability screen, not a buy list."),
    ("roce", "desc"): ("Highest ROCE", "Ranked by return on capital employed "
                       "— a capital-efficiency screen, not a buy list."),
    ("de", "asc"): ("Least levered", "Ranked by lowest debt-to-equity — a "
                    "balance-sheet screen, not a buy list."),
    ("payout", "desc"): ("Highest payout", "Ranked by dividend payout ratio "
                         "(share of profit paid out), not dividend yield."),
    ("revenue_growth", "desc"): ("Fastest revenue growth", "Ranked by YoY "
                       "revenue growth (latest two annual filings) — a growth "
                       "screen, not a buy list."),
    ("net_profit_growth", "desc"): ("Fastest profit growth", "Ranked by YoY "
                       "net-profit growth (latest two annual filings) — a "
                       "growth screen, not a buy list."),
    ("eps_growth", "desc"): ("Fastest EPS growth", "Ranked by YoY EPS growth "
                       "(latest two annual filings) — a growth screen, not a "
                       "buy list."),
    ("market_cap", "desc"): ("Largest", "Ranked by market cap (₹ crore, "
                       "current) — a size screen, not a buy list."),
    ("market_cap", "asc"): ("Smallest", "Ranked smallest-first by market cap "
                       "(₹ crore, current)."),
}


def _inr_cr(v: float | int | None) -> str:
    """Indian-grouped ₹-crore ('₹2,81,115 Cr')."""
    if v is None:
        return "—"
    iv = int(round(float(v)))
    sign = "-" if iv < 0 else ""
    n = f"{abs(iv):,}"
    # Re-group western 1,234,567 → Indian 12,34,567.
    digits = n.replace(",", "")
    if len(digits) > 3:
        head, tail = digits[:-3], digits[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        n = ",".join(parts + [tail])
    return f"₹{sign}{n} Cr"


def _fmt_metric(field: str, v: float | None) -> str:
    if v is None:
        return "—"
    if field in _CR_METRICS:
        return _inr_cr(v)          # ₹-crore absolutes (incl. market cap)
    if field in _PCT_METRICS:
        return f"{v:+.2f}%" if field.endswith("_growth") else f"{v:.2f}%"
    return f"{v:.2f}"


def render_screen_markdown(data: dict) -> str | None:
    """Render a screen result as the final chat reply (markdown), or None when
    the result isn't cleanly renderable (empty → let the LLM narrate the note).
    Quotes tool values verbatim — never derives or invents."""
    results = data.get("results") or []
    if not results:
        return None
    sb = data.get("sorted_by") or {}
    field = sb.get("field")
    if field not in _METRIC_LABELS:
        return None
    dir_ = "asc" if (sb.get("dir") or "desc") == "asc" else "desc"
    label = _METRIC_LABELS[field]
    full_label = _METRIC_FULL_LABELS.get(field, label)
    head_word, framing = _RANK_FRAMES.get(
        (field, dir_),
        (f"Top by {full_label}",
         f"Ranked by {full_label} ({'lowest first' if dir_ == 'asc' else 'highest first'})."),
    )

    # Only title by sector when the WHOLE result set shares one (a real sector
    # screen) — otherwise "Banking —" would mislead on an unsectored screen whose
    # #1 row happens to be a bank. Full words in the heading, never slugs.
    secs = {(r.get("sector") or "").strip() for r in results}
    sector = next(iter(secs)) if len(secs) == 1 and "" not in secs else ""
    sector_disp = _SECTOR_DISPLAY.get(sector, sector.replace("_", " ").title())
    title = (f"{sector_disp} — Ranked by {full_label}"
             if sector else f"Fundamental Screen — Ranked by {full_label}")

    # Column order: Rank · Company · Market cap · screened metrics (ranked
    # first) · Sector · 1-Year Return. Market cap / sector / 1-year return are
    # ALWAYS-ON context columns; each is deduped when it's itself the screened
    # thing (mcap drops out of the metric list, a single-sector screen carries
    # sector in the heading instead of a column).
    extra_metrics = [m for m in _METRIC_LABELS
                     if m != field and m != "market_cap"
                     and results[0].get(m) is not None]
    metrics_cols = ([field] if field != "market_cap" else []) + extra_metrics
    show_mcap = any(r.get("market_cap_cr") is not None for r in results)
    show_sector = not sector and any((r.get("sector") or "").strip() for r in results)
    show_yr = any(r.get("one_year_pct") is not None for r in results)

    cols = ["Rank", "Company"]
    aligns = ["---:", "---"]
    if show_mcap:
        cols.append("Market cap")
        aligns.append("---:")
    cols += [_METRIC_LABELS[m] for m in metrics_cols]
    aligns += ["---:"] * len(metrics_cols)
    if show_sector:
        cols.append("Sector")
        aligns.append("---")
    if show_yr:
        cols.append("1-Year Return")
        aligns.append("---:")

    lines = [f"## {title}", "", framing, ""]
    filt = data.get("applied_filters") or []
    if filt:
        shown = " · ".join(
            f"{_METRIC_LABELS.get(f['field'], f['field'])} {f['op']} "
            + (_METRIC_LABELS.get(f["value_field"], f["value_field"])
               if f.get("value_field") else f"{f['value']:g}")
            for f in filt if f.get("field") in _METRIC_LABELS
        )
        if shown:
            lines += [f"Filters applied: {shown}", ""]
    lines.append("| " + " | ".join(cols) + " |")
    lines.append("|" + "|".join(aligns) + "|")
    for i, r in enumerate(results, 1):
        row = [str(i), f"{r.get('name') or r['symbol']} (`{r['symbol']}`)"]
        if show_mcap:
            row.append(_inr_cr(r.get("market_cap_cr")))
        row += [_fmt_metric(m, r.get(m)) for m in metrics_cols]
        if show_sector:
            rs = (r.get("sector") or "").strip()
            row.append(_SECTOR_DISPLAY.get(rs, rs.replace("_", " ").title()) or "—")
        if show_yr:
            v = r.get("one_year_pct")
            row.append(f"{v:+.1f}%" if v is not None else "—")
        lines.append("| " + " | ".join(row) + " |")

    top = results[0]
    top_val = _fmt_metric(field, top.get(field))
    top_name = top.get("name") or top["symbol"]
    if field == "pe":
        top_line = (f"**{head_word}:** {top_name} (`{top['symbol']}`) at "
                    f"{top_val}× P/E.")
    else:
        top_line = (f"**{head_word}:** {top_name} (`{top['symbol']}`) at "
                    f"{top_val} {label}.")

    # Deterministic supporting sentence from the ACTUAL rows (never invented):
    # the spread of the ranked metric across the names shown, so the reader gets
    # a sense of the range, not just the leader.
    vals = [r.get(field) for r in results if r.get(field) is not None]
    spread = ""
    if len(vals) >= 2:
        lo, hi = min(vals), max(vals)
        spread = (f" Across the {len(results)} names shown, {full_label.lower()} "
                  f"ranges from {_fmt_metric(field, lo)} to {_fmt_metric(field, hi)}.")
    lines += ["", top_line + spread]

    # Internal mechanics (sort default, recency floor, basis, bounds) stay in
    # raw_data["note"] for the model and debugging — they are NOT printed:
    # the italic footer read as noise to users (removed on request 2026-07-17).
    # The one user-actionable line survives: how to widen a floored screen.
    note = (data.get("note") or "").strip()
    if "include small caps" in note:
        lines += ["", "_Say “include small caps” to widen this screen._"]
    return "\n".join(lines)


# ── Batch gate-input fetch (latency path for the strategy builder) ──────────
# The cheap quality/value ratios the strategy builder's selection GATE needs
# (roe, roce, de, pe) for an EXPLICIT symbol list, pulled in ONE round-trip.
_GATE_FIELDS: tuple[str, ...] = ("roe", "roce", "de", "pe")


def fetch_gate_inputs(
    symbols: list[str],
    *,
    min_period_end: date | None | str = "default",
    session: Session | None = None,
) -> dict[str, dict[str, float | None]]:
    """Batch-fetch the cheap gate ratios (roe/roce/de/pe) for ``symbols`` in ONE
    SQL round-trip, keyed by UPPER(symbol).

    This is the latency fast-path for ``strategy_builder``: instead of calling
    the per-name :func:`analysis_chat_tools.fetch_fundamentals` (≈9 Azure
    round-trips each) across a ~30-name pre-gate universe, we resolve only the
    four ratios the gate actually reads, for the whole universe, in a single
    statement — then run the cheap gate and call the FULL per-name fetch only on
    the ~8-12 survivors. Output is byte-identical to what the gate would have
    read from the per-name fetcher (same DB CTEs: latest-per-sc_id, consolidated
    basis preferred, same recency floor, same P/E = 1/EarningsYield derivation,
    same data-quality bounds), so this is a pure I/O batching change — the gate
    decision is unchanged.

    Returns ``{SYMBOL: {"roe": .., "roce": .., "de": .., "pe": ..}}``. A symbol
    the DB can't serve is simply absent from the map (never fabricated); the
    caller leaves those ratios ``None`` exactly as the per-name path would.
    """
    syms = [str(s).strip().upper() for s in symbols if str(s).strip()]
    if not syms:
        return {}

    if min_period_end == "default":
        floor: date | None = _default_min_period_end()
    elif isinstance(min_period_end, str):
        floor = date.fromisoformat(min_period_end)
    else:
        floor = min_period_end

    owns = session is None
    s = session or FinancialsSessionLocal()
    try:
        # ── 1. Resolve the symbol list → sc_ids in ONE indexed companies lookup.
        # This is the latency key: it lets the (expensive) statement_lines CTEs
        # below be constrained to ≤30 sc_ids (index range-scan) instead of a
        # DISTINCT-ON scan over the whole ~11k-company statement_lines table.
        # The impostor-row dedup (RELIANCE collision) is applied here so we map
        # each display symbol to the CANONICAL sc_id (nse_symbol holder wins).
        comp_rows = s.execute(
            text(
                """
                SELECT c.sc_id,
                       UPPER(COALESCE(c.nse_symbol, c.ticker)) AS sym,
                       c.nse_symbol
                FROM mc.companies c
                WHERE c.is_active
                  AND UPPER(COALESCE(c.nse_symbol, c.ticker)) = ANY(:syms)
                  AND (c.nse_symbol IS NOT NULL OR c.ticker NOT IN
                       (SELECT nse_symbol FROM mc.companies
                        WHERE nse_symbol IS NOT NULL))
                """
            ),
            {"syms": syms},
        ).fetchall()

        sc_to_sym: dict[str, str] = {}
        sym_has_canonical: dict[str, bool] = {}
        for sc_id, sym, nse_symbol in comp_rows:
            # On a residual collision prefer the canonical nse_symbol holder.
            if sym in sym_has_canonical and sym_has_canonical[sym] and nse_symbol is None:
                continue
            sc_to_sym[str(sc_id)] = sym
            sym_has_canonical[sym] = nse_symbol is not None
        if not sc_to_sym:
            return {}
        sc_ids = list(sc_to_sym)

        # ── 2. One CTE per gate metric, each constrained to our sc_ids ──
        params: dict = {"floor": floor, "sc_ids": sc_ids}
        cte_sqls: list[str] = []
        select_cols: list[str] = []
        for i, mf in enumerate(_GATE_FIELDS):
            defn = _FIELD_DEFS[mf]
            items_key = f"items_{i}"
            params[items_key] = defn["items"]
            cte_name = f"m_{mf}"
            # Same EY guard as screen_by_fundamentals so a sub-1 P/E artifact can't
            # leak into the gate (keeps the batch path identical to the per-name one).
            extra = ""
            if defn["kind"] == "pe_from_ey":
                extra = "AND sl.value_numeric > 0 AND sl.value_numeric <= 1.0"
            cte_sqls.append(
                f"""{cte_name} AS MATERIALIZED (
                    SELECT DISTINCT ON (sl.sc_id)
                           sl.sc_id, sl.value_numeric AS v
                    FROM mc.statement_lines sl
                    WHERE sl.sc_id = ANY(:sc_ids)
                      AND sl.line_item = ANY(:{items_key})
                      AND sl.value_numeric IS NOT NULL
                      {extra}
                      AND (:floor IS NULL OR sl.period_end >= :floor)
                      AND (sl.statement <> 'ratios' OR sl.source IN ('mc_html', 'mc_api'))
                    ORDER BY sl.sc_id,
                             (sl.basis = 'consolidated') DESC,
                             sl.period_end DESC NULLS LAST,
                             sl.availability_date DESC NULLS LAST
                )"""
            )
            if defn["kind"] == "pe_from_ey":
                select_cols.append(
                    f"CASE WHEN {cte_name}.v <> 0 THEN 1.0/{cte_name}.v END AS val_{mf}"
                )
            else:
                select_cols.append(f"{cte_name}.v AS val_{mf}")

        # Driver = the union of sc_ids present in ANY metric CTE; LEFT JOIN each
        # metric onto it so a name present in only some metrics still returns its
        # available ratios (the per-name fetcher is equally partial-tolerant).
        union_ids = " UNION ".join(f"SELECT sc_id FROM m_{mf}" for mf in _GATE_FIELDS)
        join_sqls = [f"LEFT JOIN m_{mf} ON m_{mf}.sc_id = d.sc_id" for mf in _GATE_FIELDS]
        sql = f"""
        WITH {", ".join(cte_sqls)},
             driver AS ({union_ids})
        SELECT d.sc_id, {", ".join(select_cols)}
        FROM driver d
        {" ".join(join_sqls)}
        """
        rows = s.execute(text(sql), params).fetchall()
    finally:
        if owns:
            s.close()

    out: dict[str, dict[str, float | None]] = {}
    for row in rows:
        sc_id = str(row[0]) if row[0] is not None else None
        sym = sc_to_sym.get(sc_id) if sc_id else None
        if sym is None:
            continue
        out[sym] = {
            mf: (round(float(row[1 + i]), 4) if row[1 + i] is not None else None)
            for i, mf in enumerate(_GATE_FIELDS)
        }

    # ── 3. yfinance fallback for symbols the MC batch cannot serve ──
    # Two failure modes converge on the same all-null gate row: (a) the
    # symbol never resolved to any mc.companies sc_id (SBIN has no row at
    # all; ICICIBANK's ticker maps only to impostors filtered out above),
    # and (b) the sc_id has profit_loss data but NO 'ratios' rows (TCS,
    # INFY, HDFCBANK, HINDUNILVR are all in this bucket — verified against
    # the live Azure DB: 0 ratios rows despite hundreds of P&L rows).
    # Neither is fixable by better symbol resolution — we go to yfinance,
    # the same source `routers/financials.py` uses for the single-stock
    # page's ratio-fallback block. Never overwrite an MC value with the
    # yfinance one; only fill genuine nulls.
    missing: list[str] = []
    for sym in syms:
        rec = out.get(sym)
        if rec is None:
            missing.append(sym)
            continue
        if rec.get("pe") is None and rec.get("roe") is None:
            missing.append(sym)

    if not missing:
        return out

    try:
        from concurrent.futures import ThreadPoolExecutor

        from backend.market import yfinance_fundamentals as yff  # lazy: avoid circular import
    except Exception as exc:  # noqa: BLE001
        logger.debug("[fundamentals_screen] yfinance fallback unavailable: %s", exc)
        return out

    def _fetch_one(symbol: str) -> tuple[str, dict[str, float | None] | None]:
        """Pull latest.pe/latest.roe from yfinance. Every failure returns None
        so the caller leaves the symbol's ratios null (honest-null contract).
        The underlying `yff.fetch_fundamentals` is Redis-cached (1h) already —
        we do NOT layer a second cache."""
        try:
            data = yff.fetch_fundamentals(symbol) or {}
            latest = data.get("latest") or {}
            pe_pt = latest.get("pe") or {}
            roe_pt = latest.get("roe") or {}
            pe = pe_pt.get("value") if isinstance(pe_pt, dict) else None
            roe = roe_pt.get("value") if isinstance(roe_pt, dict) else None
            if pe is None and roe is None:
                return symbol, None
            return symbol, {
                "pe": round(float(pe), 4) if pe is not None else None,
                "roe": round(float(roe), 4) if roe is not None else None,
            }
        except Exception as exc:  # noqa: BLE001 — yfinance throws assorted
            logger.debug(
                "[fundamentals_screen] yfinance fallback failed for %s: %s",
                symbol, str(exc)[:120],
            )
            return symbol, None

    # ~130-200 curated names — a serial loop would add seconds of network
    # latency to every cold page. Cap workers to keep it neighbourly to
    # yfinance and Redis; the module-level 1h cache absorbs the rest.
    fallback: dict[str, dict[str, float | None]] = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        for sym, rec in pool.map(_fetch_one, missing):
            if rec is not None:
                fallback[sym] = rec

    for sym, extra in fallback.items():
        existing = out.get(sym)
        if existing is None:
            # Symbol wasn't in the MC batch at all — synthesise a full row
            # with the yfinance-served ratios and honest nulls for roce/de
            # (yfinance doesn't publish ROCE; MC-derived D/E we skip here
            # because it wasn't the reported bug and would need shape
            # translation — leave as null under the honest-null contract).
            out[sym] = {
                "roe": extra.get("roe"),
                "roce": None,
                "de": None,
                "pe": extra.get("pe"),
            }
        else:
            # MC-first: only fill genuine nulls, never overwrite.
            if existing.get("pe") is None and extra.get("pe") is not None:
                existing["pe"] = extra["pe"]
            if existing.get("roe") is None and extra.get("roe") is not None:
                existing["roe"] = extra["roe"]

    return out
