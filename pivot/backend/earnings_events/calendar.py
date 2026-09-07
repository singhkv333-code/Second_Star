"""Per-symbol earnings calendar, fed from yfinance.

The calendar's job is to answer: *for symbol ``X``, is a fresh earnings
release within the verification window right now?* Earnings dates are
per-ticker and shift quarter-to-quarter, so the registry is *live*:
:func:`fetch_earnings_rows` pulls the next 8 rows from yfinance's
``get_earnings_dates`` and caches them in Redis for ~12 h.

Public surface:

  - :class:`EarningsEventDef` — one occurrence (symbol + report-at + window).
  - :func:`fetch_earnings_rows` — raw provider rows for a symbol.
  - :func:`get_next_earnings`  — the next upcoming occurrence.
  - :func:`due_event`          — the occurrence whose verify window
    currently contains ``now`` (the scheduler's gate).

Everything is injection-seam'd via the ``fetch`` callable so tests run
with zero network. Fail-safe everywhere — provider hiccups return None,
never raise.
"""
from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

from backend.cache import redis_client

logger = logging.getLogger(__name__)


# Default verify window: 48 h. Long enough that the scheduler's 30-min
# tick will always see at least one chance to fire after a release, even
# if yfinance backfills the "reported_eps" cell several hours late;
# short enough that we don't re-evaluate stale prints into the next week.
DEFAULT_VERIFY_WINDOW_MINUTES: int = 2880

_ROWS_CACHE_TTL_SECONDS: int = 12 * 3600  # 12 h


@dataclass(frozen=True)
class EarningsEventDef:
    """One earnings occurrence for a single symbol."""

    symbol: str
    report_at_utc: datetime
    verify_window_minutes: int
    label: str

    @property
    def window_end_utc(self) -> datetime:
        return self.report_at_utc + timedelta(minutes=self.verify_window_minutes)

    def instance_key(self) -> str:
        """Stable per-occurrence id for the fire-once latch
        (e.g. ``'INFY:2026-07-15'``). The scheduler latches on this so a
        single quarter's release can fire at most once per workflow."""
        return f"{self.symbol.upper()}:{self.report_at_utc.date().isoformat()}"


# ── yfinance row fetcher (injectable for tests) ──────────────────────


def _rows_cache_key(symbol: str) -> str:
    return f"earnings:rows:{symbol.upper()}"


def _coerce_float(value: Any) -> Optional[float]:
    """Best-effort float coercion that treats NaN / None / '' as missing.

    yfinance routinely returns ``float('nan')`` for the
    reported / estimate cells of *future* quarters; we want those to
    surface as ``None`` so the verifier can fail-safe on them."""
    if value is None:
        return None
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        try:
            return float(s)
        except ValueError:
            return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return f


def _coerce_dt(value: Any) -> Optional[datetime]:
    """Coerce a yfinance index entry (Timestamp / datetime / str) into a
    timezone-aware UTC datetime. None if uncoerceable."""
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        # pandas.Timestamp exposes to_pydatetime(); fall back to isoformat.
        to_py = getattr(value, "to_pydatetime", None)
        if callable(to_py):
            try:
                dt = to_py()
            except Exception:  # noqa: BLE001
                return None
        else:
            try:
                dt = datetime.fromisoformat(str(value))
            except (TypeError, ValueError):
                return None
    if dt.tzinfo is None:
        # yfinance sometimes returns naive timestamps in US/Eastern;
        # we treat naive as UTC rather than guess and fail closed.
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt


