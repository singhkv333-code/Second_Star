"""Price history from the chart server's own store (the dataserver `/bars`).

The chart server keeps every NSE/BSE minute, the indices, MCX and crypto, fed
by the Kite stream, and resamples on request. It runs on the same machine, so
a read is a local call measured in milliseconds, against 3+ seconds for a
yfinance round trip (and nothing at all since Yahoo began answering this
server 401 in October 2026). Routers that draw a line ask here first and fall
back to their remote sources only for what this store does not hold.
"""
from __future__ import annotations

import json
import logging
import os
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Optional
from urllib.parse import quote

logger = logging.getLogger(__name__)

_BASE = (os.environ.get("PIVOT_INTERNAL_URL") or "http://127.0.0.1:5174").rstrip("/")
IST = 19800

# range -> (the dataserver interval, bars to ask for, seconds back from the newest bar;
# 0 = the newest IST session only)
RANGES: dict[str, tuple[str, int, int]] = {
    "1D": ("5m", 120, 0),
    "1W": ("30m", 120, 7 * 86400),
    "1M": ("1d", 40, 31 * 86400),
    "3M": ("1d", 80, 92 * 86400),
    "6M": ("1d", 150, 183 * 86400),
    "1Y": ("1d", 280, 366 * 86400),
    "5Y": ("1w", 280, 5 * 366 * 86400),
}


def bars(symbol: str, interval: str, limit: int, timeout: float = 4.0) -> list[dict]:
    """`[{t,o,h,l,c,v}, ...]` oldest first, or [] when the store has none."""
    try:
        with urllib.request.urlopen(
            f"{_BASE}/bars?symbol={quote(symbol)}&interval={interval}&limit={limit}",
            timeout=timeout,
        ) as r:
            return json.loads(r.read()).get("bars") or []
    except Exception as exc:  # noqa: BLE001 — absence is an answer here
        logger.debug("dataserver bars %s %s: %s", symbol, interval, exc)
        return []


def series(symbol: str, rng: str) -> list[tuple[int, float]]:
    """`[(unix_ts, close), ...]` for one of RANGES, or []."""
    interval, limit, back = RANGES[rng]
    rows = bars(symbol, interval, limit)
    if not rows:
        return []
    last = int(rows[-1]["t"])
    if back:
        keep = [b for b in rows if int(b["t"]) >= last - back]
    else:
        day = (last + IST) // 86400
        keep = [b for b in rows if (int(b["t"]) + IST) // 86400 == day]
    return [(int(b["t"]), float(b["c"])) for b in keep if b.get("c") is not None]


def session_closes(symbols: list[str], interval: str = "15m") -> dict[str, list[float]]:
    """The newest session's closes for many symbols at once (thumbnails)."""
    def one(sym: str) -> Optional[list[float]]:
        rows = bars(sym, interval, 60)
        if not rows:
            return None
        day = (int(rows[-1]["t"]) + IST) // 86400
        pts = [round(float(b["c"]), 4) for b in rows if (int(b["t"]) + IST) // 86400 == day]
        return pts if len(pts) >= 3 else None

    if not symbols:
        return {}
    with ThreadPoolExecutor(max_workers=min(12, len(symbols))) as ex:
        got = list(ex.map(one, symbols))
    return {s: p for s, p in zip(symbols, got) if p}


def quotes(symbols: list[str], timeout: float = 4.0) -> dict[str, dict]:
    """`{symbol: {last, prev_close, change, change_pct, as_of, ...}}` from the
    chart server's batch quote. As fresh as its stream; {} when unreachable."""
    if not symbols:
        return {}
    try:
        with urllib.request.urlopen(
            f"{_BASE}/quotes?symbols={quote(','.join(symbols))}", timeout=timeout,
        ) as r:
            rows = json.loads(r.read()).get("quotes") or []
    except Exception as exc:  # noqa: BLE001
        logger.debug("dataserver quotes %s: %s", symbols, exc)
        return {}
    return {q["symbol"]: q for q in rows if q.get("symbol") and q.get("last") is not None}
