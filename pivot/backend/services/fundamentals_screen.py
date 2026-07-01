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
from datetime import date, timedelta

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
    "market_cap": {
        # No point-in-time market-cap line item exists in mc.statement_lines,
        # and mc.companies.market_cap is 100% NULL. Cannot be served here.
        "kind": "unsupported",
        "items": [],
        "label": "Market Cap",
    },
}

# Accept a few common aliases the agent/LLM may emit for the public fields.
_FIELD_ALIASES: dict[str, str] = {
    "p/e": "pe",
    "pe_ratio": "pe",
    "price_to_earnings": "pe",
    "d/e": "de",
    "debt_to_equity": "de",
    "debt_equity": "de",
    "return_on_equity": "roe",
    "return_on_capital_employed": "roce",
    "dividend_payout": "payout",
    "payout_ratio": "payout",
    "mcap": "market_cap",
    "marketcap": "market_cap",
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
    (re.compile(r"^automobiles|^autoancillaries|^automobile|^auto"), "auto"),
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
    "auto": ["automobiles%", "autoancillaries%", "automobile%", "auto%"],
    "metal": ["steel%", "metalsnonferrous%", "metalsferrous%", "mining%", "aluminium%", "castingsforgings%"],
    "finance": ["finance%"],
    "chemicals": ["chemicals%", "fertilisers%", "pesticidesagrochemicals%"],
    "fmcg": ["fmcg%", "foodprocessing%", "personalcare%", "cigarettes%", "breweriesdistilleries%"],
    "infra": ["cement%", "construction%", "realestate%", "infrastructure%"],
    "textiles": ["textiles%"],
}

# Default recency floor: keep the cross-section to companies whose latest
# filing is within ~2 fiscal years. Without it, dormant shells with stale
# 2006-2012 rows (and nonsense ratios) leak into every result.
_RECENCY_YEARS = 2


def _default_min_period_end() -> date:
    today = date.today()
    return date(today.year - _RECENCY_YEARS, 1, 1)


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