def _resolve_earnings_listing(symbol: str) -> tuple[str, str]:
    """Resolve a user symbol to a SINGLE authoritative yfinance ticker +
    its exchange label, deterministically — the core of the tightened
    resolution.

    The bug this fixes: ``resolve_symbol`` blindly appends ``.NS`` to
    every plain ticker (India-first), and the old fetcher then silently
    fell back to the *bare* symbol when the ``.NS`` fetch came back empty.
    For a stock dual-listed as both an NSE scrip and a US ADR (INFY, WIT,
    …) that meant the calendar could read the NSE listing (EPS in ₹) while
    the verifier read the ADR (EPS in $) — different currency and scale,
    so a ``surprise_threshold_pct`` gate became unreliable.

    Tightened contract: an Indian listing is AUTHORITATIVE and we never
    cross currencies behind the user's back. International listings must
    be opted into explicitly via an exchange hint, so an ADR is only ever
    used when the user actually asked for it:

      ``NSE:INFY`` / ``INFY.NS``            -> ("INFY.NS", "NSE")
      ``BSE:INFY`` / ``INFY.BO`` / ``500209.BO`` -> (..., "BSE")
      ``NASDAQ:AAPL`` / ``NYSE:IBM`` / ``AAPL.US`` -> ("AAPL", "US")
      ``INFY`` (bare)                       -> ("INFY.NS", "NSE")  [India-first]

    Returns ``(yf_ticker, exchange_label)`` where exchange_label is one of
    ``NSE | BSE | US | INDEX``.
    """
    raw = (symbol or "").strip()
    upper = raw.upper()

    # Explicit exchange prefixes (EXCH:TICKER).
    if ":" in upper:
        prefix, _, rest = upper.partition(":")
        rest = rest.strip()
        if prefix in ("NSE", "NS"):
            return f"{rest}.NS", "NSE"
        if prefix in ("BSE", "BO"):
            return f"{rest}.BO", "BSE"
        if prefix in ("NASDAQ", "NYSE", "US", "AMEX"):
            return rest, "US"

    # Explicit suffixes.
    if upper.endswith(".NS"):
        return upper, "NSE"
    if upper.endswith(".BO"):
        return upper, "BSE"
    if upper.endswith(".US"):
        return upper[:-3], "US"
    if upper.startswith("^"):
        return upper, "INDEX"

    # No hint → defer to the shared India-first resolver (handles index
    # aliases + the curated NAME_TO_TICKER map, else appends .NS).
    try:
        from backend.market.yfinance_service import resolve_symbol
        resolved = resolve_symbol(raw)
    except Exception:  # noqa: BLE001
        resolved = f"{upper}.NS"
    ru = resolved.upper()
    if ru.startswith("^"):
        return resolved, "INDEX"
    if ru.endswith(".BO"):
        return resolved, "BSE"
    # Anything else from the resolver is treated as an NSE scrip — it only
    # ever emits .NS for equities, and we keep the listing INR-consistent.
    if not ru.endswith(".NS"):
        resolved = f"{ru}.NS"
    return resolved, "NSE"


def _fetch_yf_earnings_df(yf_mod: Any, ticker: str) -> Any:
    """Single yfinance ``get_earnings_dates`` call for a concrete ticker.
    Returns the DataFrame (possibly empty) or None on error.

    Earnings dates are yfinance-only (Kite has no forward-earnings feed), so
    this is bounded by a hard wall-clock — on a cloud IP the call would
    otherwise hang and stall whatever background job invoked it."""
    from backend.market.net_timeout import call_bounded

    def _fetch() -> Any:
        t = yf_mod.Ticker(ticker)
        get_dates = getattr(t, "get_earnings_dates", None)
        if callable(get_dates):
            return get_dates(limit=8)
        return getattr(t, "earnings_dates", None)  # very old yfinance

    return call_bounded(_fetch, timeout=8, default=None,
                        label=f"yf.earnings {ticker}")


