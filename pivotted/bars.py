"""Daily bars for companies outside Charto's archive.

Charto stores minute bars for ~557 symbols, because a chart's universe is
bounded by what it can render. Research is not: the filings side already
reaches all 11,256 listed companies, so a question that starts fundamental
("this screen threw up a name I don't know") and turns technical ("is it
trending?") fell off a cliff at symbol 558 — and the honest refusal it got
was correct but unhelpful.

Nothing else in reach covers the gap. mc.daily_prices sounds like the answer
and holds 12,918 rows for NINE companies. What DOES cover it is Pivot's
`fetch_price_history`, which is Kite-first with a yfinance fallback and works
for any NSE ticker.

So the gap is closed by fetching daily bars on demand and writing them into
the same `bars_1d` table Charto's own daily path already reads — see
`dataserver._daily`, which reads bars_1d and folds only the newer minutes on
top. A symbol with no minutes simply has no tail to fold. Every daily tool —
indicators, levels, trendlines, patterns, gaps, results, screens — then works
unchanged, because none of them know where the row came from.

What this does NOT do, and must never claim: intraday. 5m/15m/1h tools read
the minute table, and one row per day cannot be resampled into it. Those stay
archive-only, and `tools._call` says so rather than serving a daily bar under
an intraday name.
"""
from __future__ import annotations

import logging
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
PIVOT = HERE.parent / "pivot"

IST_OFF = 19800          # bars_1d timestamps are IST-shifted, as Charto writes them
_SESSION_OPEN = 9 * 3600 + 15 * 60

# One fetch per symbol per process. A miss is cached too: a company with no
# price history anywhere should cost one lookup, not one per tool call in a
# turn that fans out.
_lock = threading.Lock()
_locks: dict[str, threading.Lock] = {}
_tried: dict[str, bool] = {}


_yf_mod = None
_yf_lock = threading.Lock()


def _yf():
    """Pivot's price service, imported once under a lock.

    The lock is not paranoia. `backend.market.yfinance_service` participates
    in a circular import, so when two tool workers raced into it the loser got
    "cannot import name 'fetch_price_history' from partially initialized
    module" and the call failed — while the very next call in the same turn
    succeeded, because by then the module had finished loading. An
    intermittent failure that heals on retry is worse than a consistent one:
    it makes a real capability look flaky.
    """
    global _yf_mod
    if _yf_mod is not None:
        return _yf_mod
    with _yf_lock:
        if _yf_mod is None:
            if str(PIVOT) not in sys.path:
                sys.path.insert(0, str(PIVOT))
            try:
                from dotenv import load_dotenv
                load_dotenv(PIVOT / ".env")
            except ImportError:
                pass
            # The cycle can leave the module half-built when this import
            # lands mid-way through another module's chain, so a lock alone
            # is not enough — the FIRST importer can be the one that fails.
            # One retry is all it takes; by then the outer import has
            # completed. `warm()` at boot normally makes this moot.
            for attempt in (1, 2):
                try:
                    from backend.market import yfinance_service as yfs
                    _yf_mod = yfs
                    break
                except ImportError:
                    if attempt == 2:
                        raise
                    time.sleep(0.25)
    return _yf_mod


def warm() -> bool:
    """Resolve the price-service import once, single-threaded, at boot."""
    try:
        _yf()
        return True
    except Exception as exc:                        # noqa: BLE001
        logging.warning("pivotted: price service not available (%s)", exc)
        return False


def _ts(date_str: str) -> int | None:
    """'YYYY-MM-DD' -> the IST-shifted epoch Charto stores for a daily bar.

    Charto's daily rows are stamped at the session OPEN in IST-shifted epoch
    (see _fold_daily / _bucket_stamp). Getting this wrong does not error —
    it silently puts every bar on the wrong day, which is worse.
    """
    try:
        d = datetime.strptime(date_str[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None
    return int(d.timestamp()) - IST_OFF + _SESSION_OPEN


def have_daily(con, symbol: str) -> bool:
    return bool(con.execute(
        "SELECT 1 FROM bars_1d WHERE symbol=? LIMIT 1", (symbol,)).fetchone())


def ensure_daily(con, symbol: str, period: str = "5y") -> dict:
    """Fetch and store daily bars for `symbol`. Returns a small status dict.

    Writes through Charto's own connection so the rows land in the same WAL
    the reader is using; `import_universe_daily.py` already writes this table
    the same way, so the shape and the concurrency story are established.
    """
    sym = (symbol or "").upper().strip()
    if not sym:
        return {"ok": False, "reason": "no symbol"}
    if have_daily(con, sym):
        return {"ok": True, "cached": True}
    with _lock:
        lk = _locks.setdefault(sym, threading.Lock())
    with lk:
        if have_daily(con, sym):
            return {"ok": True, "cached": True}
        if _tried.get(sym) is False:
            return {"ok": False, "reason": "no price history found"}
        t0 = time.time()
        try:
            rows = _yf().fetch_price_history(sym, period, "1d")
        except Exception as exc:                    # noqa: BLE001
            logging.warning("pivotted: bar fetch failed for %s (%s)", sym, exc)
            return {"ok": False, "reason": f"price fetch failed: {exc}"}
        packed = []
        for r in rows:
            ts = _ts(r.get("date", ""))
            if ts is None:
                continue
            packed.append((sym, ts, r.get("open"), r.get("high"),
                           r.get("low"), r.get("close"), int(r.get("volume") or 0)))
        if len(packed) < 30:
            # Too short to compute anything a research answer would lean on —
            # a 200-day SMA off 12 bars is not a number, it is a shape.
            _tried[sym] = False
            return {"ok": False, "reason": "no usable price history",
                    "bars": len(packed)}
        con.executemany(
            "INSERT OR IGNORE INTO bars_1d(symbol,ts,o,h,l,c,v) VALUES (?,?,?,?,?,?,?)",
            packed)
        con.commit()
        _tried[sym] = True
        logging.info("pivotted: hydrated %s with %d daily bars in %.2fs",
                     sym, len(packed), time.time() - t0)
        return {"ok": True, "bars": len(packed),
                "took_s": round(time.time() - t0, 2)}
