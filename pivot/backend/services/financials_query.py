"""Chat tool: general-purpose fundamentals query with history + aggregations.

Why this exists
---------------
The chat surface today reaches only ``fetch_fundamentals`` — a *latest snapshot*
of 8 curated ratios. But ``mc.statement_lines`` holds ~26 ratios plus full
P&L / balance-sheet / cash-flow line items across up to 12 annual periods per
company. Historical questions like:

  * "which year did Reliance have the max net profit?"
  * "has HDFC Bank's ROE been trending down?"
  * "revenue CAGR of TCS over the last 5 years"
  * "INFY debt over the last decade"

were structurally unanswerable — the LLM had nothing to lean on. ``query_financials``
is the single tool that closes that gap: N symbols × M fields × one aggregation
(``latest`` / ``series`` / ``max`` / ``min`` / ``cagr`` / ``yoy``), compiled to
``get_fundamental_history`` on the read replica.

Semantics
---------
* Per-``(symbol, field)`` degradation: an unknown symbol becomes a per-symbol
  ``error`` entry, a sparse field becomes ``{"value": null, "note": "..."}``.
  One bad input never fails the whole call — the LLM narrates around the gaps.
* Units where the DB provides them (``%``, ``Cr``, ``x``…) so the model uses the
  right denominator when narrating.
* Bound-parameter SQL only, via the FIELD_MAP resolver in
  :mod:`backend.market.financials_db` — never string-interpolates values.
* PE is derived point-in-time as ``1/earnings_yield`` (mirrors
  :func:`financials_db.resolve_metric`) — the DB doesn't ship PE directly.
* ROE-standalone-only-in-DB is handled transparently by
  :func:`get_fundamental_history`, which auto-falls-back to the other basis
  when the requested one has no rows.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Iterable

from backend.database import FinancialsSessionLocal
from backend.market import financials_db as fdb


# ── Public config surface ────────────────────────────────────────────────

_VALID_AGGS: tuple[str, ...] = (
    "latest",
    "series",
    "max",
    "min",
    "cagr",
    "yoy",
)

# The full queryable vocabulary. Everything in FIELD_MAP plus the synthetic
# "pe" (= 1/earnings_yield). Keep this sorted so error messages are stable
# — the LLM uses that list to self-repair on typos in one round.
_ALLOWED_FIELDS: tuple[str, ...] = tuple(
    sorted({*fdb.FIELD_MAP.keys(), "pe"})
)

_VALID_BASIS: tuple[str, ...] = ("consolidated", "standalone")

_MAX_SYMBOLS: int = 6
_MAX_FIELDS: int = 8
_MIN_YEARS: int = 1
_MAX_YEARS: int = 12
# Rough hard cap on total returned data points so a pathological
# ``(6 symbols, 8 fields, series, 12 years)`` request can't ship the LLM 576
# rows. Series responses are the only ones that can blow past this — the
# other aggregations are one row per (symbol, field).
_ROW_CAP: int = 100


# ── Arg validation ───────────────────────────────────────────────────────


def _coerce_str_list(v: Any, name: str, cap: int) -> list[str]:
    if not isinstance(v, list) or not v:
        raise ValueError(f"{name} must be a non-empty list of strings")
    if len(v) > cap:
        raise ValueError(f"{name} accepts at most {cap} entries; got {len(v)}")
    out: list[str] = []
    for s in v:
        if not isinstance(s, str) or not s.strip():
            raise ValueError(f"{name} entries must be non-empty strings")
        out.append(s.strip())
    return out


def _validate_fields(fields: list[str]) -> None:
    unknown = [f for f in fields if f not in _ALLOWED_FIELDS]
    if unknown:
        # The error MUST name the valid vocabulary so the LLM can self-repair
        # in one step — the tool never gets a second call for this turn.
        raise ValueError(
            f"unknown field(s) {unknown!r}. Valid fields: {list(_ALLOWED_FIELDS)}"
        )


def _parse_years(v: Any) -> int:
    try:
        y = int(v)
    except (TypeError, ValueError) as e:
        raise ValueError(
            f"years must be an integer between {_MIN_YEARS} and {_MAX_YEARS}"
        ) from e
    if not (_MIN_YEARS <= y <= _MAX_YEARS):
        raise ValueError(
            f"years must be between {_MIN_YEARS} and {_MAX_YEARS}; got {y}"
        )
    return y


def _parse_agg(v: Any) -> str:
    a = str(v).strip().lower()
    if a not in _VALID_AGGS:
        raise ValueError(
            f"unknown agg {a!r}. Valid: {list(_VALID_AGGS)}"
        )
    return a


def _parse_basis(v: Any) -> str:
    b = str(v).strip().lower()
    if b not in _VALID_BASIS:
        raise ValueError(f"basis must be one of {list(_VALID_BASIS)}; got {b!r}")
    return b


# ── Data helpers ─────────────────────────────────────────────────────────


def _round_val(v: float | None) -> float | None:
    if v is None:
        return None
    try:
        return round(float(v), 4)
    except (TypeError, ValueError):
        return None


def _iso(d: date | None) -> str | None:
    return d.isoformat() if d is not None else None


def _pct_change(new: float | None, old: float | None) -> float | None:
    if new is None or old is None or old == 0:
        return None
    return (new - old) / abs(old) * 100.0


def _cagr(new: float, old: float, years: int) -> tuple[float | None, str | None]:
    """Standard geometric CAGR as a percentage, guarding non-positive bases.

    Returns (value_pct, note). CAGR is undefined for negative or zero
    endpoints — return None and explain in the note rather than fabricate.
    """
    if years <= 0:
        return None, "cagr window collapsed to zero years"
    if old <= 0:
        return None, "cagr undefined for non-positive starting value"
    if new <= 0:
        return None, "cagr undefined for non-positive ending value"
    try:
        r = (new / old) ** (1.0 / years) - 1.0
        return round(r * 100.0, 4), None
    except (ValueError, OverflowError, ZeroDivisionError):
        return None, "cagr computation failed"


def _history_for(
    sc_id: str,
    field: str,
    basis: str,
    limit: int,
    session: Any,
) -> tuple[list[tuple[date, float, str | None]], str | None]:
    """Return ``(rows, unit)`` for a field, newest-first.

    Rows are ``(period_end, value, unit_or_None)``. The synthetic ``pe`` field
    is derived from the earnings_yield history as ``1/ey`` — mirrors the
    :func:`financials_db.resolve_metric` PE branch so live and history share
    one synthesis rule. Rows with a null value or null period_end are dropped
    (they'd break every aggregation and can't be surfaced usefully).
    """
    if field == "pe":
        raw = fdb.get_fundamental_history(
            sc_id, "earnings_yield",
            basis=basis, limit=limit, session=session,
        )
        rows: list[tuple[date, float, str | None]] = []
        for r in raw:
            ey = r.value_numeric
            if ey is None or ey == 0 or r.period_end is None:
                continue
            rows.append((r.period_end, 1.0 / float(ey), "x"))
        # Canonical unit for a multiple.
        return rows, ("x" if rows else None)

    raw = fdb.get_fundamental_history(
        sc_id, field,
        basis=basis, limit=limit, session=session,
    )
    rows = []
    unit: str | None = None
    for r in raw:
        if r.value_numeric is None or r.period_end is None:
            continue
        rows.append((r.period_end, float(r.value_numeric), r.unit))
        if unit is None and r.unit:
            unit = r.unit
    return rows, unit


def _fetch_limit_for_agg(agg: str, years: int) -> int:
    """Only pull as many rows as the aggregation actually needs — cuts the
    per-field query cost on Azure without changing the answer."""
    if agg == "latest":
        return 1
    if agg == "yoy":
        return 2
    return min(years, _MAX_YEARS)


# ── Aggregation dispatch ─────────────────────────────────────────────────


def _agg_latest(
    hist: list[tuple[date, float, str | None]], unit: str | None,
) -> dict[str, Any]:
    pe, val, _ = hist[0]
    return {"value": _round_val(val), "period_end": _iso(pe), "unit": unit}


def _agg_series(
    hist: list[tuple[date, float, str | None]],
    unit: str | None,
    room: int,
) -> tuple[dict[str, Any], int, bool]:
    truncated = False
    keep = hist[:room] if len(hist) > room else hist
    if len(hist) > len(keep):
        truncated = True
    seq = [
        {"period_end": _iso(pe), "value": _round_val(val)}
        for pe, val, _ in keep
    ]
    return ({"series": seq, "unit": unit}, len(seq), truncated)


def _agg_extreme(
    hist: list[tuple[date, float, str | None]],
    unit: str | None,
    which: str,
) -> dict[str, Any]:
    op = max if which == "max" else min
    best = op(hist, key=lambda r: r[1])
    return {
        "value": _round_val(best[1]),
        "period_end": _iso(best[0]),
        "unit": unit,
    }


def _agg_cagr(
    hist: list[tuple[date, float, str | None]], unit: str | None,
) -> dict[str, Any]:
    if len(hist) < 2:
        return {
            "value": None,
            "note": "need at least 2 non-null years for CAGR",
            "unit": unit,
        }
    newest_pe, newest_val, _ = hist[0]
    oldest_pe, oldest_val, _ = hist[-1]
    span_years = newest_pe.year - oldest_pe.year
    if span_years <= 0:
        return {
            "value": None,
            "note": "cagr window collapsed to zero years",
            "unit": unit,
        }
    cagr_pct, note = _cagr(newest_val, oldest_val, span_years)
    out: dict[str, Any] = {
        "value": cagr_pct,
        "unit": "%",
        "start_period_end": _iso(oldest_pe),
        "start_value": _round_val(oldest_val),
        "end_period_end": _iso(newest_pe),
        "end_value": _round_val(newest_val),
        "span_years": span_years,
    }
    if note:
        out["note"] = note
    return out


def _agg_yoy(
    hist: list[tuple[date, float, str | None]], unit: str | None,
) -> dict[str, Any]:
    if len(hist) < 2:
        return {
            "value": None,
            "note": "need at least 2 years for YoY",
            "unit": unit,
        }
    (new_pe, new_val, _), (old_pe, old_val, _) = hist[0], hist[1]
    return {
        "value": _round_val(new_val),
        "prior_value": _round_val(old_val),
        "change_abs": _round_val(new_val - old_val),
        "change_pct": _round_val(_pct_change(new_val, old_val)),
        "period_end": _iso(new_pe),
        "prior_period_end": _iso(old_pe),
        "unit": unit,
    }


# ── Public handler ───────────────────────────────────────────────────────


async def query_financials(args: dict[str, Any]) -> dict[str, Any]:
    """v2-handler entry point. Validates, resolves symbols, aggregates.

    Never raises for per-symbol / per-field data gaps — those become
    ``error`` / ``note`` entries in the return payload so the LLM can narrate
    a truthful "no data for X" rather than fabricate a number. Raises
    :class:`ValueError` only on structurally-bad input (wrong shape, unknown
    field, out-of-range years) so the LLM can self-repair on the next turn.
    """
    if not isinstance(args, dict):
        raise ValueError("args must be a dict")

    symbols = _coerce_str_list(args.get("symbols"), "symbols", _MAX_SYMBOLS)
    fields = _coerce_str_list(args.get("fields"), "fields", _MAX_FIELDS)
    _validate_fields(fields)
    agg = _parse_agg(args.get("agg", "latest"))
    years = _parse_years(args.get("years", 5))
    basis = _parse_basis(args.get("basis", "consolidated"))

    result: dict[str, Any] = {
        "symbols": {},
        "agg": agg,
        "years": years,
        "basis": basis,
    }
    notes: list[str] = []
    total_rows = 0
    row_cap_hit = False
    fetch_limit = _fetch_limit_for_agg(agg, years)

    # One session for the whole call — resolve_symbol + every history lookup
    # share the connection so we make one Azure round-trip per (symbol, field)
    # instead of two.
    session = FinancialsSessionLocal()
    try:
        for sym in symbols:
            sym_key = sym.upper()
            # Duplicate symbols in the input collapse to one entry; keeping
            # both would just replay the same lookup for zero information.
            if sym_key in result["symbols"]:
                continue

            sc_id = fdb.resolve_symbol(sym, session=session)
            if sc_id is None:
                result["symbols"][sym_key] = {
                    "error": f"symbol {sym_key!r} did not resolve to a company; "
                             "unknown to the fundamentals DB"
                }
                continue

            per_field: dict[str, Any] = {}
            for field in fields:
                try:
                    hist, unit = _history_for(
                        sc_id, field, basis, fetch_limit, session,
                    )
                except Exception as e:  # noqa: BLE001 — degrade to per-field null
                    per_field[field] = {
                        "value": None,
                        "note": f"lookup failed: {type(e).__name__}",
                    }
                    continue

                if not hist:
                    # Coverage varies by company: P&L line items (revenue,
                    # net_profit, eps_basic, operating_profit) span ~6,750
                    # companies; ratio history only ~3,700 and some large
                    # caps (e.g. TCS) carry P&L-only entries. Name the
                    # broader-coverage alternative so the LLM can offer a
                    # real next step instead of a dead end (probe D3:
                    # empty results should carry alternatives).
                    per_field[field] = {
                        "value": None,
                        "note": (
                            "no data in DB for this field for this company "
                            f"(basis={basis}, up to {fetch_limit} years). "
                            "Ratio-history coverage varies by company; "
                            "P&L lines (revenue, net_profit, eps_basic, "
                            "operating_profit) have the broadest history "
                            "coverage — try those, or fetch_fundamentals "
                            "for the latest snapshot of this ratio."
                        ),
                    }
                    continue

                if agg == "latest":
                    per_field[field] = _agg_latest(hist, unit)
                    total_rows += 1
                elif agg == "series":
                    room = max(_ROW_CAP - total_rows, 0)
                    entry, added, truncated = _agg_series(hist, unit, room)
                    per_field[field] = entry
                    total_rows += added
                    if truncated:
                        row_cap_hit = True
                elif agg in ("max", "min"):
                    per_field[field] = _agg_extreme(hist, unit, agg)
                    total_rows += 1
                elif agg == "cagr":
                    per_field[field] = _agg_cagr(hist, unit)
                    total_rows += 1
                elif agg == "yoy":
                    per_field[field] = _agg_yoy(hist, unit)
                    total_rows += 1

                if total_rows >= _ROW_CAP:
                    row_cap_hit = True
                    break

            result["symbols"][sym_key] = {"fields": per_field, "sc_id": sc_id}
            if row_cap_hit:
                break
    finally:
        session.close()

    if row_cap_hit:
        notes.append(
            f"result truncated at ~{_ROW_CAP} rows; narrow `fields`, `symbols`, "
            "or `years` to see the rest"
        )
    if notes:
        result["note"] = " | ".join(notes)
    # NOTE: no `_render_hint` — the FE chat renderer has no matching hint, so
    # per house rules the LLM narrates a markdown table from this JSON itself.
    return result


# ── Registration payload ─────────────────────────────────────────────────

# Team-lead will register this with a single `tool(...)` call. Kept as
# module-level constants so we don't touch tool_registry / tools.py during
# the concurrent refactor.


TOOL_NAME = "query_financials"

# Under 120 words. Starts with what it queries; ends with a Best-for /
# NOT-for gate so the router picks it over screen_fundamentals / market
# tools reliably. Every phrase is probe-driven.
TOOL_DESCRIPTION = (
    "Historical fundamentals for 1-6 Indian stocks: 26 ratios (PE, ROE, ROCE, "
    "D/E, margins, payout, EV/EBITDA, book value, ...) plus P&L / BS / CF "
    "line items (revenue, net_profit, EPS, total_debt, cash_from_ops) across "
    "up to 12 annual periods. Aggregations: latest (current snapshot), "
    "series (year-by-year newest-first), max/min (best/worst year in window), "
    "cagr (geometric growth oldest→newest), yoy (latest vs prior year, "
    "absolute + %). Returns DB-declared units and per-(symbol,field) nulls "
    "with a short note when data is missing — never fabricates. Best for: "
    "any fundamentals question including history — 'max profit year for "
    "Reliance', 'has HDFC ROE been falling', 'TCS 5y revenue CAGR', 'INFY "
    "debt over the last decade'. NOT for: screening/filtering many stocks "
    "(use screen_fundamentals) or price/quote data (use market data tools)."
)

TOOL_PROPERTIES: dict[str, dict[str, Any]] = {
    "symbols": {
        "type": "array",
        "items": {"type": "string"},
        "minItems": 1,
        "maxItems": _MAX_SYMBOLS,
        "description": (
            "1-6 NSE tickers (uppercase). Unknown symbols return a per-symbol "
            "error entry without failing the call."
        ),
    },
    "fields": {
        "type": "array",
        "items": {"type": "string", "enum": list(_ALLOWED_FIELDS)},
        "minItems": 1,
        "maxItems": _MAX_FIELDS,
        "description": (
            "1-8 fundamentals identifiers. Includes 'pe' (synthesised as "
            "1/earnings_yield) plus every FIELD_MAP key: revenue, net_profit, "
            "operating_profit, eps_basic, eps_diluted, interest_expense, "
            "total_debt, total_equity, reserves, cash_from_ops, roe, roce, "
            "roa, debt_to_equity, current_ratio, quick_ratio, "
            "interest_coverage, net_profit_margin, ebitda_margin, "
            "price_to_book, ev_to_ebitda, earnings_yield, dividend_payout, "
            "book_value_per_share, asset_turnover, enterprise_value_cr."
        ),
    },
    "agg": {
        "type": "string",
        "enum": list(_VALID_AGGS),
        "default": "latest",
        "description": (
            "latest = current snapshot value; series = full year-by-year "
            "history newest-first; max/min = best/worst single year in the "
            "window; cagr = geometric growth between oldest and newest "
            "non-null values (window length); yoy = latest vs prior year, "
            "absolute + %."
        ),
    },
    "years": {
        "type": "integer",
        "minimum": _MIN_YEARS,
        "maximum": _MAX_YEARS,
        "default": 5,
        "description": (
            "Window size (1-12 annual periods) used by series/max/min/cagr. "
            "latest ignores this; yoy always uses 2."
        ),
    },
    "basis": {
        "type": "string",
        "enum": list(_VALID_BASIS),
        "default": "consolidated",
        "description": (
            "consolidated is preferred; the DB auto-falls-back to standalone "
            "when the requested basis has no rows (e.g. ROE is standalone-"
            "only for many banks)."
        ),
    },
}

TOOL_REQUIRED: list[str] = ["symbols", "fields"]


def valid_fields() -> Iterable[str]:
    """Public read of the accepted `fields` vocabulary — used by tests and by
    the LLM-side prompt scaffolding if the router wants to inject the list."""
    return _ALLOWED_FIELDS
