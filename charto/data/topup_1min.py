"""Incremental 1-minute top-up for the NSE equities already in charto_bars.db.

backfill_1min.py always restarts from the measured 2015-02-02 archive floor:
~70 windows per symbol, ~27s a stock, ~3.7 hours for 500 names. That is the
right shape for a first load and the wrong shape for a daily catch-up, where
the gap is a handful of sessions. This script closes only the gap. The whole
outage as of 2026-08-02 is seven sessions, comfortably inside Kite's 60-day
per-request cap, so it is ONE minute request per symbol — ~3 minutes for 500
names at the measured 3 req/s. Same storage contract as the backfills: one row
per (symbol, minute) in `bars`, epoch seconds UTC, INSERT OR REPLACE.

Three rules, each earned from a specific failure:

1. Overlap, never abut. The fetch starts at the last session we know closed
   CLEANLY, not at MAX(ts)+60s. Writes are INSERT OR REPLACE, so re-requesting
   the trailing day costs nothing and REPAIRS a truncated tail in place instead
   of appending after it — which is what RELIANCE needs, its history stopping
   at 2026-07-23 14:29 where a backfill died mid-session. backfill_macro.py's
   `MAX(ts) - 2 days` restart point is the same instinct.

2. Distrust the trailing session on every run. A full NSE session's last 1-min
   bar OPENS at 15:29 IST; a trailing session that ends earlier is presumed
   truncated and is re-fetched whole. The asymmetry is deliberate — a false
   partial (an illiquid name with no trade in the closing minute, so Kite emits
   no bar) costs nothing, because the request that repairs it was already being
   made; a false complete leaves a permanently truncated day that no later run
   ever revisits.

3. Minute bars are RAW, Kite's daily endpoint is corporate-action ADJUSTED.
   That is why `bars` and `bars_1d` are separately sourced and must never be
   folded into each other. After topping a symbol up this folds the new minutes
   to daily and compares each session's close against Kite's own daily candle.

   A ONE-DAY COMPARISON CANNOT BE USED, and this is the whole reason the file
   does the harder thing: NSE's official daily close is the VWAP of the last 30
   minutes of trading, while the minute-fold close is the LAST TRADED PRICE at
   15:29. They are different quantities by construction, so they disagree by a
   few bps every single session, at random, forever. Measured 2026-08-02: a
   flat ">0.1% on any session" rule fires on 24 of 41 symbols — a 60%
   false-positive rate, a detector that cries wolf on every run.

   A corporate action rescales the ENTIRE adjusted series by one factor, so its
   signature is a ratio that is CONSTANT ACROSS SESSIONS, not a ratio that is
   large on one session. So the test is STABILITY, not magnitude — see
   classify_ratios() for the measured series both verdicts were fitted to.
   Stable-and-large means a split/bonus (a visible permanent step in the
   intraday chart) and is reported loudly; stable-and-small means Kite's daily
   series is dividend-adjusted and its minute series is raw, which is true for
   everyone and is what TradingView shows too — a one-line note, never an
   error, never a refetch.

   Flag only, always. This script never auto-refetches and never rewrites a
   price: the fix for a split is a full minute-history refetch
   (backfill_1min.py), and that is a decision for a human, not a side effect
   of a catch-up job.

Indices / MCX / FX belong to backfill_macro.py and crypto to
backfill_crypto.py — both already incremental. They are skipped by name here.

Run:  cd pivot && .venv/bin/python ../charto/data/topup_1min.py
      cd pivot && .venv/bin/python ../charto/data/topup_1min.py RELIANCE INFY
      python3 ../charto/data/topup_1min.py --dry-run    # no Kite, no writes
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import date, datetime, time as dtime, timedelta, timezone
from os import environ
from pathlib import Path
from statistics import fmean, pstdev

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))                          # sibling dataserver
sys.path.insert(0, str(_HERE.parents[1] / "pivot"))     # pivot backend

# CHARTO_DB retargets the store. The catch-up a REMOTE host needs is not the
# one this laptop needs, and copying 13 GB across to find out is absurd — so we
# seed a throwaway DB with the remote's watermarks (one anchor row per symbol),
# run this script against THAT, and ship only the rows it fetched. plan_symbol
# reads MAX(ts)/sync_state and nothing else, so a seeded DB plans exactly the
# windows the remote is missing. Measured 2026-08-03: 500 NSE names, 22 Jul ->
# 03 Aug, 1.5M bars fetched locally and shipped as 20 MB instead of 13 GB.
DB_PATH = Path(environ.get("CHARTO_DB") or (_HERE / "charto_bars.db"))
IST = timezone(timedelta(hours=5, minutes=30))

WINDOW_DAYS = 55        # measured cap is 60; stay safe (same as the backfills)
WORKERS = 3             # measured sweet spot — 3 req/s is where Kite stops 429ing
PACE = 0.35             # per-thread pacing -> ~3 req/s across 3 workers
# WORKERS/PACE above are tuned for backfill_1min.py's shape: ~70 windows of 55
# DAYS per symbol, where each response is megabytes and Kite does throttle.
# A top-up's window is a handful of sessions, and there the ceiling is far
# higher. Measured 2026-08-03 against the live session, higher-concurrency
# config run FIRST each time so a drained bucket cannot flatter it:
#     workers=3  pace=0.35 ->  4.96 req/s   0 failures
#     workers=6  pace=0    -> 10.14 req/s   0 failures
#     workers=8  pace=0    -> 13.41 req/s   0 failures
#     workers=16 pace=0    -> 10.10 req/s   0 failures  (no better, just noisier)
# So the plateau is ~13 req/s at 8 workers and MORE THREADS DO NOT HELP past
# that. Pass --workers 8 --pace 0 for a catch-up; leave the defaults alone for
# a cold backfill, where the request shape is different and untested at speed.
MAX_ATTEMPTS = 4        # a 429'd window is retried, never dropped (see worker)
BACKOFF_BASE = 0.5      # 0.5s, 1s, 2s — Kite's historical throttle is per-second

# How far back to look for a cleanly-closed session before widening. Ten
# trading days of holidays and a long weekend still fit inside 21 calendar days.
TAIL_LOOKBACK_DAYS = 21
TAIL_LOOKBACK_WIDE = 90

# ── corporate-action detector thresholds ──────────────────────────
# Every number here was fitted to the measured ratio series in
# classify_ratios()' docstring, not chosen for roundness.
CA_MIN_SESSIONS = 5      # below this the stdev of a ratio series means nothing
CA_LOOKBACK_DAYS = 21    # widen the daily request so a steady-state top-up
                         # (1-2 new sessions) still has 5+ ratios to test
CA_STDEV_MAX = 0.0020    # ABBOTINDIA's real offset sits at 0.0013; BPCL's
                         # noisiest week at 0.0047
CA_K = 3.0               # |mean - 1| must clear 3 stdevs to be a real offset
CA_SPLIT_MIN = 0.05      # beyond 5% off 1.0 it is a split/bonus, not a dividend
CA_SEGMENT_MIN = 3       # points needed either side of a mid-gap step

# Fallback only — dataserver.session_close_for is the owner (see below).
NSE_CLOSE_MIN = 15 * 60 + 29

# ── instrument families ───────────────────────────────────────────
# Mirrors dataserver.session_for / scope_for so the two agree on what an NSE
# equity is. Deliberately duplicated rather than imported: --dry-run must run
# with no Kite session and no backend env, and importing dataserver opens the
# 11 GB store with a 4 GB mmap window just to answer "is this a stock?".
_MCX = {"GOLD", "GOLDM", "SILVER", "SILVERM", "CRUDEOIL",
        "NATURALGAS", "COPPER", "ZINC", "ALUMINIUM",
        "LEAD", "NICKEL", "COTTON", "MENTHAOIL"}
_FX = {"USDINR", "EURINR", "GBPINR", "JPYINR"}
_INDEX_TOKENS = ("NIFTY", "SENSEX", "BANKEX", "INDIA VIX")

OWNER = {
    "crypto": "backfill_crypto.py",
    "mcx": "backfill_macro.py",
    "fx": "backfill_macro.py",
    "index": "backfill_macro.py",
}

# ── sync_state (soft dependency) ──────────────────────────────────
# Another worker owns charto/data/sync_state.py. This file must run whether or
# not it has landed, so the import is optional and every call into it is
# guarded: a watermark is an optimisation, and `bars` is always the fallback
# source of truth. This script never creates or alters the sync_state table.
try:                                    # noqa: SIM105
    import sync_state                   # type: ignore
except Exception:                       # noqa: BLE001
    sync_state = None                   # type: ignore


# ── time helpers ──────────────────────────────────────────────────

def now_ist() -> datetime:
    return datetime.now(IST)


def _ist(ts: int) -> datetime:
    return datetime.fromtimestamp(int(ts), IST)


def _ist_date(ts: int) -> date:
    return _ist(ts).date()


def _minute_of_day(ts: int) -> int:
    d = _ist(ts)
    return d.hour * 60 + d.minute


def _session_open(day: date) -> datetime:
    return datetime.combine(day, dtime(0, 0), tzinfo=IST)


# ── session close ─────────────────────────────────────────────────
# dataserver owns these (NSE 15:29 / MCX 23:29 / crypto 23:59 / FX 16:59, all
# measured off complete stored sessions). Resolved through it rather than
# hardcoded because the values are not interchangeable and the trap is quiet:
# session_for() puts the INR pairs on the NSE anchor even though they trade to
# 17:00, so a hardcoded 15:29 would call every complete FX session complete an
# hour early — and rule 2 would then never repair a truncated one.
_ds_cache: list = []


def _dataserver():
    """The dataserver module, or None. Imported lazily and memoised."""
    if not _ds_cache:
        try:
            import dataserver                      # type: ignore
            _ds_cache.append(dataserver)
        except Exception:                          # noqa: BLE001
            _ds_cache.append(None)
    return _ds_cache[0]


def session_close_min(symbol: str) -> int:
    """Minute-of-day, in the symbol's own tz, of the last bar of a full session."""
    ds = _dataserver()
    fn = getattr(ds, "session_close_for", None) if ds else None
    return fn(symbol) if fn else NSE_CLOSE_MIN


