"""Read-only access layer for the Moneycontrol-derived `financials` Postgres DB.

The DB is maintained by `pivot-mc-scraper` (long-format `mc.statement_lines`
plus `mc.companies` and `mc.daily_prices`). This module is the single entry
point used by:
  - agent tools that need fundamentals (P/E, EPS, ROE, ...)
  - the workflow backtester when it replays `fetch.fundamental` steps

Design choices
  - Never writes. The financials DB is curated externally.
  - Reads via `FinancialsSessionLocal` so a slow query can't starve the
    operational `pivot_db` pool.
  - Uses raw SQL (text()) rather than declaring ORM models. The `mc.*`
    schema isn't ours; the scraper owns it.
  - Point-in-time correctness: every fundamentals query takes `as_of_date`
    and filters by `availability_date <= as_of_date` so backtests cannot
    leak future earnings.
  - Curated field map for common identifiers (eps_basic, revenue, ...) plus
    a generic `get_line_item` escape hatch.
  - Safe formula evaluator: when a desired metric isn't in FIELD_MAP, the
    caller (typically the LLM at workflow-build time) emits an arithmetic
    expression over FIELD_MAP keys. `evaluate_formula` parses it with an
    AST whitelist (no calls, no attribute access) and resolves each name
    point-in-time.
"""
from __future__ import annotations

import ast
import math
import time
from collections import defaultdict
from dataclasses import dataclass, asdict
from datetime import date
from typing import Sequence

from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.database import FinancialsSessionLocal


# Curated identifier → (statement, [line_item synonyms]).
# Synonyms exist because Moneycontrol writes the same concept under different
# strings across years and across standalone/consolidated views. List the
# preferred form first; the resolver picks whichever one returns a value.
FIELD_MAP: dict[str, tuple[str, tuple[str, ...]]] = {
    # --- Profit & Loss (annual) ---
    "revenue": (
        "profit_loss",
        (
            "Total Operating Revenues",
            "Revenue From Operations [Net]",
            "Revenue From Operations [Gross]",
            "Total Income From Operations",
            "Revenue From Operations",
            "Net Sales",
            "Net Sales/Income from operations",
            "Income from Operations",
            "Total Revenue",
            # Banks file their top line under these — without them every
            # bank's P&L (HDFC, etc.) came back empty.
            "Total Income",
            "Total Interest Earned",
        ),
    ),
    "net_profit": (
        "profit_loss",
        (
            "Net Profit",
            "Net Profit/(Loss) For the Period",
            "Profit/Loss For The Period",
            "Net Profit After Tax",
            "Profit/(Loss) for the Period",
            # Bank label variants.
            "Net Profit / Loss for The Year",
            "Net Profit/Loss for The Year",
            "Net Profit / Loss After EI & Prior Year Items",
        ),
    ),
    "operating_profit": (
        "profit_loss",
        (
            "Operating Profit",
            "EBITDA",
            "Profit/Loss Before Exceptional, ExtraOrdinary Items And Tax",
            "Profit/Loss Before Tax",
        ),
    ),
    "eps_basic": (
        "profit_loss",
        ("Basic EPS", "Basic EPS.", "Basic EPS (Rs.)"),
    ),
    "eps_diluted": (
        "profit_loss",
        ("Diluted EPS", "Diluted EPS.", "Diluted EPS (Rs.)"),
    ),
    "interest_expense": (
        "profit_loss",
        ("Finance Costs", "Interest", "Finance Cost", "Interest Expended"),
    ),
    # --- Balance Sheet ---
    "total_debt": (
        "balance_sheet",
        (
            "Total Debt",
            "Long Term Borrowings",
            "Total Non-Current Liabilities",
        ),
    ),
    "total_equity": (
        "balance_sheet",
        ("Total Shareholders Funds", "Total Shareholder's Funds", "Total Equity"),
    ),
    "reserves": (
        "balance_sheet",
        ("Reserves and Surplus", "Reserves & Surplus", "Other Reserves"),
    ),
    # --- Cash Flow ---
    "cash_from_ops": (
        "cash_flow",
        (
            "Net CashFlow From Operating Activities",
            "Net Cash from Operating Activities",
            "Cash Flow from Operating Activities",
        ),
    ),
    # --- Ratios (the rich set MC actually exposes) ---
    "roe": (
        "ratios",
        (
            "Return on Networth / Equity (%)",
            "Return on Networth/Equity (%)",
            "Return on Equity (%)",
            # Banks/NBFCs report ROE under these label variants — without
            # them every banking screen returned 0 names (2026-05-29).
            "Return On Equity/Networth (%)",
            "Return on Equity / Networth (%)",
            "Return On Equity / Networth (%)",
            "Return on Equity/Networth (%)",
        ),
    ),
    "roce": (
        "ratios",
        (
            "Return on Capital Employed (%)",
            "Return On Capital Employed (%)",
        ),
    ),
    "roa": (
        "ratios",
        ("Return on Assets (%)",),
    ),
    "debt_to_equity": (
        "ratios",
        ("Total Debt/Equity (X)", "Debt/Equity (X)"),
    ),
    "current_ratio": (
        "ratios",
        ("Current Ratio (X)",),
    ),
    "quick_ratio": (
        "ratios",
        ("Quick Ratio (X)",),
    ),
    "interest_coverage": (
        "ratios",
        ("Interest Coverage Ratios (%)", "Interest Coverage Ratios (Post Tax) (%)"),
    ),
    "net_profit_margin": (
        "ratios",
        ("Net Profit Margin (%)",),
    ),
    "ebitda_margin": (
        "ratios",
        ("PBDIT Margin (%)",),
    ),
    "price_to_book": (
        "ratios",
        ("Price/BV (X)",),
    ),
    "ev_to_ebitda": (
        "ratios",
        ("EV/EBITDA (X)",),
    ),
    "earnings_yield": (
        "ratios",
        # Banks/NBFCs publish it as "Earnings Yield (X)" — without this
        # variant every bank P/E screen (P/E = 1/EY) returned 0.
        ("Earnings Yield", "Earnings Yield (X)"),
    ),
    "dividend_payout": (
        "ratios",
        ("Dividend Payout Ratio (NP) (%)", "Dividend Payout Ratio (CP) (%)"),
    ),
    "book_value_per_share": (
        "ratios",
        (
            "Book Value [ExclRevalReserve]/Share (Rs.)",
            "Book Value [InclRevalReserve]/Share (Rs.)",
        ),
    ),
    "asset_turnover": (
        "ratios",
        ("Asset Turnover Ratio (%)",),
    ),
    "enterprise_value_cr": (
        "ratios",
        ("Enterprise Value (Cr.)",),
    ),
    # ── Extended ratio set (scraped where present + pivot-derived backfill;
    # source='pivot_derived' rows computed from the raw statements) ──
    "roic": (
        "ratios",
        ("Return on Invested Capital (%)",),
    ),
    "operating_margin": (
        "ratios",
        ("PBIT Margin (%)",),
    ),
    "gross_margin": (
        "ratios",
        ("Gross Profit Margin (%)",),
    ),
    "inventory_turnover": (
        "ratios",
        ("Inventory Turnover Ratio (X)",),
    ),
    "receivables_turnover": (
        "ratios",
        ("Debtors Turnover Ratio (X)",),
    ),
    # Per-share figures — the denominators for PRICE ratios computed live
    # as (latest price ÷ per-share value). See compute_price_ratio.
    "net_profit_per_share": (
        "ratios",
        ("Net Profit/Share (Rs.)",),
    ),
    "sales_per_share": (
        "ratios",
        ("Revenue from Operations/Share (Rs.)", "Operating Revenue Per Share"),
    ),
}


@dataclass(frozen=True)
class Company:
    sc_id: str
    name: str
    nse_symbol: str | None
    bse_code: str | None
    ticker: str | None
    sector: str | None
    industry_slug: str | None
    market_cap: float | None
    is_active: bool
    logo_url: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class FundamentalValue:
    sc_id: str
    field: str
    line_item: str
    statement: str
    basis: str
    period_label: str
    period_end: date | None
    availability_date: date | None
    value_numeric: float | None
    value_text: str | None
    unit: str | None

    def to_dict(self) -> dict:
        d = asdict(self)
        if self.period_end:
            d["period_end"] = self.period_end.isoformat()
        if self.availability_date:
            d["availability_date"] = self.availability_date.isoformat()
        return d


