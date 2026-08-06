"""Backfill 1-minute Kite history for indices / India VIX / MCX metals / INR pairs.

The macro counterpart to backfill_1min.py (equities) and backfill_crypto.py.
Identical storage contract — one row per (symbol, minute) in `bars`, epoch
seconds UTC — so every other interval stays a read-time fold in the dataserver.

Two instrument shapes, resolved live from the instrument master because
futures roll:
  * indices (segment INDICES) — never expire, continuous=False, volume is
    always 0 because an index is computed rather than traded.
  * futures (MCX-FUT metals/energy, CDS-FUT currency) — each contract only
    holds data for its own life. Kite REJECTS continuous=True on minute data
    ("invalid interval for continuous data" — it is a daily+ feature), and
    expired contracts are absent from the instrument master, so their history
    is simply unreachable. Measured 2026-07-29: the FRONT contract is the
    deepest of the listed ones (USDINR26JULFUT carries 2025-07-29 onward,
    later expiries start progressively later), so ~12-13 months is the hard
    ceiling for 1-minute futures history. FUT_LOOKBACK_DAYS reflects that.

Run:  cd pivot && source .venv/bin/activate
      python ../charto/data/backfill_macro.py            # all 37
      python ../charto/data/backfill_macro.py indices    # one group
      python ../charto/data/backfill_macro.py "NIFTY 50" GOLD USDINR
"""
from __future__ import annotations

import re
import sqlite3
import sys
from os import environ
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

# CHARTO_DB retargets the store, same contract as topup_1min.py. Without it
# this file could only ever fill the store it sits next to, so the remote
# catch-up (catchup_remote.py — seed a throwaway DB with the remote's
# watermarks, fetch only its missing windows, ship those) reached the 500 NSE
# equities and left the 37 macro symbols — every index, the four INR pairs and
# the MCX commodities (now 13: bullion/base-metals/energy/agri) — two
# sessions stale with no way to close them.
DB_PATH = Path(environ.get("CHARTO_DB") or (Path(__file__).parent / "charto_bars.db"))
# Kite's client formats a datetime as a bare "%Y-%m-%d %H:%M:%S" string and
# DROPS tzinfo, so the server reads whatever fields it is given as IST. A UTC
# `now` therefore asks for "up to 13:13" and Kite hears 13:13 IST, silently
# truncating exactly 5:30 of the current session. Measured 2026-08-03 on
# GOLD: UTC bound -> 254 bars ending 13:13, IST bound -> 584 ending 18:43.
# It never showed on old history because those windows end in the past.
IST_TZ = timezone(timedelta(hours=5, minutes=30))
FLOOR = datetime(2015, 2, 2, tzinfo=IST_TZ)   # measured Kite 1-min floor
FUT_LOOKBACK_DAYS = 400   # a listed future's whole life; older contracts are gone
WINDOW_DAYS = 55          # cap is 60; stay safe
WORKERS = 3               # measured sweet spot — 3 req/s is where Kite stops 429ing

INDICES = [
    "NIFTY 50", "NIFTY BANK", "INDIA VIX", "NIFTY IT", "NIFTY AUTO",
    "NIFTY PHARMA", "NIFTY FMCG", "NIFTY METAL", "NIFTY REALTY",
    "NIFTY ENERGY", "NIFTY INFRA", "NIFTY PSU BANK", "NIFTY PVT BANK",
    "NIFTY FIN SERVICE", "NIFTY MIDCAP 100", "NIFTY SMLCAP 100",
    "NIFTY NEXT 50", "NIFTY 100", "NIFTY 500", "NIFTY CONSUMPTION",
    "NIFTY MEDIA", "NIFTY COMMODITIES", "SENSEX", "BANKEX",
]
METALS = ["GOLD", "GOLDM", "SILVER", "SILVERM", "CRUDEOIL",
          "NATURALGAS", "COPPER", "ZINC", "ALUMINIUM",
          "LEAD", "NICKEL", "COTTON", "MENTHAOIL"]           # MCX-FUT