# ── families ──────────────────────────────────────────────────────

def family(symbol: str) -> str:
    """Which backfill script owns a symbol. Unknown NSE-shaped names are equity."""
    s = symbol.upper()
    if s.endswith("USDT") or s.endswith("-USD"):
        return "crypto"
    if s in _MCX:
        return "mcx"
    if s in _FX:
        return "fx"
    if any(tok in s for tok in _INDEX_TOKENS):
        return "index"
    return "equity"


# ── db ────────────────────────────────────────────────────────────

def _connect(readonly: bool = False) -> sqlite3.Connection:
    if readonly:
        # --dry-run cannot write by construction, not merely by discipline.
        con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True,
                              check_same_thread=False, timeout=60)
    else:
        con = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=60)
        con.execute("PRAGMA journal_mode=WAL")   # dataserver keeps reading mid-write
        con.execute("PRAGMA synchronous=OFF")    # derived data — re-fetchable
    con.execute("PRAGMA busy_timeout=30000")
    return con


def _symbols_in_bars(con: sqlite3.Connection) -> list[str]:
    """Distinct symbols via index skip-scan.

    SELECT DISTINCT symbol walks all 89M rows; this walks one row per symbol
    through the (symbol, ts) primary key — 36ms instead of a minute.
    """
    q = ("WITH RECURSIVE d(s) AS ("
         " SELECT MIN(symbol) FROM bars"
         " UNION ALL"
         " SELECT (SELECT MIN(symbol) FROM bars WHERE symbol > d.s)"
         " FROM d WHERE d.s IS NOT NULL)"
         " SELECT s FROM d WHERE s IS NOT NULL")
    return [r[0] for r in con.execute(q)]