@dataclass(frozen=True)
class DailyBar:
    sc_id: str
    trade_date: date
    open: float | None
    high: float | None
    low: float | None
    close: float
    volume: int | None
    adj_factor: float
    source: str

    def to_dict(self) -> dict:
        d = asdict(self)
        d["trade_date"] = self.trade_date.isoformat()
        return d


class UnknownSymbolError(LookupError):
    """Raised when a symbol cannot be resolved to an sc_id."""


def _session() -> Session:
    return FinancialsSessionLocal()


# `resolve_symbol` is called once per FIELD_MAP metric (get_fundamental →
# _get_line_item_value) even within a single fetch_fundamentals() snapshot
# — 8 metrics meant 8 re-runs of the 5-way UNION below (one branch an EXISTS
# subquery against statement_lines), the dominant cost in a ~5.5s single-
# symbol fetch (live-probed 2026-07-14 on a "compare oil majors" turn that
# took 52s end-to-end). The sc_id mapping is static reference data — a
# company doesn't change its sc_id intraday — so cache it in-process rather
# than threading an "already resolved" flag through every caller in the
# resolve_metric/get_fundamental/get_company chain (get_company already
# calls this too). Read-only DB, so a stale-for-a-few-minutes cache is safe;
# TTL just bounds how long a scraper-side rename takes to show up.
_SYMBOL_CACHE_TTL_S = 300
_symbol_cache: dict[str, tuple[str | None, float]] = {}


def resolve_symbol(symbol: str, *, session: Session | None = None) -> str | None:
    """Map a user-facing symbol (NSE ticker, BSE code, sc_id, or name) → sc_id.
    Cached in-process for `_SYMBOL_CACHE_TTL_S` — see module note above."""
    key = (symbol or "").strip().upper()
    if not key:
        return None
    cached = _symbol_cache.get(key)
    if cached is not None and (time.monotonic() - cached[1]) < _SYMBOL_CACHE_TTL_S:
        return cached[0]
    result = _resolve_symbol_uncached(symbol, session=session)
    _symbol_cache[key] = (result, time.monotonic())
    return result


def _resolve_symbol_uncached(symbol: str, *, session: Session | None = None) -> str | None:
    """Order of attempts:
      1. exact sc_id match
      2. exact `ticker` match (case-insensitive)
      3. exact `nse_symbol` match (case-insensitive)
      4. exact `bse_code` match
      5. case-insensitive `company_name` exact match
      6. enrich-DB ticker → sc_id bridge (see below)

    Step 6 exists because `mc.companies.nse_symbol` is NULL on 11,246 of 11,256
    rows and `.ticker` is populated on only ~3,020, so some real trading names
    (e.g. TCS's own row historically had both fields NULL) can't be found by
    the mc-only lookup. The `pivot_enrich` sibling DB was built exactly to
    bridge that gap via an offline yfinance name-matching pass
    (`scripts/map_no_ticker_companies.py`), keyed by `sc_id`. We consult it
    only after the mc-only path returns nothing, and we re-verify the sc_id
    still exists in `mc.companies` because the two DBs live on different
    physical hosts — we can't SQL-join across them. Any hiccup on the enrich
    DB fails soft (like every other branch in this function).

    Returns None when nothing matches — caller decides whether that's fatal.
    """
    owns = session is None
    s = session or _session()
    try:
        sym = symbol.strip()
        if not sym:
            return None
        # Priority: exact sc_id > verified nse_symbol > scraper ticker >
        # bse_code > exact name. nse_symbol OUTRANKS ticker because the
        # scraper `ticker` column is polluted (e.g. Jay Electric and Bharat
        # Hotels both carry ticker='BHEL' while the real BHEL sits under a
        # verified nse_symbol). Within a ticker collision, a row that actually
        # has statement data wins over a fundamentals-less shell.
        row = s.execute(
            text(
                """
                SELECT sc_id FROM (
                    SELECT sc_id, 1 AS pri, 0 AS f
                      FROM mc.companies WHERE sc_id = :s
                    UNION ALL
                    SELECT sc_id, 2, 0
                      FROM mc.companies WHERE upper(nse_symbol) = upper(:s)
                    UNION ALL
                    SELECT c.sc_id, 3,
                           CASE WHEN EXISTS (
                               SELECT 1 FROM mc.statement_lines sl
                               WHERE sl.sc_id = c.sc_id
                           ) THEN 0 ELSE 1 END
                      FROM mc.companies c WHERE upper(c.ticker) = upper(:s)
                    UNION ALL
                    SELECT sc_id, 4, 0
                      FROM mc.companies WHERE bse_code = :s
                    UNION ALL
                    SELECT sc_id, 5, 0
                      FROM mc.companies WHERE upper(company_name) = upper(:s)
                ) x
                ORDER BY pri, f
                LIMIT 1
                """
            ),
            {"s": sym},
        ).fetchone()
        if row:
            return row[0]

        # Step 6: enrich-DB fallback. Additive — never restructures the SQL
        # above; only runs when the mc-only path missed.
        try:
            from backend.market import enrich_db

            if enrich_db.is_enabled():
                enr = enrich_db.get_by_ticker(sym)
                if enr is not None:
                    confirmed = s.execute(
                        text(
                            "SELECT sc_id FROM mc.companies WHERE sc_id = :id"
                        ),
                        {"id": enr.sc_id},
                    ).fetchone()
                    if confirmed:
                        return confirmed[0]
        except Exception:
            # enrich hiccup can never break symbol resolution — every other
            # branch here fails soft too.
            pass
        return None
    finally:
        if owns:
            s.close()


def get_company(symbol_or_sc_id: str, *, session: Session | None = None) -> Company | None:
    """Resolve a symbol to its mc.companies row.

    NOTE: ``sector``, ``bse_code`` and ``market_cap`` are 100% NULL in
    ``mc.companies`` — they are carried on the dataclass for shape only. The
    real sector / market-cap come from the enrich DB (``enrich_db``); callers
    that display them (financials router, analysis tools, autosuggest) source
    them there, never from these dead columns.
    """
    owns = session is None
    s = session or _session()
    try:
        sc_id = resolve_symbol(symbol_or_sc_id, session=s)
        if sc_id is None:
            return None
        row = s.execute(
            text(
                """
                SELECT sc_id, company_name, nse_symbol, bse_code, ticker,
                       sector, industry_slug, market_cap, is_active, logo_url
                FROM mc.companies
                WHERE sc_id = :id
                """
            ),
            {"id": sc_id},
        ).fetchone()
        if not row:
            return None
        return Company(
            sc_id=row[0],
            name=row[1],
            nse_symbol=row[2],
            bse_code=row[3],
            ticker=row[4],
            sector=row[5],
            industry_slug=row[6],
            market_cap=float(row[7]) if row[7] is not None else None,
            is_active=bool(row[8]),
            logo_url=row[9],
        )
    finally:
        if owns:
            s.close()