def _default_fetch_yfinance_rows(symbol: str) -> list[dict[str, Any]]:
    """Default :func:`fetch_earnings_rows` provider — yfinance.

    Returns up to 8 rows, newest first (the natural yfinance order).
    Each row is a plain dict:
      ``{"report_date": datetime (UTC, aware),
         "eps_estimate": float | None,
         "reported_eps": float | None,
         "surprise_pct": float | None,
         "exchange": str}``

    Resolution is via :func:`_resolve_earnings_listing` — a single
    authoritative listing per symbol, never crossing currencies. For an
    Indian listing the only fallback is NSE→BSE (both ₹, so the numbers
    stay comparable); we do NOT fall back to a US ADR. A transient empty
    on the authoritative listing is retried ONCE before giving up. Any
    exception fails closed to an empty list — the verifier surfaces
    ``unknown`` and the scheduler simply re-checks on its next tick.
    """
    try:
        import yfinance as yf
    except Exception as exc:  # noqa: BLE001
        logger.warning("[earnings_calendar] yfinance import failed: %s", exc)
        return []

    resolved, exchange = _resolve_earnings_listing(symbol)

    # Candidate chain — currency-consistent only. NSE→BSE keeps ₹; never
    # an ADR. One retry of the primary absorbs a transient empty.
    candidates: list[tuple[str, str]] = [(resolved, exchange)]
    if exchange == "NSE":
        candidates.append((resolved, exchange))  # retry primary once
        bse = resolved[:-3] + ".BO" if resolved.endswith(".NS") else None
        if bse:
            candidates.append((bse, "BSE"))

    df = None
    used_exchange = exchange
    for cand_ticker, cand_exchange in candidates:
        df = _fetch_yf_earnings_df(yf, cand_ticker)
        if df is not None and not getattr(df, "empty", True):
            used_exchange = cand_exchange
            resolved = cand_ticker
            break

    if df is None or getattr(df, "empty", True):
        return []

    # df is a pandas DataFrame with a DatetimeIndex and columns like
    # "EPS Estimate", "Reported EPS", "Surprise(%)". Be defensive about
    # column names — yfinance has renamed these in past releases.
    cols = {str(c).strip().lower(): c for c in getattr(df, "columns", [])}
    est_col = (
        cols.get("eps estimate")
        or cols.get("epsestimate")
        or cols.get("estimate")
    )
    rep_col = (
        cols.get("reported eps")
        or cols.get("reportedeps")
        or cols.get("reported")
    )
    sur_col = (
        cols.get("surprise(%)")
        or cols.get("surprise %")
        or cols.get("surprise")
    )

    rows: list[dict[str, Any]] = []
    try:
        for idx, row in df.iterrows():
            when = _coerce_dt(idx)
            if when is None:
                continue
            eps_estimate = _coerce_float(row.get(est_col)) if est_col else None
            reported_eps = _coerce_float(row.get(rep_col)) if rep_col else None
            surprise_pct = _coerce_float(row.get(sur_col)) if sur_col else None
            rows.append({
                "report_date": when,
                "eps_estimate": eps_estimate,
                "reported_eps": reported_eps,
                "surprise_pct": surprise_pct,
                "exchange": used_exchange,
            })
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[earnings_calendar] row coercion failed sym=%s err=%s",
            resolved, exc,
        )
        return []

    return rows


def _serialise_rows(rows: list[dict[str, Any]]) -> str:
    payload = []
    for r in rows:
        dt = r.get("report_date")
        payload.append({
            "report_date": dt.isoformat() if isinstance(dt, datetime) else None,
            "eps_estimate": r.get("eps_estimate"),
            "reported_eps": r.get("reported_eps"),
            "surprise_pct": r.get("surprise_pct"),
            "exchange": r.get("exchange"),
        })
    return json.dumps(payload)


def _deserialise_rows(raw: str) -> list[dict[str, Any]]:
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    out: list[dict[str, Any]] = []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        dt_str = entry.get("report_date")
        dt: Optional[datetime] = None
        if isinstance(dt_str, str):
            try:
                dt = datetime.fromisoformat(dt_str)
            except ValueError:
                dt = None
        if dt is None:
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        out.append({
            "report_date": dt,
            "eps_estimate": _coerce_float(entry.get("eps_estimate")),
            "reported_eps": _coerce_float(entry.get("reported_eps")),
            "surprise_pct": _coerce_float(entry.get("surprise_pct")),
            "exchange": entry.get("exchange"),
        })
    return out