def screen_by_fundamentals(
    filters: list[dict],
    sector: str | None = None,
    sort_by: dict | None = None,
    limit: int = 15,
    *,
    market_cap_tier: str | None = None,
    min_period_end: date | None | str = "default",
    session: Session | None = None,
) -> dict:
    """Return companies passing every fundamental constraint in `filters`.

    Parameters
    ----------
    filters
        List of `{"field", "op", "value"}`. `field` is one of
        pe / roe / roce / de / payout / market_cap (aliases accepted).
        `op` is one of < <= > >= =. `value` is numeric.
    sector
        Optional coarse sector ("pharma", "bank", "it", "energy", "auto",
        "metal", "finance", ...). Matched against industry_slug prefixes
        because mc.companies.sector is NULL.
    sort_by
        `{"field": <screen field>, "dir": "asc"|"desc"}`. Defaults to the
        first filter field, descending. The sort field is automatically
        included in the SELECT so it can be ordered on.
    limit
        Max rows returned (1..100).
    min_period_end
        Recency floor on the latest filing period. "default" -> ~2 fiscal
        years back; None -> no floor (includes dormant shells); or pass an
        explicit `date`.

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
        floor: date | None = _default_min_period_end()
    elif isinstance(min_period_end, str):
        floor = date.fromisoformat(min_period_end)
    else:
        floor = min_period_end  # date or None

    notes: list[str] = []

    # ── 1. Validate + normalise filters ─────────────────────────────────
    valid_filters: list[dict] = []
    for raw in filters or []:
        field = _normalise_field(raw.get("field", ""))
        op = (raw.get("op") or "").strip()
        if field not in _FIELD_DEFS:
            notes.append(f"unknown field {raw.get('field')!r} skipped")
            continue
        if op not in _ALLOWED_OPS:
            notes.append(f"unsupported op {raw.get('op')!r} on {field} skipped")
            continue
        defn = _FIELD_DEFS[field]
        if defn["kind"] == "unsupported":
            notes.append(
                f"{field} not available in financials DB "
                "(no point-in-time market-cap line item) — filter skipped"
            )
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
        if sf in _FIELD_DEFS and _FIELD_DEFS[sf]["kind"] != "unsupported":
            sort_field = sf
            sort_dir = "asc" if sd == "asc" else "desc"
        elif sf:
            notes.append(f"cannot sort by {sf!r}")
    if sort_field is None and valid_filters:
        sort_field = valid_filters[0]["field"]
        sort_dir = "desc"
    if sort_field is None and sector:
        # sector-only ask with no metric/sort -> rank by quality (ROE desc).
        sort_field = "roe"
        sort_dir = "desc"
        notes.append("no metric given — ranked by ROE (quality)")

    if not valid_filters and sort_field is None:
        return {
            "count": 0,
            "results": [],
            "applied_filters": [],
            "note": "; ".join(notes)
            or "give me a metric (PE/ROE/ROCE/D-E/payout) or a sector to screen",
        }

    metric_fields = list({f["field"] for f in valid_filters} | {sort_field})

    # ── 3. Build one CTE per metric: latest row per sc_id, basis-preferred ─
    params: dict = {"floor": floor}
    cte_sqls: list[str] = []
    select_cols: list[str] = []
    join_sqls: list[str] = []

    for i, mf in enumerate(metric_fields):
        defn = _FIELD_DEFS[mf]
        items_key = f"items_{i}"
        params[items_key] = defn["items"]
        cte_name = f"m_{mf}"
        # P/E is derived as 1/EarningsYield. A sane P/E (>= 1) needs 0 < EY <= 1;
        # sub-1 P/Es (EY > 1) are data artifacts for thinly-covered microcaps
        # (e.g. P/E 0.03), so bound the EY at the source — keeps filter AND sort
        # honest. (No effect on other fields.)
        extra = ""
        if defn["kind"] == "pe_from_ey":
            extra = "AND sl.value_numeric > 0 AND sl.value_numeric <= 1.0"
        # DISTINCT ON picks one row per sc_id: prefer consolidated basis,
        # then most recent period. Recency floor applied when `floor` set.
        cte_sqls.append(
            f"""{cte_name} AS (
                SELECT DISTINCT ON (sl.sc_id)
                       sl.sc_id, sl.value_numeric AS v,
                       sl.period_label AS plabel, sl.basis AS pbasis
                FROM mc.statement_lines sl
                WHERE sl.line_item = ANY(:{items_key})
                  AND sl.value_numeric IS NOT NULL
                  {extra}
                  AND (:floor IS NULL OR sl.period_end >= :floor)
                ORDER BY sl.sc_id,
                         (sl.basis = 'consolidated') DESC,
                         sl.period_end DESC NULLS LAST,
                         sl.availability_date DESC NULLS LAST
            )"""
        )
        join_sqls.append(f"JOIN {cte_name} ON {cte_name}.sc_id = c.sc_id")
        # PE is derived from EY at SELECT time; raw EY also exposed for debug.
        if defn["kind"] == "pe_from_ey":
            select_cols.append(
                f"CASE WHEN {cte_name}.v <> 0 THEN 1.0/{cte_name}.v END AS val_{mf}"
            )
        else:
            select_cols.append(f"{cte_name}.v AS val_{mf}")

    # ── 4. WHERE clause from the filters ─────────────────────────────────
    where_parts: list[str] = ["c.is_active"]
    for j, f in enumerate(valid_filters):
        defn = _FIELD_DEFS[f["field"]]
        cte_name = f"m_{f['field']}"
        val_param = f"val_{j}"
        if defn["kind"] == "pe_from_ey":
            # PE op value  <=>  EY (inv_op) (1/value), EY>0 assumed.
            inv = _OP_INVERT[f["op"]]
            if f["value"] == 0:
                # PE op 0 is degenerate; EY can't be infinite. Skip cleanly.
                notes.append("pe comparison against 0 skipped")
                continue
            params[val_param] = 1.0 / f["value"]
            where_parts.append(f"{cte_name}.v > 0 AND {cte_name}.v {inv} :{val_param}")
            # MC stores Earnings Yield at 2-decimal precision (0.01, 0.02, ...),
            # so the derived P/E lives on a coarse grid (100, 50, 33.3, 25, 20,
            # 16.7, ...). A displayed P/E may sit right on the threshold (e.g.
            # EY 0.04 -> P/E 24.9999... rounds to 25.0 yet truly is < 25).
            if not any("P/E derived" in n for n in notes):
                notes.append(
                    "P/E derived from MC Earnings Yield (2-dp) — values are "
                    "quantized and may display on the filter boundary"
                )
        else:
            params[val_param] = f["value"]
            where_parts.append(f"{cte_name}.v {f['op']} :{val_param}")

    # ── 5. Sector filter via industry_slug prefixes ─────────────────────
    if sector:
        sec = sector.strip().lower()
        prefixes = _SECTOR_SLUG_PREFIXES.get(sec)
        if prefixes:
            params["sector_prefixes"] = prefixes
            where_parts.append("c.industry_slug ILIKE ANY(:sector_prefixes)")
        else:
            notes.append(
                f"unknown sector {sector!r} (known: "
                f"{', '.join(sorted(_SECTOR_SLUG_PREFIXES))}) — sector filter ignored"
            )

    # ── 5a. Market-cap tier whitelist (P5) ───────────────────────────────
    # The DB has no market-cap field, so "large cap"/"bluechip" is honoured
    # by restricting to the curated cap universe. Without this the cap word
    # is silently dropped and micro-cap artifacts dominate the ranking.
    if tier in ("large", "mid"):
        syms = _LARGE_CAP_SYMS if tier == "large" else (_LARGE_CAP_SYMS | _MID_CAP_SYMS)
        params["cap_syms"] = list(syms)
        where_parts.append("UPPER(COALESCE(c.nse_symbol, c.ticker)) = ANY(:cap_syms)")
        notes.append(
            f"restricted to curated {tier}-cap universe (~{len(syms)} NSE names; "
            "DB has no market-cap field)"
        )
    elif tier == "small":
        notes.append(
            "small-cap filter is approximate — no curated small-cap list, "
            "so cap is not strictly enforced"
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
    }
    if tier in ("large", "mid"):
        _PLAUSIBLE = {**_PLAUSIBLE, "roe": "BETWEEN -50 AND 80",
                      "roce": "BETWEEN -50 AND 80"}
    for mf in metric_fields:
        cte_name = f"m_{mf}"
        if _FIELD_DEFS[mf]["kind"] == "pe_from_ey":
            # derived P/E = 1/v; keep P/E in (0, 500].
            where_parts.append(f"{cte_name}.v > 0 AND 1.0/{cte_name}.v <= 500")
        elif mf in _PLAUSIBLE:
            where_parts.append(f"{cte_name}.v {_PLAUSIBLE[mf]}")
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
    ORDER BY val_{sort_field} {order_dir} NULLS LAST
    LIMIT :lim
    """

    owns = session is None
    s = session or FinancialsSessionLocal()
    try:
        rows = s.execute(text(sql), params).fetchall()
    finally:
        if owns:
            s.close()

    # ── 7. Shape results ─────────────────────────────────────────────────
    val_idx = {mf: 5 + i for i, mf in enumerate(metric_fields)}
    results: list[dict] = []
    for row in rows:
        nse_symbol = row[2]
        ticker = row[3]
        symbol = nse_symbol or ticker or row[0]
        rec: dict = {
            "symbol": symbol,
            "name": row[1],
            "sector": _sector_for_slug(row[4]),
        }
        for mf in metric_fields:
            v = row[val_idx[mf]]
            rec[mf] = round(float(v), 2) if v is not None else None
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

    return {
        "count": len(results),
        "results": results,
        "applied_filters": valid_filters,
        "note": "; ".join(notes),
    }


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
                f"""{cte_name} AS (
                    SELECT DISTINCT ON (sl.sc_id)
                           sl.sc_id, sl.value_numeric AS v
                    FROM mc.statement_lines sl
                    WHERE sl.sc_id = ANY(:sc_ids)
                      AND sl.line_item = ANY(:{items_key})
                      AND sl.value_numeric IS NOT NULL
                      {extra}
                      AND (:floor IS NULL OR sl.period_end >= :floor)
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