def get_logo_urls_by_symbols(
    symbols: list[str], *, session: Session | None = None
) -> dict[str, str | None]:
    """Batch-fetch the precomputed ``mc.companies.logo_url`` for a page of
    symbols in ONE query, keyed by ``UPPER(symbol)``.

    Used by :func:`backend.market.company_logos.get_logo_urls` as the last-resort
    fallback so a whole page resolves the precomputed column without an N+1 of
    per-symbol :func:`get_company` calls. Matches by ``UPPER(nse_symbol)`` or
    ``UPPER(ticker)``; on the residual RELIANCE-style collision the canonical
    ``nse_symbol`` holder wins (same dedup as
    ``fundamentals_screen.fetch_gate_inputs``). A symbol with no row (or a NULL
    ``logo_url``) is simply absent from the map — the caller treats absent as
    "no precomputed logo". Never raises to the caller beyond the DB layer."""
    syms = [str(s).strip().upper() for s in symbols if str(s).strip()]
    if not syms:
        return {}
    owns = session is None
    s = session or _session()
    try:
        rows = s.execute(
            text(
                """
                SELECT UPPER(COALESCE(c.nse_symbol, c.ticker)) AS sym,
                       c.logo_url,
                       c.nse_symbol
                FROM mc.companies c
                WHERE c.is_active
                  AND UPPER(COALESCE(c.nse_symbol, c.ticker)) = ANY(:syms)
                """
            ),
            {"syms": syms},
        ).fetchall()
        out: dict[str, str | None] = {}
        has_canonical: dict[str, bool] = {}
        for sym, logo_url, nse_symbol in rows:
            # Prefer the canonical nse_symbol holder on a symbol collision.
            if sym in has_canonical and has_canonical[sym] and nse_symbol is None:
                continue
            out[sym] = logo_url
            has_canonical[sym] = nse_symbol is not None
        return out
    finally:
        if owns:
            s.close()


def get_names_by_symbols(
    symbols: list[str], *, session: Session | None = None
) -> dict[str, str]:
    """Batch-resolve UPPER(symbol) -> display name in two round-trips (one
    ``mc.companies`` query + one enrich_db batch), for surfaces that show a
    company name for a short symbol list (e.g. equity-basket holdings)
    without an N+1 of per-symbol :func:`get_company` calls. Prefers the
    untruncated enrich ``long_name`` over ``mc.companies.company_name``
    (truncated to 15 chars — see :func:`search_companies`); falls back to
    the truncated name, then to the bare symbol, when enrich has nothing.
    A symbol absent from ``mc.companies`` entirely is simply absent from the
    returned map — the caller falls back to the symbol itself."""
    syms = [str(s).strip().upper() for s in symbols if str(s).strip()]
    if not syms:
        return {}
    owns = session is None
    s = session or _session()
    try:
        rows = s.execute(
            text(
                """
                SELECT UPPER(COALESCE(c.nse_symbol, c.ticker)) AS sym,
                       c.sc_id,
                       c.company_name,
                       c.nse_symbol
                FROM mc.companies c
                WHERE c.is_active
                  AND UPPER(COALESCE(c.nse_symbol, c.ticker)) = ANY(:syms)
                """
            ),
            {"syms": syms},
        ).fetchall()
        by_sym: dict[str, tuple[str, str | None]] = {}
        has_canonical: dict[str, bool] = {}
        for sym, sc_id, mc_name, nse_symbol in rows:
            if sym in has_canonical and has_canonical[sym] and nse_symbol is None:
                continue
            by_sym[sym] = (sc_id, mc_name)
            has_canonical[sym] = nse_symbol is not None

        profiles: dict[str, dict] = {}
        try:
            from backend.market import enrich_db
            if enrich_db.is_enabled():
                profiles = enrich_db.get_profiles_by_sc_ids(
                    [sc_id for sc_id, _ in by_sym.values()]
                )
        except Exception:  # noqa: BLE001 — enrichment is decorative here
            profiles = {}

        out: dict[str, str] = {}
        for sym, (sc_id, mc_name) in by_sym.items():
            long_name = (profiles.get(sc_id) or {}).get("long_name")
            out[sym] = (long_name or "").strip() or mc_name or sym
        return out
    finally:
        if owns:
            s.close()


@dataclass(frozen=True)
class CompanyHit:
    """A single autosuggest result. `symbol` is the navigable trading symbol
    (prefer NSE, fall back to the generic ticker)."""
    sc_id: str
    symbol: str
    name: str
    sector: str | None
    has_fundamentals: bool
    logo_url: str | None = None


def search_companies(
    q: str,
    *,
    limit: int = 10,
    session: Session | None = None,
) -> list[CompanyHit]:
    """Fuzzy company lookup for the FE autosuggest dropdown.

    Searches `mc.companies` (the only equity universe we have) by name,
    trading symbol, or sc_id. Only rows that carry a navigable symbol
    (nse_symbol or ticker) are returned, since the stock page routes on the
    symbol. Prefix matches rank above substring matches; ties break on name.
    `has_fundamentals` flags whether we hold any statement data for the row.
    """
    q = (q or "").strip()
    if not q:
        return []
    limit = max(1, min(int(limit), 50))
    owns = session is None
    s = session or _session()
    try:
        qu = q.upper()
        # Over-fetch so the per-symbol dedup below can still return `limit`
        # clean rows after collapsing the messy duplicates MC carries (the
        # same trading symbol under several sc_ids / name spellings).
        rows = s.execute(
            text(
                """
                SELECT sc_id,
                       company_name,
                       nse_symbol,
                       ticker,
                       logo_url
                FROM mc.companies
                WHERE upper(company_name) LIKE :prefix
                   OR upper(company_name) LIKE :substr
                   OR upper(nse_symbol) LIKE :prefix
                   OR upper(ticker) LIKE :prefix
                ORDER BY
                  CASE
                    WHEN upper(nse_symbol) = :exact OR upper(ticker) = :exact THEN 0
                    WHEN upper(company_name) LIKE :prefix THEN 1
                    WHEN upper(nse_symbol) LIKE :prefix OR upper(ticker) LIKE :prefix THEN 2
                    ELSE 3
                  END,
                  length(company_name),
                  company_name
                LIMIT :scan
                """
            ),
            {
                "exact": qu,
                "prefix": f"{qu}%",
                "substr": f"%{qu}%",
                "scan": min(limit * 5, 200),
            },
        ).fetchall()
        if not rows:
            return []

        # One round-trip to flag which of the hits actually have statement data.
        sc_ids = [r[0] for r in rows]
        have = {
            row[0]
            for row in s.execute(
                text(
                    "SELECT DISTINCT sc_id FROM mc.statement_lines "
                    "WHERE sc_id = ANY(:ids)"
                ),
                {"ids": sc_ids},
            ).fetchall()
        }

        # Promote rows that actually carry statement data ahead of the ETF /
        # shell-company noise MC mixes in (stable — keeps the SQL rank within
        # each group).
        ordered = sorted(rows, key=lambda r: 0 if r[0] in have else 1)

        # Neither sector NOR a full name is read from mc.companies: the sector
        # column is 100% NULL and company_name is truncated to 15 chars ("BHEL"
        # for "Bharat Heavy Electricals Limited"). Both come from the enrich DB.
        # Resolve the whole page in ONE cross-DB batch (fail-safe: no enrich →
        # fall back to the mc name / no sector).
        profiles: dict[str, dict] = {}
        try:
            from backend.market import enrich_db
            if enrich_db.is_enabled():
                profiles = enrich_db.get_profiles_by_sc_ids([r[0] for r in ordered])
        except Exception:  # noqa: BLE001 — enrich enrichment is decorative here
            profiles = {}

        out: list[CompanyHit] = []
        seen: set[str] = set()
        for r in ordered:
            sc_id, name, nse_symbol, ticker, logo_url = r
            has_fund = sc_id in have
            prof = profiles.get(sc_id) or {}
            # Prefer the full enrich long_name over the truncated mc name.
            display_name = (prof.get("long_name") or "").strip() or name
            # Navigable symbol: the verified nse_symbol first; a scraper
            # `ticker` only when the row actually has fundamentals (shells like
            # 'Jay Electric'/'Bharat Hotels' carry a stolen ticker='BHEL' but
            # zero statements, so they must NOT contribute a navigable symbol
            # that would collide with — or masquerade as — the real company).
            # The MC `sc_id` is NEVER a symbol: '/stock/BHE' and
            # '/stock/HARIS54268' can't resolve a quote and dead-end the page.
            nav = (nse_symbol or "").strip() or (
                (ticker or "").strip() if has_fund else ""
            )
            if not nav:
                continue
            sym = nav.upper()
            if sym in seen:
                continue
            seen.add(sym)
            out.append(
                CompanyHit(
                    sc_id=sc_id,
                    symbol=sym,
                    name=display_name,
                    sector=prof.get("sector"),
                    has_fundamentals=has_fund,
                    logo_url=logo_url,
                )
            )
            if len(out) >= limit:
                break
        return out
    finally:
        if owns:
            s.close()