CURRENCY = ["USDINR", "EURINR", "GBPINR", "JPYINR"]         # CDS-FUT

_lock = threading.Lock()


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


# A MONTHLY contract ends in a three-letter month (USDINR26AUGFUT); a WEEKLY
# ends in the expiry date (USDINR26807FUT). The distinction is not cosmetic:
# the CDS segment lists weeklies that carry NO minute history at all, so
# "nearest expiry" silently resolves to a dead contract and every top-up
# afterwards fetches zero rows while reporting success. Measured 2026-08-02
# over 20-31 Jul: USDINR26807FUT / 26814FUT / 26821FUT returned 0 bars each,
# USDINR26AUGFUT returned 4,789 across 10 full sessions. MCX never showed the
# bug only because its nearest contract happens to be the liquid one.
_MONTHLY_FUT = re.compile(r"[A-Z]{3}FUT$")


def resolve(kite, wanted: set[str]) -> list[tuple[str, int, bool]]:
    """-> [(symbol, instrument_token, is_future)]. Futures pick the nearest
    MONTHLY contract, falling back to nearest expiry when a name lists no
    monthly at all."""
    inst = kite.instruments()
    out: list[tuple[str, int, bool]] = []
    for name in INDICES:
        if wanted and name not in wanted:
            continue
        hit = [i for i in inst
               if i["segment"] == "INDICES" and i["tradingsymbol"].upper() == name]
        if hit:
            out.append((name, hit[0]["instrument_token"], False))
        else:
            print(f"  ! index not found: {name}")
    # Candidate monthly contracts per name, then ONE batched quote call to
    # pick by liquidity. Nearest expiry is the wrong rule near a roll: on
    # 2026-08-03 GOLD26AUGFUT expired in two days and had traded 717 lots with
    # its last print at 18:52, while GOLD26OCTFUT had 3,371 and was still
    # printing at 19:00 — so "front month" put a dying contract on the chart
    # and into the store. GOLDM was worse: 6,676 against the next month's
    # 19,414. Liquidity migrates before the calendar does, which is why every
    # vendor rolls a continuous series on volume.
    cands: dict[str, list[dict]] = {}
    for group, seg in ((METALS, "MCX-FUT"), (CURRENCY, "CDS-FUT")):
        for name in group:
            if wanted and name not in wanted:
                continue
            contracts = [i for i in inst
                         if i["segment"] == seg and i["name"] == name and i.get("expiry")]
            if not contracts:
                print(f"  ! {seg} not found: {name}")
                continue
            monthly = [i for i in contracts
                       if _MONTHLY_FUT.search(i["tradingsymbol"].upper())]
            if not monthly:
                print(f"  ! {name}: no monthly contract listed — using nearest "
                      f"expiry; verify it carries bars")
            cands[name] = sorted(monthly or contracts,
                                 key=lambda x: str(x["expiry"]))[:3]

    vols: dict[str, float] = {}
    keys = [f"{i['exchange']}:{i['tradingsymbol']}"
            for c in cands.values() for i in c]
    if keys:
        try:                      # one call for every candidate, not one each
            for key, q in (kite.quote(keys) or {}).items():
                vols[key.split(":", 1)[1]] = float(q.get("volume") or 0)
        except Exception as exc:  # noqa: BLE001
            print(f"  ! quote for roll selection failed ({exc}); "
                  f"falling back to nearest expiry")

    for name, cs in cands.items():
        if vols:
            front = max(cs, key=lambda i: vols.get(i["tradingsymbol"], 0.0))
            if front is not cs[0]:
                print(f"  {name}: rolled to {front['tradingsymbol']} "
                      f"(vol {vols.get(front['tradingsymbol'], 0):,.0f}) over "
                      f"{cs[0]['tradingsymbol']} "
                      f"(vol {vols.get(cs[0]['tradingsymbol'], 0):,.0f})")
        else:
            front = cs[0]
        out.append((name, front["instrument_token"], True))
    return out