def fetch_earnings_rows(symbol: str) -> list[dict[str, Any]]:
    """Per-symbol earnings rows, Redis-cached ~12 h.

    See :func:`_default_fetch_yfinance_rows` for the row shape. The
    cache key is per-symbol so different workflows polling the same
    ticker only pay one yfinance call per half day.
    """
    sym = (symbol or "").strip().upper()
    if not sym:
        return []
    cache_key = _rows_cache_key(sym)
    try:
        cached = redis_client.get(cache_key)
    except Exception as exc:  # noqa: BLE001
        logger.debug("[earnings_calendar] cache read failed sym=%s err=%s",
                     sym, exc)
        cached = None
    if cached:
        raw = cached.decode() if isinstance(cached, (bytes, bytearray)) else cached
        rows = _deserialise_rows(raw)
        if rows:
            return rows

    rows = _default_fetch_yfinance_rows(sym)
    if rows:
        try:
            redis_client.set(cache_key, _serialise_rows(rows),
                             ex=_ROWS_CACHE_TTL_SECONDS)
        except Exception as exc:  # noqa: BLE001
            logger.debug("[earnings_calendar] cache write failed sym=%s err=%s",
                         sym, exc)
    return rows


# ── Calendar resolution ──────────────────────────────────────────────


def _rows_for(
    symbol: str,
    *,
    fetch: Optional[Callable[[str], list[dict[str, Any]]]],
) -> list[dict[str, Any]]:
    """Resolve rows via the (optional) injected provider, else
    :func:`fetch_earnings_rows`. Sorted ascending by report_date so the
    callers can do simple linear scans."""
    provider = fetch or fetch_earnings_rows
    try:
        rows = provider(symbol) or []
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[earnings_calendar] fetch provider raised sym=%s err=%s",
            symbol, exc,
        )
        return []
    rows = [r for r in rows if isinstance(r.get("report_date"), datetime)]
    rows.sort(key=lambda r: r["report_date"])
    return rows


def _row_to_def(
    symbol: str,
    row: dict[str, Any],
    *,
    verify_window_minutes: int,
) -> EarningsEventDef:
    sym = symbol.strip().upper()
    when: datetime = row["report_date"]
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    else:
        when = when.astimezone(timezone.utc)
    label = f"{sym} earnings ({when.date().isoformat()})"
    return EarningsEventDef(
        symbol=sym,
        report_at_utc=when,
        verify_window_minutes=verify_window_minutes,
        label=label,
    )


def get_next_earnings(
    symbol: str,
    *,
    now: datetime,
    verify_window_minutes: int = DEFAULT_VERIFY_WINDOW_MINUTES,
    fetch: Optional[Callable[[str], list[dict[str, Any]]]] = None,
) -> Optional[EarningsEventDef]:
    """The next upcoming earnings event for ``symbol`` strictly after
    ``now`` (used by the FE draft preview / chat planner). ``None`` if
    yfinance has nothing forward-dated."""
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    rows = _rows_for(symbol, fetch=fetch)
    for row in rows:
        when: datetime = row["report_date"]
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        if when > now:
            return _row_to_def(
                symbol, {**row, "report_date": when},
                verify_window_minutes=verify_window_minutes,
            )
    return None


def due_event(
    symbol: str,
    now: datetime,
    *,
    verify_window_minutes: int = DEFAULT_VERIFY_WINDOW_MINUTES,
    fetch: Optional[Callable[[str], list[dict[str, Any]]]] = None,
) -> Optional[EarningsEventDef]:
    """The occurrence whose verify window currently contains ``now``,
    i.e. ``report_at <= now <= report_at + verify_window``.

    ``None`` if no event is in its window right now. If multiple overlap
    (they shouldn't, given quarterly cadence) the earliest is returned —
    matches the macro calendar's tie-break.
    """
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    rows = _rows_for(symbol, fetch=fetch)
    for row in rows:
        when: datetime = row["report_date"]
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        end = when + timedelta(minutes=verify_window_minutes)
        if when <= now <= end:
            return _row_to_def(
                symbol, {**row, "report_date": when},
                verify_window_minutes=verify_window_minutes,
            )
    return None