def get_fundamental(
    symbol_or_sc_id: str,
    field: str,
    *,
    as_of_date: date | None = None,
    basis: str = "consolidated",
    session: Session | None = None,
) -> FundamentalValue | None:
    """Latest value of a curated fundamental field as of a given date.

    `field` must be a key in FIELD_MAP. Tries each synonym in order; first
    one with a non-null `value_numeric` wins.

    `as_of_date` enforces point-in-time correctness: only rows whose
    `availability_date` is on or before this date are considered. Pass None
    to get the latest available row (use sparingly — fine for live UI,
    wrong for backtests).
    """
    if field not in FIELD_MAP:
        raise KeyError(
            f"Unknown fundamental field {field!r}. Known fields: "
            f"{sorted(FIELD_MAP)}"
        )
    statement, synonyms = FIELD_MAP[field]
    return _get_line_item_value(
        symbol_or_sc_id,
        statement=statement,
        line_items=synonyms,
        field_alias=field,
        as_of_date=as_of_date,
        basis=basis,
        session=session,
    )


def get_line_item(
    symbol_or_sc_id: str,
    line_item: str,
    *,
    statement: str | None = None,
    as_of_date: date | None = None,
    basis: str = "consolidated",
    session: Session | None = None,
) -> FundamentalValue | None:
    """Escape hatch: query any raw Moneycontrol line_item string.

    Useful when the curated FIELD_MAP doesn't cover a field. `statement`
    is optional — when omitted we search across all four statement types.
    """
    statements: tuple[str, ...]
    if statement:
        statements = (statement,)
    else:
        statements = ("profit_loss", "balance_sheet", "cash_flow", "ratios")
    for st in statements:
        v = _get_line_item_value(
            symbol_or_sc_id,
            statement=st,
            line_items=(line_item,),
            field_alias=line_item,
            as_of_date=as_of_date,
            basis=basis,
            session=session,
        )
        if v is not None:
            return v
    return None


# Free-text term → canonical FIELD_MAP key. Seeds the semantic resolver so
# the most common ways a user/LLM phrases a metric hit the curated synonyms
# before we fall back to fuzzy line-item matching. Keys are normalised
# (lowercased, alnum+space). Extend freely — misses just fall through.
_TERM_ALIASES: dict[str, str] = {
    "sales": "revenue", "revenue": "revenue", "topline": "revenue",
    "top line": "revenue", "turnover": "revenue", "total revenue": "revenue",
    "total income": "revenue", "net sales": "revenue", "total sales": "revenue",
    "profit": "net_profit", "net profit": "net_profit", "pat": "net_profit",
    "net income": "net_profit", "bottomline": "net_profit",
    "bottom line": "net_profit", "earnings": "net_profit",
    "operating profit": "operating_profit", "ebit": "operating_profit",
    "ebitda": "ebitda_margin", "eps": "eps_basic",
    "earnings per share": "eps_basic", "diluted eps": "eps_diluted",
    "book value": "book_value_per_share", "bvps": "book_value_per_share",
    "debt": "total_debt", "borrowings": "total_debt", "equity": "total_equity",
    "net worth": "total_equity", "shareholders funds": "total_equity",
    "reserves": "reserves", "interest": "interest_expense",
    "finance cost": "interest_expense", "finance costs": "interest_expense",
    "cash from operations": "cash_from_ops", "operating cash flow": "cash_from_ops",
    "ocf": "cash_from_ops", "cfo": "cash_from_ops",
    "cash flow from operations": "cash_from_ops",
    "roe": "roe", "return on equity": "roe", "return on networth": "roe",
    "roce": "roce", "return on capital employed": "roce",
    "roa": "roa", "return on assets": "roa",
    "roic": "roic", "return on invested capital": "roic",
    "de": "debt_to_equity", "d/e": "debt_to_equity",
    "debt to equity": "debt_to_equity", "debt equity": "debt_to_equity",
    "leverage": "debt_to_equity",
    "current ratio": "current_ratio", "quick ratio": "quick_ratio",
    "acid test": "quick_ratio",
    "interest coverage": "interest_coverage", "icr": "interest_coverage",
    "net margin": "net_profit_margin", "net profit margin": "net_profit_margin",
    "profit margin": "net_profit_margin",
    "operating margin": "operating_margin", "opm": "operating_margin",
    "pbit margin": "operating_margin",
    "gross margin": "gross_margin", "gross profit margin": "gross_margin",
    "ebitda margin": "ebitda_margin", "pbdit margin": "ebitda_margin",
    "asset turnover": "asset_turnover",
    "inventory turnover": "inventory_turnover",
    "receivables turnover": "receivables_turnover",
    "debtors turnover": "receivables_turnover",
    "payout": "dividend_payout", "dividend payout": "dividend_payout",
    "pe": "pe", "p/e": "pe", "price to earnings": "pe", "pe ratio": "pe",
    "pb": "pb", "p/b": "pb", "price to book": "pb",
    "ps": "ps", "p/s": "ps", "price to sales": "ps",
    "enterprise value": "enterprise_value_cr", "ev": "enterprise_value_cr",
    "ev/ebitda": "ev_to_ebitda", "earnings yield": "earnings_yield",
    "market cap": "mcap", "marketcap": "mcap", "market capitalisation": "mcap",
}


def _norm_term(t: str) -> str:
    """Lowercase, keep alnum + single spaces — so 'D/E Ratio' ~ 'de ratio'."""
    import re as _re
    return _re.sub(r"\s+", " ", _re.sub(r"[^a-z0-9 ]", " ", (t or "").lower())).strip()


# Alias keys normalised the SAME way inputs are, so slashed/punctuated forms
# ('P/S' → 'p s', 'D/E' → 'd e') resolve. Built once at import.
_TERM_ALIASES_NORM: dict[str, str] = {}
for _k, _v in _TERM_ALIASES.items():
    _TERM_ALIASES_NORM.setdefault(_norm_term(_k), _v)


def list_available_line_items(
    symbol_or_sc_id: str, *, session: Session | None = None,
) -> list[dict]:
    """Every distinct (statement, line_item) a company actually has a numeric
    value for — the vocabulary the fuzzy resolver matches against and the LLM
    can be shown to translate an unmatched term."""
    owns = session is None
    s = session or _session()
    try:
        sc_id = resolve_symbol(symbol_or_sc_id, session=s)
        if sc_id is None:
            return []
        rows = s.execute(
            text(
                """
                SELECT DISTINCT statement::text, line_item
                FROM mc.statement_lines
                WHERE sc_id = :sc AND value_numeric IS NOT NULL
                ORDER BY 1, 2
                """
            ),
            {"sc": sc_id},
        ).fetchall()
        return [{"statement": r[0], "line_item": r[1]} for r in rows]
    finally:
        if owns:
            s.close()


def _score_line_item(term_norm: str, line_item: str) -> float:
    """0..1 relevance of a raw line_item to a normalised term. Token overlap
    (Jaccard) + a substring bonus + a difflib ratio, so 'sales' scores high
    on 'Net Sales' and 'cash from ops' on 'Net CashFlow From Operating…'."""
    import difflib
    li = _norm_term(line_item)
    if not li or not term_norm:
        return 0.0
    tset, lset = set(term_norm.split()), set(li.split())
    jacc = len(tset & lset) / len(tset | lset) if (tset | lset) else 0.0
    sub = 1.0 if (term_norm in li or li in term_norm) else 0.0
    ratio = difflib.SequenceMatcher(None, term_norm, li).ratio()
    # Weight token overlap + substring highest; ratio breaks ties.
    return 0.5 * jacc + 0.35 * sub + 0.15 * ratio


