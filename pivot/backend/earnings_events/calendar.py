"""Per-symbol earnings calendar, fed from yfinance.

The calendar's job is to answer: *for symbol ``X``, is a fresh earnings
release within the verification window right now?* Unlike the macro
calendar (RBI / FOMC dates are hardcoded a year out), earnings dates are
per-ticker and shift quarter-to-quarter, so the registry is *live*:
:func:`fetch_earnings_rows` pulls the next 8 rows from yfinance's
``get_earnings_dates`` and caches them in Redis for ~12 h.

Public surface mirrors :mod:`backend.macro_events.calendar`:

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


def _default_fetch_yfinance_rows(symbol: str) -> list[dict[str, Any]]:
    """Default :func:`fetch_earnings_rows` provider — yfinance.

    Returns up to 8 rows, newest first (the natural yfinance order).
    Each row is a plain dict:
      ``{"report_date": datetime (UTC, aware),
         "eps_estimate": float | None,
         "reported_eps": float | None,
         "surprise_pct": float | None}``

    Indian tickers (INFY, TCS, …) are routed through
    :func:`backend.market.yfinance_service.resolve_symbol` so they pick
    up the ``.NS`` suffix yfinance needs. Any exception fails closed to
    an empty list — the verifier will then surface ``unknown``.
    """
    try:
        from backend.market.yfinance_service import resolve_symbol
    except Exception:  # noqa: BLE001
        def resolve_symbol(s: str) -> str:  # type: ignore[no-redef]
            return s

    try:
        import yfinance as yf
    except Exception as exc:  # noqa: BLE001
        logger.warning("[earnings_calendar] yfinance import failed: %s", exc)
        return []

    resolved = resolve_symbol(symbol)
    rows: list[dict[str, Any]] = []
    df = None
    try:
        ticker = yf.Ticker(resolved)
        get_dates = getattr(ticker, "get_earnings_dates", None)
        if callable(get_dates):
            df = get_dates(limit=8)
        else:  # very old yfinance — fall back to the attribute form
            df = getattr(ticker, "earnings_dates", None)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[earnings_calendar] yfinance fetch failed sym=%s err=%s",
            resolved, exc,
        )
        df = None

    if df is None or getattr(df, "empty", True):
        # Retry without the .NS suffix in case the resolver added it but
        # the listing is on a US ADR / different exchange.
        if resolved.endswith(".NS"):
            bare = resolved[:-3]
            try:
                ticker = yf.Ticker(bare)
                get_dates = getattr(ticker, "get_earnings_dates", None)
                if callable(get_dates):
                    df = get_dates(limit=8)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "[earnings_calendar] yfinance fallback failed sym=%s err=%s",
                    bare, exc,
                )
                df = None

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