def _tail_sessions(con: sqlite3.Connection, symbol: str, max_ts: int,
                   days: int) -> list[tuple[date, int]]:
    """[(ist_date, last_bar_ts)] for the trailing `days` calendar days, ascending.

    One indexed range scan of a few thousand rows per symbol — cheap enough to
    do for all 500 names before deciding anything.
    """
    rows = con.execute(
        "SELECT ts FROM bars WHERE symbol=? AND ts>=? ORDER BY ts",
        (symbol, max_ts - days * 86400)).fetchall()
    last: dict[date, int] = {}
    for (ts,) in rows:
        last[_ist_date(ts)] = ts
    return sorted(last.items())


# ── sync_state readers (shape-tolerant) ───────────────────────────

def _field(row: object, name: str):
    if row is None:
        return None
    if isinstance(row, dict):
        return row.get(name)
    try:
        return row[name]                       # sqlite3.Row
    except Exception:                          # noqa: BLE001
        return getattr(row, name, None)


def _as_date(value) -> date | None:
    """Accept whatever shape the watermark stores a session in."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.astimezone(IST).date()
    if isinstance(value, date):
        return value
    if isinstance(value, (int, float)):
        return _ist_date(int(value))
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


# ── planning ──────────────────────────────────────────────────────

@dataclass
class Plan:
    symbol: str
    last_bar_ts: int | None = None
    last_complete: date | None = None
    partial: date | None = None
    windows: list[tuple[datetime, datetime]] = field(default_factory=list)
    origin: str = "bars"          # where last_complete came from
    note: str = ""
    skip: str = ""                # non-empty -> nothing to do


def _windows(frm: datetime, to: datetime) -> list[tuple[datetime, datetime]]:
    out: list[tuple[datetime, datetime]] = []
    cur = frm
    while cur < to:
        end = min(cur + timedelta(days=WINDOW_DAYS), to)
        out.append((cur, end))
        cur = end
    return out or [(frm, to)]


def plan_symbol(con: sqlite3.Connection, symbol: str, now: datetime) -> Plan:
    """Decide the fetch window for one symbol without touching Kite."""
    p = Plan(symbol=symbol)
    fam = family(symbol)
    if fam != "equity":
        p.skip = f"{fam}: owned by {OWNER[fam]}"
        return p

    row = con.execute("SELECT MAX(ts) FROM bars WHERE symbol=?", (symbol,)).fetchone()
    if not row or row[0] is None:
        # A top-up has no anchor without history. Sending this to Kite would
        # silently fetch 55 days and call a 10-year hole "done".
        p.skip = "no stored history — run backfill_1min.py first"
        return p
    p.last_bar_ts = int(row[0])

    # Watermark first when it exists; `bars` is always the fallback truth.
    if sync_state is not None:
        try:
            wm = sync_state.read(con, symbol)
        except Exception:                       # noqa: BLE001
            wm = None
        if wm is not None:
            lcs = _as_date(_field(wm, "last_complete_session"))
            if lcs is not None:
                p.last_complete = lcs
                p.partial = _as_date(_field(wm, "partial_session"))
                p.origin = "sync_state"

    if p.last_complete is None:
        close_min = session_close_min(symbol)
        sessions = _tail_sessions(con, symbol, p.last_bar_ts, TAIL_LOOKBACK_DAYS)
        complete = [d for d, ts in sessions if _minute_of_day(ts) >= close_min]
        if not complete:
            sessions = _tail_sessions(con, symbol, p.last_bar_ts, TAIL_LOOKBACK_WIDE)
            complete = [d for d, ts in sessions if _minute_of_day(ts) >= close_min]
        if complete:
            p.last_complete = complete[-1]
        else:
            # Nothing in 90 days closed cleanly — the trailing tail is not
            # trustworthy at all, so fall back to backfill_macro.py's blunt
            # `MAX(ts) - 2 days` rather than trusting a shape we cannot verify.
            p.last_complete = _ist_date(p.last_bar_ts) - timedelta(days=2)
            p.note = "no clean session in 90d; using MAX(ts)-2d"
        tail_day = _ist_date(p.last_bar_ts)
        if tail_day > p.last_complete:
            p.partial = tail_day

    frm = _session_open(p.last_complete)         # 00:00 IST of that session
    if frm >= now:
        p.skip = "already current"
        return p
    p.windows = _windows(frm, now)
    return p


# ── kite ──────────────────────────────────────────────────────────

def get_token() -> str:
    # Imported lazily: --dry-run must work with no Kite session and no backend
    # environment at all, which is exactly how this script gets verified.
    from backend.database import SessionLocal
    from backend.brokers.sessions import get_active_kite_session
    from backend.kite.auth import read_kite_access_token

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


def _resolve(symbol: str, token: str) -> int | None:
    from backend.kite.historical import _resolve_instrument_token
    return _resolve_instrument_token(symbol, "NSE", token)


def _fold_new_daily(con: sqlite3.Connection, symbol: str,
                    since: datetime) -> list[list]:
    """The stored minutes from `since` folded to daily bars, on the symbol's clock."""
    ds = _dataserver()
    if ds is None:
        raise RuntimeError("dataserver unavailable — cannot fold minutes to daily")
    rows = con.execute(
        "SELECT ts,o,h,l,c,v FROM bars WHERE symbol=? AND ts>=? ORDER BY ts",
        (symbol, int(since.timestamp()))).fetchall()
    return ds._fold_daily(rows, ds.session_for(symbol))