def resolve_financial_query(
    symbol_or_sc_id: str,
    term: str,
    *,
    basis: str = "consolidated",
    as_of_date: date | None = None,
    session: Session | None = None,
    max_candidates: int = 6,
    history: int = 0,
) -> dict:
    """Resolve ANY free-text financial term for a company to a value.

    Resolution order:
      1. price ratios  (pe / pb / ps)      → compute_price_ratio
      2. term-alias / FIELD_MAP key         → resolve_metric / get_fundamental
      3. fuzzy match vs the company's real line_items → get_line_item

    Returns a dict: {resolved, value, unit, line_item, statement, period_label,
    matched_via, matched_key, candidates}. `candidates` is the ranked list of
    plausible line_items when we're NOT confident — the caller (LLM) can then
    translate by picking one. Never fabricates; `resolved=False` on a miss.

    When ``history`` > 0, the resolved FIELD_MAP metric is returned as an
    annual series too (``series``: newest-first ``[{period_label, period_end,
    value}]``) so trend/CAGR/"which year had max profit" asks are answerable.
    """
    out: dict = {
        "symbol": (symbol_or_sc_id or "").strip().upper(),
        "term": term, "resolved": False, "value": None, "unit": None,
        "line_item": None, "statement": None, "period_label": None,
        "matched_via": None, "matched_key": None, "candidates": [],
    }
    if history:
        out["series"] = []
    tnorm = _norm_term(term)
    if not tnorm:
        return out

    def _attach_history(field_key: str, s: Session) -> None:
        if not history or field_key not in FIELD_MAP:
            return
        try:
            hist = get_fundamental_history(
                symbol_or_sc_id, field_key, basis=basis,
                limit=max(2, int(history)), as_of_date=as_of_date, session=s,
            )
            out["series"] = [
                {
                    "period_label": h.period_label,
                    "period_end": h.period_end.isoformat() if h.period_end else None,
                    "value": float(h.value_numeric) if h.value_numeric is not None else None,
                }
                for h in hist
            ]
        except Exception:  # noqa: BLE001 — history is best-effort
            pass

    owns = session is None
    s = session or _session()
    try:
        # 1) price ratios
        alias_key = _TERM_ALIASES_NORM.get(tnorm) or _TERM_ALIASES.get(tnorm)
        if alias_key in ("pe", "pb", "ps"):
            v = resolve_metric(
                symbol_or_sc_id, alias_key, as_of_date=as_of_date,
                basis=basis, session=s,
            )
            if v is not None:
                out.update(resolved=True, value=round(float(v), 2), unit="x",
                           line_item=alias_key.upper(), statement="derived",
                           matched_via="price_ratio", matched_key=alias_key)
                return out
        # 2) curated key (via alias or a direct FIELD_MAP key)
        key = alias_key or (tnorm.replace(" ", "_") if tnorm.replace(" ", "_") in FIELD_MAP else None)
        if key and key in FIELD_MAP:
            fv = get_fundamental(
                symbol_or_sc_id, key, as_of_date=as_of_date,
                basis=basis, session=s,
            )
            if fv and fv.value_numeric is not None:
                out.update(
                    resolved=True, value=float(fv.value_numeric), unit=fv.unit,
                    line_item=fv.line_item, statement=fv.statement,
                    period_label=fv.period_label, matched_via="field_map",
                    matched_key=key,
                )
                _attach_history(key, s)
                return out
        # 3) fuzzy match against the company's actual line_items
        items = list_available_line_items(symbol_or_sc_id, session=s)
        scored = sorted(
            ((_score_line_item(tnorm, it["line_item"]), it) for it in items),
            key=lambda x: x[0], reverse=True,
        )
        out["candidates"] = [
            {**it, "score": round(sc, 3)} for sc, it in scored[:max_candidates] if sc > 0.15
        ]
        if scored and scored[0][0] >= 0.55:
            best = scored[0][1]
            fv = get_line_item(
                symbol_or_sc_id, best["line_item"], statement=best["statement"],
                as_of_date=as_of_date, basis=basis, session=s,
            )
            if fv and fv.value_numeric is not None:
                out.update(
                    resolved=True, value=float(fv.value_numeric), unit=fv.unit,
                    line_item=fv.line_item, statement=fv.statement,
                    period_label=fv.period_label, matched_via="fuzzy",
                    matched_key=best["line_item"],
                )
                return out
        return out
    finally:
        if owns:
            s.close()


def _get_line_item_value(
    symbol_or_sc_id: str,
    *,
    statement: str,
    line_items: Sequence[str],
    field_alias: str,
    as_of_date: date | None,
    basis: str,
    session: Session | None,
) -> FundamentalValue | None:
    owns = session is None
    s = session or _session()
    try:
        sc_id = resolve_symbol(symbol_or_sc_id, session=s)
        if sc_id is None:
            return None
        # Try the requested basis first; fall back to the other if no rows.
        for active_basis in (basis, "standalone" if basis == "consolidated" else "consolidated"):
            row = s.execute(
                text(
                    """
                    SELECT line_item, basis, period_label, period_end,
                           availability_date, value_numeric, value_text, unit
                    FROM mc.statement_lines
                    WHERE sc_id = :sc
                      AND statement = :stmt
                      AND basis = :basis
                      AND line_item = ANY(:items)
                      AND value_numeric IS NOT NULL
                      AND (:as_of IS NULL OR availability_date <= :as_of)
                      AND (statement <> 'ratios' OR source IN ('mc_html', 'mc_api'))
                    ORDER BY period_end DESC NULLS LAST,
                             availability_date DESC NULLS LAST
                    LIMIT 1
                    """
                ),
                {
                    "sc": sc_id,
                    "stmt": statement,
                    "basis": active_basis,
                    "items": list(line_items),
                    "as_of": as_of_date,
                },
            ).fetchone()
            if row:
                return FundamentalValue(
                    sc_id=sc_id,
                    field=field_alias,
                    line_item=row[0],
                    statement=statement,
                    basis=row[1],
                    period_label=row[2],
                    period_end=row[3],
                    availability_date=row[4],
                    value_numeric=float(row[5]) if row[5] is not None else None,
                    value_text=row[6],
                    unit=row[7],
                )
        return None
    finally:
        if owns:
            s.close()


def get_fundamental_history(
    symbol_or_sc_id: str,
    field: str,
    *,
    basis: str = "consolidated",
    limit: int = 12,
    as_of_date: date | None = None,
    session: Session | None = None,
) -> list[FundamentalValue]:
    """All available historical values for a field, newest period first.

    Deduped per `period_end`: when multiple synonyms match the same
    fiscal period (e.g. MC lists both "Revenue From Operations [Net]"
    and "[Gross]" for the same year), we keep only the first hit in
    synonym-priority order. Without this guard the FE renders the
    same year multiple times.
    """
    if field not in FIELD_MAP:
        raise KeyError(f"Unknown fundamental field {field!r}")
    statement, synonyms = FIELD_MAP[field]
    owns = session is None
    s = session or _session()
    try:
        sc_id = resolve_symbol(symbol_or_sc_id, session=s)
        if sc_id is None:
            return []
        # Try the requested basis first, then fall back to the other one —
        # mirrors `_get_line_item_value`. Without this, a company that only
        # files standalone statements (e.g. TCS — ~3,285 names in this DB
        # have no consolidated rows) returns an EMPTY history for the
        # default consolidated basis, so the FE renders all-"—" P&L and
        # Financials tables even though the data is present standalone.
        rows = []
        for active_basis in (
            basis,
            "standalone" if basis == "consolidated" else "consolidated",
        ):
            # Over-fetch then dedupe. The list_position trick gives us
            # synonym-priority ordering so the preferred name wins on a tie.
            rows = s.execute(
                text(
                    """
                    SELECT DISTINCT ON (period_end)
                           line_item, basis, period_label, period_end,
                           availability_date, value_numeric, value_text, unit
                    FROM mc.statement_lines
                    WHERE sc_id = :sc
                      AND statement = :stmt
                      AND basis = :basis
                      AND line_item = ANY(:items)
                      AND value_numeric IS NOT NULL
                      AND period_end IS NOT NULL
                      AND (:as_of IS NULL OR availability_date <= :as_of)
                      AND (statement <> 'ratios' OR source IN ('mc_html', 'mc_api'))
                    ORDER BY period_end DESC,
                             array_position(CAST(:items AS text[]), line_item)
                    LIMIT :lim
                    """
                ),
                {
                    "sc": sc_id,
                    "stmt": statement,
                    "basis": active_basis,
                    "items": list(synonyms),
                    "as_of": as_of_date,
                    "lim": limit,
                },
            ).fetchall()
            if rows:
                break
        return [
            FundamentalValue(
                sc_id=sc_id,
                field=field,
                line_item=r[0],
                statement=statement,
                basis=r[1],
                period_label=r[2],
                period_end=r[3],
                availability_date=r[4],
                value_numeric=float(r[5]) if r[5] is not None else None,
                value_text=r[6],
                unit=r[7],
            )
            for r in rows
        ]
    finally:
        if owns:
            s.close()