def _connect() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=60)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=OFF")
    con.execute("PRAGMA busy_timeout=30000")
    con.execute(
        "CREATE TABLE IF NOT EXISTS bars ("
        " symbol TEXT NOT NULL, ts INTEGER NOT NULL,"
        " o REAL, h REAL, l REAL, c REAL, v INTEGER,"
        " PRIMARY KEY (symbol, ts))"
    )
    con.commit()
    return con


def backfill(con, token: str, symbol: str, instrument: int, is_future: bool) -> None:
    t0 = time.time()
    now = datetime.now(IST_TZ)
    floor = now - timedelta(days=FUT_LOOKBACK_DAYS) if is_future else FLOOR
    row = con.execute("SELECT MAX(ts) FROM bars WHERE symbol=?", (symbol,)).fetchone()
    start = (datetime.fromtimestamp(row[0], IST_TZ) - timedelta(days=2)
             if row and row[0] else floor)
    if start >= now - timedelta(minutes=5):
        print(f"  {symbol:20s} already current")
        return

    windows: list[tuple[datetime, datetime]] = []
    cur = start
    while cur < now:
        end = min(cur + timedelta(days=WINDOW_DAYS), now)
        windows.append((cur, end))
        cur = end

    queue = list(enumerate(windows))
    total = [0]
    errors: list[str] = []

    def worker() -> None:
        kite = get_authenticated_kite(token)
        while True:
            with _lock:
                if not queue:
                    return
                idx, (frm, to) = queue.pop(0)
            try:
                candles = kite.historical_data(
                    instrument_token=instrument, from_date=frm, to_date=to,
                    interval="minute", continuous=False, oi=False,
                )
            except Exception as exc:                       # noqa: BLE001
                with _lock:
                    errors.append(f"{frm:%Y-%m-%d}: {str(exc)[:60]}")
                time.sleep(1.0)
                continue
            rows = [
                (symbol, int(c["date"].timestamp()),
                 float(c["open"]), float(c["high"]), float(c["low"]),
                 float(c["close"]), int(c.get("volume", 0) or 0))
                for c in (candles or [])
            ]
            with _lock:
                if rows:
                    con.executemany(
                        "INSERT OR REPLACE INTO bars VALUES (?,?,?,?,?,?,?)", rows)
                    con.commit()
                    total[0] += len(rows)
            time.sleep(0.35)     # per-thread pacing -> ~3 req/s across 3 workers

    threads = [threading.Thread(target=worker) for _ in range(WORKERS)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    n, lo, hi = con.execute(
        "SELECT COUNT(*), MIN(ts), MAX(ts) FROM bars WHERE symbol=?", (symbol,)).fetchone()
    if n:
        print(f"DONE {symbol:20s} {n:>9,} bars  "
              f"{datetime.fromtimestamp(lo, timezone.utc):%Y-%m-%d} -> "
              f"{datetime.fromtimestamp(hi, timezone.utc):%Y-%m-%d %H:%M}  "
              f"{time.time() - t0:5.0f}s  err={len(errors)}", flush=True)
    else:
        print(f"EMPTY {symbol:20s} (no data returned)  err={len(errors)}", flush=True)
    for e in errors[:3]:
        print("      ERR", e)


def main(argv: list[str]) -> None:
    groups = {"indices": INDICES, "metals": METALS, "currency": CURRENCY}
    wanted: set[str] = set()
    for a in argv:
        if a.lower() in groups:
            wanted |= set(groups[a.lower()])
        else:
            wanted.add(a.upper() if a.upper() in METALS + CURRENCY else a)

    token = get_token()
    kite = get_authenticated_kite(token)
    targets = resolve(kite, wanted)
    print(f"{len(targets)} instruments to backfill\n", flush=True)

    con = _connect()
    t0 = time.time()
    for symbol, instrument, is_future in targets:
        backfill(con, token, symbol, instrument, is_future)
    print(f"\nALL DONE in {time.time() - t0:.0f}s", flush=True)
    con.close()


if __name__ == "__main__":
    main(sys.argv[1:])