# ── corporate-action detection ────────────────────────────────────

@dataclass
class Verdict:
    kind: str                  # insufficient | noise | dividend | split | step
    n: int = 0
    mean: float = 1.0
    stdev: float = 0.0
    detail: str = ""

    @property
    def loud(self) -> bool:
        """Does this need a human and a full refetch?"""
        return self.kind in ("split", "step")


# Ratios a split/bonus actually lands on. Used only to NAME what happened once
# the stability test has already decided something happened.
_CLEAN = {2.0: "1:2 split", 3.0: "1:3 split", 4.0: "1:4 split",
          5.0: "1:5 split", 10.0: "1:10 split", 0.5: "2:1 reverse",
          0.25: "4:1 reverse", 0.2: "5:1 reverse", 0.1: "10:1 reverse",
          1.5: "1:2 bonus", 2.0 / 3: "2:3 bonus", 1.25: "1:4 bonus"}


def _name_factor(mean: float) -> str:
    for factor, label in _CLEAN.items():
        if abs(mean - factor) / factor < 0.01:
            return f" ~{label}"
    return ""


def _stable(ratios: list[float]) -> tuple[bool, float, float]:
    """(is a constant offset, mean, stdev).

    Two independent conditions, both required. The sign test is what actually
    separates the measured series — noise crosses 1.0 freely, a rescale never
    does — and the stdev/K test is the quantitative backstop for a short run
    that happens not to cross.
    """
    mean, sd = fmean(ratios), pstdev(ratios)
    one_sided = all(r >= 1.0 for r in ratios) or all(r <= 1.0 for r in ratios)
    return (one_sided and sd < CA_STDEV_MAX
            and abs(mean - 1.0) > CA_K * sd), mean, sd