def get_company_fundamentals_bulk(
    sc_id: str,
    *,
    fields: Sequence[str],
    history_fields: Sequence[str] = (),
    history_limit: int = 12,
    basis: str = "consolidated",
    as_of_date: date | None = None,
    session: Session | None = None,
) -> tuple[dict[str, "FundamentalValue | None"], dict[str, list["FundamentalValue"]]]:
    """Latest snapshot + multi-year history for MANY fields in ONE query.

    Replaces the N+1 pattern of calling :func:`get_fundamental` /
    :func:`get_fundamental_history` once per field — each opened its own session,
    re-resolved the symbol, and made 1-2 Azure round-trips, so the stock-detail
    page's ~36 fields cost ~36+ sequential round-trips (~7s). Here we fetch every
    candidate ``statement_lines`` row for the company in a single query and do the
    per-field selection in Python, **preserving the exact basis-preference /
    latest-period / synonym-priority semantics** of the single-field helpers:

      * basis: prefer ``basis`` (consolidated); fall back to the other basis only
        when the preferred one has no matching row for that field;
      * latest: newest ``period_end`` (NULLS LAST), tie-broken by
        ``availability_date`` DESC;
      * history: one row per ``period_end`` (synonym-priority on ties), newest
        first, capped at ``history_limit``.

    Returns ``(latest, history)`` of :class:`FundamentalValue` objects, matching
    what the per-field helpers returned.
    """
    other = "standalone" if basis == "consolidated" else "consolidated"
    latest_fields = [f for f in fields if f in FIELD_MAP]
    hist_fields = [f for f in history_fields if f in FIELD_MAP]
    used = set(latest_fields) | set(hist_fields)
    if not used:
        return ({f: None for f in latest_fields}, {f: [] for f in hist_fields})

    # Union of statements + line-item synonyms we need → bounds the single fetch.
    want_stmts = sorted({FIELD_MAP[f][0] for f in used})
    want_items = sorted({li for f in used for li in FIELD_MAP[f][1]})

    owns = session is None
    s = session or _session()
    try:
        rows = (
            s.execute(
                text(
                    """
                    SELECT statement, basis, line_item, period_label, period_end,
                           availability_date, value_numeric, value_text, unit
                    FROM mc.statement_lines
                    WHERE sc_id = :sc
                      AND value_numeric IS NOT NULL
                      AND statement::text = ANY(:stmts)
                      AND line_item = ANY(:items)
                      AND (:as_of IS NULL OR availability_date <= :as_of)
                      AND (statement::text <> 'ratios' OR source IN ('mc_html', 'mc_api'))
                    """
                ),
                {
                    "sc": sc_id,
                    "stmts": want_stmts,
                    "items": want_items,
                    "as_of": as_of_date,
                },
            )
            .mappings()
            .all()
        )
    finally:
        if owns:
            s.close()

    by_stmt: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_stmt[r["statement"]].append(r)

    def _fv(r: dict, field: str) -> FundamentalValue:
        return FundamentalValue(
            sc_id=sc_id,
            field=field,
            line_item=r["line_item"],
            statement=r["statement"],
            basis=r["basis"],
            period_label=r["period_label"],
            period_end=r["period_end"],
            availability_date=r["availability_date"],
            value_numeric=float(r["value_numeric"]) if r["value_numeric"] is not None else None,
            value_text=r["value_text"],
            unit=r["unit"],
        )

    def _candidates(field: str) -> list[dict]:
        stmt, syns = FIELD_MAP[field]
        syn_set = set(syns)
        cands = [r for r in by_stmt.get(stmt, ()) if r["line_item"] in syn_set]
        cons = [r for r in cands if r["basis"] == basis]
        return cons if cons else [r for r in cands if r["basis"] == other]

    _MIN = date.min

    def _latest_key(r: dict) -> tuple:
        pe, av = r["period_end"], r["availability_date"]
        return (pe is not None, pe or _MIN, av is not None, av or _MIN)

    latest: dict[str, FundamentalValue | None] = {}
    for field in latest_fields:
        cands = _candidates(field)
        latest[field] = _fv(max(cands, key=_latest_key), field) if cands else None

    history: dict[str, list[FundamentalValue]] = {}
    for field in hist_fields:
        _, syns = FIELD_MAP[field]
        syn_pos = {li: i for i, li in enumerate(syns)}
        best_by_pe: dict[object, tuple[int, dict]] = {}
        for r in _candidates(field):
            if r["period_end"] is None:
                continue
            pos = syn_pos.get(r["line_item"], 1 << 30)
            cur = best_by_pe.get(r["period_end"])
            if cur is None or pos < cur[0]:
                best_by_pe[r["period_end"]] = (pos, r)
        ordered = sorted(
            best_by_pe.values(), key=lambda t: t[1]["period_end"], reverse=True
        )
        history[field] = [_fv(r, field) for _, r in ordered[:history_limit]]

    return latest, history


def get_balance_sheet_statement(
    symbol_or_sc_id: str,
    *,
    basis: str = "consolidated",
    years: int = 10,
    as_of_date: date | None = None,
    session: Session | None = None,
) -> dict | None:
    """Full balance sheet grid for one company: every line item MC publishes
    under statement='balance_sheet', with section headers and a multi-year
    column, straight from mc_html/mc_api — never pivot_derived (that source
    has no balance_sheet rows anyway) and never fabricated.

    Falls back from `basis` to the other basis if the preferred one has no
    rows, mirroring `get_fundamental_history`. Returns None only when the
    symbol itself doesn't resolve; a resolved company with no scraped
    balance sheet gets an empty `rows`/`periods` payload so the caller can
    say so honestly rather than erroring.
    """
    owns = session is None
    s = session or _session()
    try:
        sc_id = resolve_symbol(symbol_or_sc_id, session=s)
        if sc_id is None:
            return None

        raw_rows: list = []
        used_basis = basis
        for active_basis in (basis, "standalone" if basis == "consolidated" else "consolidated"):
            raw_rows = s.execute(
                text(
                    """
                    SELECT line_item, section, line_order, period_label, period_end,
                           value_numeric, value_text, unit
                    FROM mc.statement_lines
                    WHERE sc_id = :sc
                      AND statement = 'balance_sheet'
                      AND basis = :basis
                      AND source IN ('mc_html', 'mc_api')
                      AND (:as_of IS NULL OR availability_date <= :as_of)
                    ORDER BY line_order, period_end DESC
                    """
                ),
                {"sc": sc_id, "basis": active_basis, "as_of": as_of_date},
            ).fetchall()
            if raw_rows:
                used_basis = active_basis
                break

        if not raw_rows:
            return {"sc_id": sc_id, "basis": basis, "unit": None, "periods": [], "rows": []}

        period_pairs = sorted(
            {(r[3], r[4]) for r in raw_rows},
            key=lambda p: (p[1] is None, p[1]),
            reverse=True,
        )
        periods = [p[0] for p in period_pairs[:years]]
        period_set = set(periods)

        by_row: dict[tuple, dict] = {}
        unit: str | None = None
        for line_item, section, line_order, period_label, _period_end, value_numeric, value_text, u in raw_rows:
            if period_label not in period_set:
                continue
            key = (line_order, line_item)
            row = by_row.setdefault(
                key, {"section": section, "line_item": line_item, "values": {}, "value_texts": {}}
            )
            row["values"][period_label] = float(value_numeric) if value_numeric is not None else None
            row["value_texts"][period_label] = value_text
            unit = unit or u

        ordered_rows = [by_row[k] for k in sorted(by_row.keys(), key=lambda k: k[0])]
        return {
            "sc_id": sc_id,
            "basis": used_basis,
            "unit": unit,
            "periods": periods,
            "rows": ordered_rows,
        }
    finally:
        if owns:
            s.close()


