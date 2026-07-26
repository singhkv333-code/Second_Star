"""Backfill full 1-minute Kite history for a symbol into charto/data/charto_bars.db.

Standalone sandbox script (Charto preview). Reuses the Pivot backend's live
Kite session + instrument resolution. Measured limits (memory 2026-07-22):
1-min archive floor = 2015-02-02, per-request cap 60d, 3 workers = 3 req/s
sweet spot (~27s per stock, ~1.06M bars).

Run:  cd pivot && source .venv/bin/activate && python ../charto/data/backfill_1min.py RELIANCE
"""
from __future__ import annotations

import sqlite3
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "pivot"))

from backend.database import SessionLocal                      # noqa: E402
from backend.brokers.sessions import get_active_kite_session   # noqa: E402
from backend.kite.auth import (                                # noqa: E402
    get_authenticated_kite, read_kite_access_token,
)
from backend.kite.historical import _resolve_instrument_token  # noqa: E402

DB_PATH = Path(__file__).parent / "charto_bars.db"
FLOOR = datetime(2015, 2, 2, tzinfo=timezone.utc)   # measured Kite 1-min floor
WINDOW_DAYS = 55                                     # cap is 60; stay safe
WORKERS = 3                                          # measured sweet spot


def get_token() -> str:
    db = SessionLocal()
    try:
        session = get_active_kite_session(db)
    finally:
        db.close()
    if session is None:
        raise SystemExit("no active Kite session — login first")
    tok = read_kite_access_token(session)
    if not tok or tok.startswith("mock_") or len(tok) < 20:
        raise SystemExit("Kite token is mock/expired — re-login")
    return tok


def main(symbol: str) -> None:
    t0 = time.time()
    token = get_token()
    instrument = _resolve_instrument_token(symbol, "NSE", token)
    if instrument is None:
        raise SystemExit(f"could not resolve instrument token for {symbol}")
    print(f"{symbol}: instrument_token={instrument}")

    now = datetime.now(timezone.utc)
    windows: list[tuple[datetime, datetime]] = []
    start = FLOOR
    while start < now:
        end = min(start + timedelta(days=WINDOW_DAYS), now)
        windows.append((start, end))
        start = end
    print(f"{len(windows)} windows of {WINDOW_DAYS}d")

    # Written from worker threads under `lock`; SQLite's same-thread guard
    # is safe to relax because every use is serialized by that lock.
    con = sqlite3.connect(DB_PATH, check_same_thread=False)
    con.execute(
        "CREATE TABLE IF NOT EXISTS bars ("
        " symbol TEXT NOT NULL, ts INTEGER NOT NULL,"
        " o REAL, h REAL, l REAL, c REAL, v INTEGER,"
        " PRIMARY KEY (symbol, ts))"
    )
    con.execute("CREATE INDEX IF NOT EXISTS ix_bars_sym_ts ON bars(symbol, ts)")
    con.commit()

    lock = threading.Lock()
    queue = list(enumerate(windows))
    inserted = [0]
    errors: list[str] = []

    def worker() -> None:
        kite = get_authenticated_kite(token)
        while True:
            with lock:
                if not queue:
                    return
                idx, (frm, to) = queue.pop(0)
            try:
                candles = kite.historical_data(
                    instrument_token=instrument, from_date=frm, to_date=to,
                    interval="minute", continuous=False, oi=False,
                )
            except Exception as exc:  # noqa: BLE001
                with lock:
                    errors.append(f"win{idx} {frm:%Y-%m-%d}: {exc}")
                time.sleep(1.0)
                continue
            rows = [
                (symbol, int(c["date"].timestamp()),
                 float(c["open"]), float(c["high"]),
                 float(c["low"]), float(c["close"]),
                 int(c.get("volume", 0) or 0))
                for c in (candles or [])
            ]
            with lock:
                con.executemany(
                    "INSERT OR REPLACE INTO bars VALUES (?,?,?,?,?,?,?)", rows)
                con.commit()
                inserted[0] += len(rows)
                done = len(windows) - len(queue)
                print(f"  win {idx:2d} {frm:%Y-%m-%d}→{to:%Y-%m-%d}: "
                      f"{len(rows):6d} bars  ({done}/{len(windows)})")
            time.sleep(0.35)  # per-thread pacing → ~3 req/s across 3 workers

    threads = [threading.Thread(target=worker) for _ in range(WORKERS)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    n, lo, hi = con.execute(
        "SELECT COUNT(*), MIN(ts), MAX(ts) FROM bars WHERE symbol=?",
        (symbol,)).fetchone()
    print(f"\nDONE {symbol}: {n:,} bars "
          f"{datetime.fromtimestamp(lo, tz=timezone.utc):%Y-%m-%d} → "
          f"{datetime.fromtimestamp(hi, tz=timezone.utc):%Y-%m-%d %H:%M} UTC "
          f"in {time.time() - t0:.1f}s; errors={len(errors)}")
    for e in errors[:5]:
        print("  ERR", e)
    con.close()


if __name__ == "__main__":
    main((sys.argv[1] if len(sys.argv) > 1 else "RELIANCE").upper())