def classify_ratios(ratios: list[float]) -> Verdict:
    """Classify minute_fold_close / kite_daily_close over consecutive sessions.

    Fitted to measurements taken 2026-08-02 over 8 sessions:

      RELIANCE  1.00008 1.00155 1.00054 1.00262 1.00136 0.99909 1.00054 0.99612
      TCS       1.00064 0.99836 0.99900 0.99914 0.99824 0.99996 0.99905 0.99941
      BPCL      1.00081 1.00000 1.00129 1.00048 0.99826 0.99921 0.99796 0.98583
        -> all three oscillate around 1.0 and flip sign freely: NOISE, the
           structural VWAP-close vs last-trade gap. Not a corporate action.

      ABBOTINDIA 1.02409 1.02233 1.02666 1.02313 1.02316 1.02280 1.02257 1.02337
        -> stable at ~1.0235 on every session, never crossing 1.0: a real
           constant rescale, small enough to be a dividend adjustment.

    The step case is the one the global-stability test alone would miss. A
    split INSIDE the fetched gap leaves ratios at the split factor before it
    and ~1.0 after, so the series is bimodal: high stdev, which the test above
    would call noise — silently missing the exact failure the check exists for.
    A single changepoint with a stable, materially different mean either side
    is therefore tested for separately.
    """
    n = len(ratios)
    if n < CA_MIN_SESSIONS:
        return Verdict("insufficient", n, detail=f"only {n} overlapping session(s)")

    ok, mean, sd = _stable(ratios)
    if ok:
        off = abs(mean - 1.0)
        if off > CA_SPLIT_MIN:
            return Verdict("split", n, mean, sd,
                           f"x{mean:.4f} on every session{_name_factor(mean)}")
        return Verdict("dividend", n, mean, sd, f"x{mean:.4f} on every session")

    # bimodal? one changepoint, each side stable, means materially apart
    for cut in range(CA_SEGMENT_MIN, n - CA_SEGMENT_MIN + 1):
        lo, hi = ratios[:cut], ratios[cut:]
        m_lo, m_hi = fmean(lo), fmean(hi)
        if (pstdev(lo) < CA_STDEV_MAX and pstdev(hi) < CA_STDEV_MAX
                and abs(m_lo - m_hi) / min(m_lo, m_hi) > CA_SPLIT_MIN):
            jump = m_lo / m_hi
            return Verdict("step", n, jump, max(pstdev(lo), pstdev(hi)),
                           f"x{m_lo:.4f} then x{m_hi:.4f} "
                           f"(jump {jump:.4f}{_name_factor(jump)})")
    return Verdict("noise", n, mean, sd, f"mean {mean:.5f} sd {sd:.5f}")