def get_ohlcv(
    symbol_or_sc_id: str,
    *,
    start: date | None = None,
    end: date | None = None,
    session: Session | None = None,
) -> list[DailyBar]:
    """Daily OHLCV from mc.daily_prices, ascending by date.

    The price table is sparsely populated as of May 2026 — callers that
    need broad coverage should fall back to yfinance when this returns
    an empty list.
    """
    owns = session is None
    s = session or _session()
    try:
        sc_id = resolve_symbol(symbol_or_sc_id, session=s)
        if sc_id is None:
            return []
        rows = s.execute(
            text(
                """
                SELECT sc_id, trade_date, open, high, low, close,
                       volume, adj_factor, source
                FROM mc.daily_prices
                WHERE sc_id = :sc
                  AND (:start IS NULL OR trade_date >= :start)
                  AND (:end IS NULL OR trade_date <= :end)
                ORDER BY trade_date ASC
                """
            ),
            {"sc": sc_id, "start": start, "end": end},
        ).fetchall()
        return [
            DailyBar(
                sc_id=r[0],
                trade_date=r[1],
                open=float(r[2]) if r[2] is not None else None,
                high=float(r[3]) if r[3] is not None else None,
                low=float(r[4]) if r[4] is not None else None,
                close=float(r[5]),
                volume=int(r[6]) if r[6] is not None else None,
                adj_factor=float(r[7]),
                source=r[8],
            )
            for r in rows
        ]
    finally:
        if owns:
            s.close()


def list_supported_fields() -> list[str]:
    """The curated identifiers callers can pass to `get_fundamental`."""
    return sorted(FIELD_MAP)


# ── Safe formula evaluator ───────────────────────────────────────────────


class FormulaError(ValueError):
    """The formula didn't parse, used a banned construct, or referenced an
    identifier that isn't a FIELD_MAP key. Carries a user-facing message."""


# Whitelisted AST nodes for fundamentals formulas. Calls, attributes,
# subscripts, comparisons, booleans, comprehensions etc. are all rejected
# so an LLM-emitted formula can never execute arbitrary code.
_ALLOWED_NODES: tuple[type, ...] = (
    ast.Expression,
    ast.Constant,
    ast.Name,
    ast.Load,
    ast.UnaryOp, ast.UAdd, ast.USub,
    ast.BinOp, ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow, ast.Mod,
)


def _validate_formula_ast(tree: ast.AST) -> None:
    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED_NODES):
            raise FormulaError(
                f"formula uses unsupported construct {type(node).__name__!r}; "
                f"only arithmetic over named fundamentals is allowed"
            )


def evaluate_formula(
    symbol_or_sc_id: str,
    formula: str,
    *,
    as_of_date: date | None = None,
    basis: str = "consolidated",
    session: Session | None = None,
) -> float | None:
    """Evaluate an arithmetic expression over FIELD_MAP identifiers.

    Each bare name in the expression is resolved to its point-in-time
    `value_numeric` via `get_fundamental`. Numeric literals are allowed.
    Supported operators: + - * / ** %, unary +/-, parentheses.

    Returns None if any referenced identifier has no data at as_of_date,
    or if evaluation produces division by zero / NaN. Raises FormulaError
    only for structural / static problems (parse error, banned construct,
    unknown identifier name) — caller decides whether to surface those.
    """
    if not formula or not formula.strip():
        raise FormulaError("formula is empty")
    try:
        tree = ast.parse(formula, mode="eval")
    except SyntaxError as e:
        raise FormulaError(f"formula syntax error: {e.msg}") from e
    _validate_formula_ast(tree)

    # Validate identifiers up front so we can give a clean error message
    # without making a DB round-trip per name.
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id not in FIELD_MAP:
            raise FormulaError(
                f"unknown identifier {node.id!r}. Available: {sorted(FIELD_MAP)}"
            )

    owns = session is None
    s = session or _session()
    try:
        sc_id = resolve_symbol(symbol_or_sc_id, session=s)
        if sc_id is None:
            return None

        def _eval(node: ast.AST) -> float | None:
            if isinstance(node, ast.Expression):
                return _eval(node.body)
            if isinstance(node, ast.Constant):
                if isinstance(node.value, (int, float)):
                    return float(node.value)
                raise FormulaError(
                    f"unsupported literal {node.value!r} — only numbers allowed"
                )
            if isinstance(node, ast.Name):
                v = get_fundamental(
                    sc_id, node.id,
                    as_of_date=as_of_date, basis=basis, session=s,
                )
                return float(v.value_numeric) if v and v.value_numeric is not None else None
            if isinstance(node, ast.UnaryOp):
                x = _eval(node.operand)
                if x is None:
                    return None
                return +x if isinstance(node.op, ast.UAdd) else -x
            if isinstance(node, ast.BinOp):
                a = _eval(node.left)
                b = _eval(node.right)
                if a is None or b is None:
                    return None
                if isinstance(node.op, ast.Add):  return a + b
                if isinstance(node.op, ast.Sub):  return a - b
                if isinstance(node.op, ast.Mult): return a * b
                if isinstance(node.op, ast.Div):
                    if b == 0:
                        return None
                    return a / b
                if isinstance(node.op, ast.Mod):
                    if b == 0:
                        return None
                    return a % b
                if isinstance(node.op, ast.Pow):
                    try:
                        return a ** b
                    except (ValueError, OverflowError):
                        return None
            raise FormulaError(f"unsupported node {type(node).__name__!r}")

        result = _eval(tree)
        if result is None:
            return None
        if not (result == result):  # NaN guard
            return None
        return float(result)
    finally:
        if owns:
            s.close()


# ── Unified resolver for fetch.fundamental ───────────────────────────────


# Legacy short codes the agent has historically emitted. Map them to the
# canonical FIELD_MAP key (or to a derived calculation, in PE's case).
_LEGACY_METRIC_MAP: dict[str, str] = {
    "roe": "roe",
    "de":  "debt_to_equity",
    # `pe` is special — MC publishes earnings_yield rather than a P/E
    # line; we synthesise as 1/EY. Handled inline in resolve_metric.
    # `mcap` is intentionally absent — it has no point-in-time DB value
    # and must fall through to a live source (yfinance).
}


# Price ratio → (per-share FIELD_MAP key used as the divisor). We compute
# these LIVE as (price ÷ per-share fundamental) rather than trusting a
# scraped snapshot ratio, so they reflect today's price and work for any
# company that has the per-share line (P/E via net-profit-per-share, P/B via
# book-value-per-share, P/S via sales-per-share).
_PRICE_RATIO_DIVISOR: dict[str, tuple[str, ...]] = {
    "pe": ("net_profit_per_share", "eps_basic"),
    "pb": ("book_value_per_share",),
    "ps": ("sales_per_share",),
}


def _latest_price(symbol_or_sc_id: str, *, session: Session | None = None) -> float | None:
    """Live last price for a symbol (Kite REST, Redis-cached) with a yfinance
    fallback. Resolves an sc_id back to its verified NSE symbol first. None
    on miss — the caller then can't compute a price ratio and reports it
    unavailable.

    Used to gate the sc_id→symbol resolution on `sym.isdigit()`, assuming
    Moneycontrol's sc_id was always numeric. It isn't — e.g. Reliance
    Industries' sc_id is the alphanumeric code "RI", not a digit string — so
    that guard let raw sc_ids straight through to the yfinance fallback,
    which then queried Yahoo for a nonexistent "RI.NS" instead of the real
    "RELIANCE.NS" (live-probed 2026-07-14: a 5-symbol comparison spent ~6s
    per miss on this before timing out). `get_company` already resolves both
    real tickers and sc_ids correctly (via `resolve_symbol`'s priority
    match, now in-process cached — see module note above `resolve_symbol`),
    so call it unconditionally and prefer its verified `nse_symbol` whenever
    one exists, rather than guessing from the input's shape.
    """
    sym = (symbol_or_sc_id or "").strip().upper()
    if not sym:
        return None
    try:
        comp = get_company(symbol_or_sc_id, session=session)
        if comp:
            verified = (comp.nse_symbol or comp.ticker or "").strip().upper()
            if verified:
                sym = verified
    except Exception:  # noqa: BLE001
        pass
    if not sym:
        return None
    try:
        from backend.kite.live_quote import get_kite_quote
        q = get_kite_quote(sym, exchange="NSE")
        if q and q.get("last_price"):
            px = float(q["last_price"])
            if not math.isnan(px):
                return px
    except Exception:  # noqa: BLE001 — never fatal
        pass
    # yfinance fallback (bounded — never hang the request).
    try:
        from backend.market.net_timeout import call_bounded
        import yfinance as yf  # type: ignore[import-untyped]
        hist = call_bounded(
            lambda: yf.Ticker(f"{sym}.NS").history(period="5d"), timeout=6.0,
        )
        if hist is not None and not hist.empty:
            # A gapped/incomplete OHLCV row can carry a NaN close (seen live
            # on INFY) — NaN is truthy and fails every `<=`/`==` comparison,
            # so it silently poisons every downstream price-ratio guard
            # (`not price or price <= 0` does NOT catch it) all the way to
            # a JSON-serialization crash on the chat response. Reject it
            # here, at the source, the same way a missing price is rejected.
            px = float(hist["Close"].iloc[-1])
            if not math.isnan(px):
                return px
    except Exception:  # noqa: BLE001
        pass
    return None


def _derive_per_share(
    symbol_or_sc_id: str,
    kind: str,
    *,
    as_of_date: date | None,
    basis: str,
    session: Session | None,
) -> float | None:
    """Per-share denominator derived from the raw statements when the scraped
    per-share line is absent. shares (crore) = net_profit ÷ EPS; then the
    per-share figure = (₹-crore statement value) ÷ shares. Returns None when
    the required raw rows are missing."""
    def _v(field: str) -> float | None:
        r = get_fundamental(
            symbol_or_sc_id, field,
            as_of_date=as_of_date, basis=basis, session=session,
        )
        return float(r.value_numeric) if r and r.value_numeric is not None else None

    eps = _v("eps_basic") or _v("eps_diluted")
    if kind == "pe":
        return eps if (eps and eps > 0) else None
    net = _v("net_profit")
    if not eps or eps <= 0 or not net or net <= 0:
        return None
    shares_cr = net / eps  # ₹-crore ÷ (₹/share) → crore shares
    if shares_cr <= 0:
        return None
    if kind == "ps":
        rev = _v("revenue")
        return (rev / shares_cr) if rev and rev > 0 else None
    if kind == "pb":
        eq = _v("total_equity") or _v("reserves")
        return (eq / shares_cr) if eq and eq > 0 else None
    return None


def compute_price_ratio(
    symbol_or_sc_id: str,
    kind: str,
    *,
    price: float | None = None,
    as_of_date: date | None = None,
    basis: str = "consolidated",
    session: Session | None = None,
) -> float | None:
    """P/E, P/B or P/S computed as (price ÷ latest per-share fundamental).

    ``kind`` ∈ {"pe","pb","ps"}. ``price`` may be passed in (e.g. a backtest
    close on ``as_of_date``); when None we fetch the live last price. Returns
    None when the per-share line or the price is unavailable — never guesses.
    """
    k = (kind or "").strip().lower()
    divisors = _PRICE_RATIO_DIVISOR.get(k)
    if not divisors:
        return None
    per_share: float | None = None
    for field in divisors:
        v = get_fundamental(
            symbol_or_sc_id, field,
            as_of_date=as_of_date, basis=basis, session=session,
        )
        if v and v.value_numeric and v.value_numeric > 0:
            per_share = float(v.value_numeric)
            break
    # Fallback: derive the per-share denominator from the raw statements when
    # the scraped per-share line is missing (common for IT/banks). shares_cr =
    # net_profit ÷ EPS, then per-share = (₹-crore figure) ÷ shares_cr.
    if per_share is None or per_share <= 0:
        per_share = _derive_per_share(
            symbol_or_sc_id, k, as_of_date=as_of_date, basis=basis, session=session,
        )
    if per_share is None or per_share <= 0 or math.isnan(per_share):
        return None
    if price is None:
        price = _latest_price(symbol_or_sc_id, session=session)
    if not price or price <= 0 or math.isnan(price):
        return None
    return float(price) / per_share


def resolve_metric(
    symbol_or_sc_id: str,
    metric: str,
    *,
    formula: str | None = None,
    as_of_date: date | None = None,
    basis: str = "consolidated",
    session: Session | None = None,
) -> float | None:
    """Single entry point for fetch.fundamental, used by both the live
    executor and the backtester. Returns the numeric value or None.

    Resolution order:
      1. `metric == "formula"`           → evaluate_formula(formula)
      2. `metric == "pe"`                → 1 / earnings_yield
      3. `metric == "mcap"`              → None (caller falls back to live)
      4. `metric` in FIELD_MAP            → get_fundamental(metric)
      5. `metric` in _LEGACY_METRIC_MAP   → get_fundamental(mapped name)
      6. otherwise                       → None
    """
    m = (metric or "").strip().lower()
    if m == "formula":
        if not formula:
            return None
        try:
            return evaluate_formula(
                symbol_or_sc_id, formula,
                as_of_date=as_of_date, basis=basis, session=session,
            )
        except FormulaError:
            # Schema validates statically; a runtime FormulaError would
            # only happen on unknown identifier that slipped through —
            # treat as missing data and let condition.numeric short-circuit.
            return None
    if m in ("pe", "pb", "ps"):
        # Price-driven: latest price ÷ per-share fundamental. For a backtest
        # (as_of_date set) we don't have a point-in-time price here, so fall
        # through to the scraped-ratio path below rather than use today's
        # price against a past per-share figure.
        if as_of_date is None:
            pr = compute_price_ratio(
                symbol_or_sc_id, m, basis=basis, session=session,
            )
            if pr is not None:
                return pr
        if m == "pe":
            # Fallback: P/E = 1 / earnings-yield (scraped), incl. as-of dates.
            ey = get_fundamental(
                symbol_or_sc_id, "earnings_yield",
                as_of_date=as_of_date, basis=basis, session=session,
            )
            if ey and ey.value_numeric and ey.value_numeric != 0:
                return float(1.0 / ey.value_numeric)
        elif m == "pb":
            v = get_fundamental(
                symbol_or_sc_id, "price_to_book",
                as_of_date=as_of_date, basis=basis, session=session,
            )
            if v and v.value_numeric:
                return float(v.value_numeric)
        return None
    if m == "mcap":
        return None
    field_name = m if m in FIELD_MAP else _LEGACY_METRIC_MAP.get(m)
    if field_name is None:
        return None
    v = get_fundamental(
        symbol_or_sc_id, field_name,
        as_of_date=as_of_date, basis=basis, session=session,
    )
    return float(v.value_numeric) if v and v.value_numeric is not None else None


# ── Misc ─────────────────────────────────────────────────────────────────


def has_fundamentals(symbol_or_sc_id: str, *, session: Session | None = None) -> bool:
    """Quick check: does this symbol have any statement_lines rows at all?"""
    owns = session is None
    s = session or _session()
    try:
        sc_id = resolve_symbol(symbol_or_sc_id, session=s)
        if sc_id is None:
            return False
        row = s.execute(
            text("SELECT 1 FROM mc.statement_lines WHERE sc_id = :sc LIMIT 1"),
            {"sc": sc_id},
        ).fetchone()
        return row is not None
    finally:
        if owns:
            s.close()