def _ca_check(folded: list[list], kite, symbol: str, instrument: int,
              since: datetime, now: datetime) -> Verdict:
    """RAW minute fold vs ADJUSTED Kite daily, over the overlapping sessions.

    Takes already-folded bars rather than a connection so the caller can do the
    DB read under its write lock and leave this HTTP round-trip outside it —
    otherwise one slow check stalls every other worker's inserts.
    """
    if not folded:
        return Verdict("insufficient", 0, detail="no folded sessions")
    daily = kite.historical_data(
        instrument_token=instrument, from_date=since, to_date=now,
        interval="day", continuous=False, oi=False,
    ) or []
    # kiteconnect parses "date" to a datetime for every interval, but a daily
    # candle is conceptually a date and one client version returning one would
    # crash a whole run's check — normalise instead of assuming.
    ref = {(c["date"].date() if isinstance(c["date"], datetime) else c["date"]):
           float(c["close"]) for c in daily if c.get("close")}
    ratios = [float(b[4]) / ref[_ist_date(b[0])]
              for b in folded
              if ref.get(_ist_date(b[0])) and b[4]]
    return classify_ratios(ratios)


# ── run ───────────────────────────────────────────────────────────

def run(plans: list[Plan], workers: int, reconcile: bool,
        pace: float = PACE) -> None:
    token = get_token()
    from backend.kite.auth import get_authenticated_kite

    con = _connect()
    if sync_state is not None:
        try:
            sync_state.ensure_table(con)     # its table, its DDL — never ours
        except Exception as exc:             # noqa: BLE001
            print(f"  ! sync_state.ensure_table failed ({exc}); watermarks off")

    lock = threading.Lock()
    queue = list(plans)
    stats = {"symbols": 0, "bars": 0}
    flagged: dict[str, list[str]] = {}
    errors: list[str] = []
    t0 = time.time()

    def worker() -> None:
        kite = get_authenticated_kite(token)
        while True:
            with lock:
                if not queue:
                    return
                p = queue.pop(0)
            ts0 = time.time()
            try:
                instrument = _resolve(p.symbol, token)
            except Exception as exc:                         # noqa: BLE001
                instrument = None
                with lock:
                    errors.append(f"{p.symbol}: resolve failed: {str(exc)[:70]}")
            if instrument is None:
                with lock:
                    if not errors or not errors[-1].startswith(p.symbol):
                        errors.append(f"{p.symbol}: could not resolve instrument token")
                    print(f"  ! {p.symbol:14s} unresolved — skipped", flush=True)
                continue

            added = 0
            for frm, to in p.windows:
                # RETRY, never drop. This loop used to `continue` on any
                # exception, which discards the window and leaves the symbol
                # permanently short of exactly the sessions the run existed to
                # fetch — while still printing DONE. It never showed at
                # WORKERS=3 because that pace does not 429; it is precisely the
                # failure that appears the moment concurrency is raised, so the
                # retry has to land BEFORE the worker count does.
                candles = None
                for attempt in range(MAX_ATTEMPTS):
                    try:
                        candles = kite.historical_data(
                            instrument_token=instrument, from_date=frm, to_date=to,
                            interval="minute", continuous=False, oi=False,
                        )
                        break
                    except Exception as exc:                 # noqa: BLE001
                        last = str(exc)[:70]
                        if attempt == MAX_ATTEMPTS - 1:
                            with lock:
                                errors.append(
                                    f"{p.symbol} {frm:%Y-%m-%d}: gave up after "
                                    f"{MAX_ATTEMPTS} attempts: {last}")
                        else:
                            time.sleep(BACKOFF_BASE * (2 ** attempt))
                if candles is None:
                    continue
                rows = [
                    (p.symbol, int(c["date"].timestamp()),
                     float(c["open"]), float(c["high"]), float(c["low"]),
                     float(c["close"]), int(c.get("volume", 0) or 0))
                    for c in (candles or [])
                ]
                if rows:
                    with lock:
                        # INSERT OR REPLACE: the overlapping day is rewritten in
                        # place, which is what repairs a truncated tail.
                        con.executemany(
                            "INSERT OR REPLACE INTO bars VALUES (?,?,?,?,?,?,?)", rows)
                        con.commit()
                    added += len(rows)
                time.sleep(pace)

            verdict = Verdict("skipped")
            if reconcile and added:
                # Reach back CA_LOOKBACK_DAYS so a steady-state top-up of one
                # session still has 5+ ratios; a stdev over 2 points is noise
                # about noise. Those older minutes are already stored, so this
                # widens the fold, not the minute fetch.
                since = min(p.windows[0][0], now_ist() - timedelta(days=CA_LOOKBACK_DAYS))
                try:
                    with lock:                    # DB read under the write lock
                        folded = _fold_new_daily(con, p.symbol, since)
                    verdict = _ca_check(folded, kite, p.symbol,       # HTTP outside it
                                        instrument, since, p.windows[-1][1])
                except Exception as exc:                     # noqa: BLE001
                    verdict = Verdict("error", detail=str(exc)[:70])
                    with lock:
                        errors.append(f"{p.symbol}: CA check failed: {str(exc)[:70]}")
                time.sleep(pace)

            with lock:
                row = con.execute("SELECT MAX(ts) FROM bars WHERE symbol=?",
                                  (p.symbol,)).fetchone()
                new_max = int(row[0]) if row and row[0] else p.last_bar_ts
                stats["symbols"] += 1
                stats["bars"] += added
                if verdict.kind in ("split", "step", "dividend"):
                    flagged[p.symbol] = verdict
                tag = {"split": "SPLIT/BONUS", "step": "SPLIT/BONUS IN GAP",
                       "dividend": "div-adj (expected)", "noise": "ok",
                       "insufficient": "ok (not assessed)",
                       "skipped": "ok", "error": "check failed"}[verdict.kind]
                print(f"DONE {p.symbol:14s} +{added:>6,} bars  -> "
                      f"{_ist(new_max):%Y-%m-%d %H:%M} IST  "
                      f"{time.time() - ts0:5.1f}s  win={len(p.windows)}  {tag}",
                      flush=True)
                if sync_state is not None and new_max:
                    try:
                        sync_state.mark(con, p.symbol, last_bar_ts=new_max,
                                        source="kite_rest")
                    except Exception as exc:                 # noqa: BLE001
                        errors.append(f"{p.symbol}: sync_state.mark: {str(exc)[:70]}")

    threads = [threading.Thread(target=worker) for _ in range(max(1, workers))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    loud = {s: v for s, v in flagged.items() if v.loud}
    notes = {s: v for s, v in flagged.items() if not v.loud}
    print(f"\nTOPPED {stats['symbols']} symbols, {stats['bars']:,} bars, "
          f"{time.time() - t0:.0f}s, {len(loud)} flagged, "
          f"{len(notes)} div-adj notes, {len(errors)} errors")
    if loud:
        print("\nSPLIT / BONUS SUSPECTED — the stored minutes carry a permanent")
        print("price step the chart cannot see. These need a full minute refetch:")
        print("  cd pivot && .venv/bin/python ../charto/data/backfill_1min.py SYM")
        for sym, v in loud.items():
            print(f"  {sym:14s} {v.detail}  (n={v.n}, sd={v.stdev:.5f})")
    if notes:
        # Not an error and not actionable: Kite's daily series is
        # dividend-adjusted and its minute series is raw, for everyone.
        # Intraday charts conventionally show raw prices, TradingView included.
        print(f"\nnote: dividend-adjusted daily vs raw minutes (expected, no action) — "
              + ", ".join(f"{s} {v.mean:.4f}" for s, v in notes.items()))
    for e in errors[:10]:
        print("  ERR", e)
    con.close()


# ── cli ───────────────────────────────────────────────────────────

def _print_skips(skipped: list[Plan]) -> None:
    by_reason: dict[str, list[str]] = {}
    for p in skipped:
        by_reason.setdefault(p.skip, []).append(p.symbol)
    for reason, syms in sorted(by_reason.items()):
        shown = ", ".join(syms[:8]) + (f", +{len(syms) - 8} more" if len(syms) > 8 else "")
        print(f"SKIP {len(syms):3d}  {reason}")
        print(f"          {shown}")


def self_test() -> int:
    """Exercise classify_ratios on measured + synthetic series. No Kite, no DB.

    The measured rows are the ones the thresholds were fitted to; they are kept
    executable rather than merely quoted, so a later tweak to CA_STDEV_MAX that
    would re-break the 60%-false-positive case fails here instead of in a run.
    """
    cases: list[tuple[str, list[float], str]] = [
        ("RELIANCE  (measured)", [1.00008, 1.00155, 1.00054, 1.00262,
                                  1.00136, 0.99909, 1.00054, 0.99612], "noise"),
        ("TCS       (measured)", [1.00064, 0.99836, 0.99900, 0.99914,
                                  0.99824, 0.99996, 0.99905, 0.99941], "noise"),
        ("BPCL      (measured)", [1.00081, 1.00000, 1.00129, 1.00048,
                                  0.99826, 0.99921, 0.99796, 0.98583], "noise"),
        ("ABBOTINDIA(measured)", [1.02409, 1.02233, 1.02666, 1.02313,
                                  1.02316, 1.02280, 1.02257, 1.02337], "dividend"),
        ("1:2 split, all sess ", [2.0004, 1.9996, 2.0001, 1.9998,
                                  2.0003, 1.9999, 2.0002, 2.0000], "split"),
        ("1:2 split mid-gap   ", [2.0004, 1.9996, 2.0001, 1.9998,
                                  1.0002, 0.9999, 1.0001, 1.0000], "step"),
        ("5:1 reverse mid-gap ", [0.2001, 0.1999, 0.2000, 0.2001,
                                  1.0001, 0.9999, 1.0000, 1.0002], "step"),
        ("too few sessions    ", [1.0235, 1.0233, 1.0236], "insufficient"),
    ]
    bad = 0
    for label, ratios, want in cases:
        v = classify_ratios(ratios)
        ok = v.kind == want
        bad += not ok
        print(f"  {'PASS' if ok else 'FAIL'}  {label}  -> {v.kind:12s} "
              f"(want {want:12s}) {v.detail}")
    print(f"\nself-test: {len(cases) - bad}/{len(cases)} passed")
    return 1 if bad else 0


def main(argv: list[str]) -> None:
    ap = argparse.ArgumentParser(
        description="Incremental 1-minute top-up for NSE equities in charto_bars.db")
    ap.add_argument("symbols", nargs="*", help="default: every NSE equity in `bars`")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the plan; touches neither Kite nor the DB")
    ap.add_argument("--workers", type=int, default=WORKERS)
    ap.add_argument("--pace", type=float, default=PACE,
                    help="per-thread sleep between requests (see PACE)")
    ap.add_argument("--no-reconcile", action="store_true",
                    help="skip the corporate-action check (1 fewer request/symbol)")
    ap.add_argument("--self-test", action="store_true",
                    help="check the corporate-action classifier; no Kite, no DB")
    args = ap.parse_args(argv)

    if args.self_test:
        raise SystemExit(self_test())

    now = now_ist()
    con = _connect(readonly=args.dry_run)
    try:
        con.execute("SELECT 1 FROM bars LIMIT 1")
    except sqlite3.OperationalError as exc:
        raise SystemExit(f"no `bars` table in {DB_PATH}: {exc}")

    wanted = [s.upper() for s in args.symbols]
    symbols = wanted or _symbols_in_bars(con)

    plans = [plan_symbol(con, s, now) for s in symbols]
    todo = [p for p in plans if not p.skip]
    skipped = [p for p in plans if p.skip]

    print(f"charto top-up  {now:%Y-%m-%d %H:%M} IST  "
          f"{len(symbols)} symbols scanned, {len(todo)} to fetch\n")
    for p in sorted(todo, key=lambda x: x.symbol):
        frm, to = p.windows[0][0], p.windows[-1][1]
        state = (f"PARTIAL {p.partial:%Y-%m-%d}" if p.partial
                 else f"complete {p.last_complete:%Y-%m-%d}")
        extra = f"  [{p.note}]" if p.note else ""
        print(f"  {p.symbol:14s} last {_ist(p.last_bar_ts):%Y-%m-%d %H:%M}  "
              f"{state:22s} fetch {frm:%Y-%m-%d} -> {to:%Y-%m-%d}  "
              f"req={len(p.windows)}  src={p.origin}{extra}")
    if skipped:
        print()
        _print_skips(skipped)

    if args.dry_run:
        reqs = sum(len(p.windows) for p in todo)
        extra = 0 if args.no_reconcile else len(todo)
        print(f"\nDRY RUN — {reqs} minute request(s)"
              f"{'' if args.no_reconcile else f' + {extra} daily reconcile request(s)'}"
              f", est {(reqs + extra) / 3.0:.0f}s at 3 req/s. Nothing fetched, "
              f"nothing written.")
        con.close()
        return

    con.close()
    if not todo:
        print("nothing to do")
        return
    run(todo, args.workers, reconcile=not args.no_reconcile, pace=args.pace)


if __name__ == "__main__":
    main(sys.argv[1:])
