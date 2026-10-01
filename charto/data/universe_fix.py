"""Correct and derive the Kite backfill stores before they are served.

Ran 2026-10-01 on the kite-backfill VM (/opt/kite-fix/fix.py, state in
/data/fix) as a systemd unit whose BACKFILL_* variables are set on ExecStart
via /usr/bin/env: /etc/kite-backfill.env also sets BACKFILL_HOME, and an
EnvironmentFile= value overrides an Environment= line.

Runs on the backfill VM as the `backfill` user, after the raw stores are
archived to blob (kite-archive/backfill-2026-10-01). Every stage is idempotent
and recorded in /data/fix/state, so a restart resumes at the stage it stopped.

  S1 repair    anomalies set aside by the backfill that are recoverable:
               an opening minute whose auction open lies outside its own
               high/low (widen h/l), a second-stamped minute (floor it, only
               into an EMPTY slot), a corrupt volume (keep prices, v=0).
               All-zero placeholder minutes stay out. Logged in `repairs`.
  S2 fetch     Kite daily (adjusted as of today) for every equity: the
               reference the minutes are checked against, and the daily
               history before 2015. Open interest for every futures
               contract (minute + daily) and every continuous series (daily).
  S3 sessions  each instrument group's trading day, MEASURED off its own
               minutes: the longest daily gap ends at the session open, and a
               session whose close falls before its open crosses midnight IST.
  S4 derive    per symbol: fold minutes to days on that session, compare with
               Kite's daily, find spans where BOTH high and low differ by the
               same ratio (a corporate action the minute archive was not
               adjusted for), rescale those minutes, then write the served
               `bars_1d` (minute fold + Kite days before the minutes) and the
               `bars_15m` rollup on the same bucket arithmetic as dataserver.
  S5 rolls     continuous futures 1!/2! per underlying: expiry rolls for
               NFO/BFO/CDS, volume rolls (capped at expiry) for MCX/NCO,
               monthly contracts only.

Model reads, code computes: nothing here is a guess. Where the data cannot
decide (one side of the range differs, a day Kite has and we do not), the row
is reported, not changed.
"""
from __future__ import annotations

import json, math, multiprocessing as mp, os, re, sqlite3, sys, threading, time, traceback
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

os.environ.setdefault("BACKFILL_HOME", "/data/fix")
os.environ.setdefault("BACKFILL_RATE", "6.0")
sys.path.insert(0, "/opt/kite-backfill2")
import backfill2 as bf  # noqa: E402  (Auth, hist, BUCKET, clean, IST, log)

FIX = Path("/data/fix")
STATE = FIX / "state"
P1 = Path("/data/backfill")
P2 = Path("/data/backfill2")
P3 = Path("/data3/backfill3")      # phase 3: charto's original 500, renamed nse500_1m.db
STORES = {p.stem: p for p in sorted(P1.glob("*.db")) + sorted(P2.glob("*.db"))
          + sorted(P3.glob("nse500_1m.db"))}
EQUITY = {"nse_1m", "bse_1m", "nse500_1m"}
IST_OFF = 19800
END = datetime.fromtimestamp(int((P1 / "state" / "end_ts").read_text()), bf.IST) \
    if (P1 / "state" / "end_ts").exists() else datetime(2026, 9, 30, 23, 0, tzinfo=bf.IST)
log = bf.log
MONTHLY = re.compile(r"[A-Z]{3}FUT$")


def con(path: Path) -> sqlite3.Connection:
    c = sqlite3.connect(path, timeout=120, isolation_level=None, check_same_thread=False)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA synchronous=NORMAL")
    c.execute("PRAGMA busy_timeout=120000")
    return c


def done(stage: str) -> bool:
    return (STATE / f"{stage}.done").exists()


def mark(stage: str, info: dict | None = None) -> None:
    (STATE / f"{stage}.done").write_text(json.dumps(info or {}, default=str))


PROG: dict = {"stage": None}


def progress(**kw) -> None:
    PROG.update(kw, updated=datetime.now(bf.IST).strftime("%H:%M:%S"))
    tmp = FIX / "progress.json.tmp"
    tmp.write_text(json.dumps(PROG, default=str, indent=1))
    tmp.replace(FIX / "progress.json")


# ── S1 repair ─────────────────────────────────────────────────────────────
def s1_repair() -> dict:
    out = {}
    for name, path in STORES.items():
        c = con(path)
        c.execute("CREATE TABLE IF NOT EXISTS repairs (symbol TEXT, ts INTEGER, kind TEXT, "
                  "action TEXT, PRIMARY KEY (symbol, ts, kind)) WITHOUT ROWID")
        n = defaultdict(int)
        c.execute("BEGIN")
        for sym, ts, kind, raw in c.execute(
                "SELECT symbol, ts, kind, raw FROM anomalies WHERE kind IN "
                "('ohlc_inconsistent','unaligned','volume_range')").fetchall():
            r = json.loads(raw)
            o, h, l, cl = r["o"], r["h"], r["l"], r["c"]
            try:
                v = int(r["v"])
            except (TypeError, ValueError):
                v = 0
            if min(o, h, l, cl) <= 0:
                continue
            if kind == "ohlc_inconsistent":
                row, act = (sym, ts, o, max(h, o, cl), min(l, o, cl), cl, v), "widened"
            elif kind == "unaligned":
                row, act = (sym, ts - ts % 60, o, max(h, o, cl), min(l, o, cl), cl,
                            v if 0 <= v < 2 ** 62 else 0), "floored"
            else:
                row, act = (sym, ts, o, max(h, o, cl), min(l, o, cl), cl, 0), "volume_zeroed"
            cur = c.execute("INSERT OR IGNORE INTO bars VALUES (?,?,?,?,?,?,?)", row)
            if not cur.rowcount:
                act = "slot_taken"
            c.execute("INSERT OR REPLACE INTO repairs VALUES (?,?,?,?)", (sym, ts, kind, act))
            n[f"{kind}:{act}"] += 1
        c.execute("COMMIT")
        c.close()
        out[name] = dict(n)
        log.info("S1 %s %s", name, dict(n))
    return out


# ── S2 fetch ──────────────────────────────────────────────────────────────
def hist_oi(tok, frm, to, interval, cont=0):
    """bf.hist with oi=1: candles carry a seventh field, open interest."""
    errors = 0
    while True:
        if getattr(bf.TL, "gen", None) != bf.AUTH.gen or bf.AUTH.token is None:
            if bf.AUTH.token is None:
                bf._login(bf.AUTH.gen)
            bf.TL.k, bf.TL.gen = bf.AUTH.client()
        bf.BUCKET.take()
        bf.bump("req")
        try:
            raw = bf.TL.k._get("market.historical",
                               url_args={"instrument_token": tok, "interval": interval},
                               params={"from": frm.astimezone(bf.IST).strftime("%Y-%m-%d %H:%M:%S"),
                                       "to": to.astimezone(bf.IST).strftime("%Y-%m-%d %H:%M:%S"),
                                       "continuous": cont, "oi": 1})
            return [(int(datetime.fromisoformat(c[0]).timestamp()), c[6] if len(c) > 6 else None)
                    for c in raw.get("candles") or []]
        except bf.kex.TokenException:
            bf._login(bf.TL.gen)
        except Exception as e:  # noqa: BLE001
            m = str(e).lower()
            if "too many" in m or "429" in m:
                bf.bump("429"); bf.BUCKET.on_429(); time.sleep(2); continue
            if isinstance(e, bf.kex.InputException):
                raise
            errors += 1
            bf.bump("err")
            if errors >= 30:
                raise
            time.sleep(min(60, 3 * errors))


def _daily_ref(c, lock, sym, tok):
    end, rows_all, nreq = END, [], 0
    while end > bf.FLOOR_DAY:
        start = max(end - timedelta(days=1999), bf.FLOOR_DAY)
        candles = bf.hist(tok, start, end, "day")
        nreq += 1
        rows, _bad = bf.clean(sym, candles, int(start.timestamp()) - 86400, int(end.timestamp()) + 86400)
        rows_all += rows
        if not candles:
            break
        end = start
    with lock:
        c.execute("BEGIN")
        c.executemany("INSERT OR REPLACE INTO kite_1d VALUES (?,?,?,?,?,?,?)", rows_all)
        c.execute("INSERT OR REPLACE INTO fetch_log VALUES (?,?,?,?)", (sym, "kite_1d", len(rows_all), nreq))
        c.execute("COMMIT")


def _oi(c, lock, sym, tok, kind, first_ts):
    rows_m, rows_d = [], []
    if kind == "contract":
        if not first_ts:
            return
        start = datetime.fromtimestamp(first_ts, bf.IST).replace(hour=0, minute=0)
        s = start
        while s < END:
            e = min(s + timedelta(days=59), END)
            rows_m += [(sym, t, oi) for t, oi in hist_oi(tok, s, e, "minute") if oi is not None]
            s = e
        rows_d = [(sym, t, oi) for t, oi in hist_oi(tok, start - timedelta(days=5), END, "day") if oi is not None]
    else:  # continuous: daily only, newest-first chunks
        end = END
        while end > bf.FLOOR_DAY:
            st = max(end - timedelta(days=1999), bf.FLOOR_DAY)
            got = hist_oi(tok, st, end, "day", 1)
            rows_d += [(sym, t, oi) for t, oi in got if oi is not None]
            if not got:
                break
            end = st
    with lock:
        c.execute("BEGIN")
        c.executemany("INSERT OR REPLACE INTO oi_1m VALUES (?,?,?)", rows_m)
        c.executemany("INSERT OR REPLACE INTO oi_1d VALUES (?,?,?)", rows_d)
        c.execute("INSERT OR REPLACE INTO fetch_log VALUES (?,?,?,?)", (sym, "oi", len(rows_m) + len(rows_d), 0))
        c.execute("COMMIT")


def s2_fetch() -> dict:
    jobs, conns = [], {}
    for name, path in STORES.items():
        c = con(path)
        lock = threading.Lock()
        conns[name] = (c, lock)
        c.execute("CREATE TABLE IF NOT EXISTS fetch_log (symbol TEXT, what TEXT, rows INTEGER, "
                  "requests INTEGER, PRIMARY KEY (symbol, what)) WITHOUT ROWID")
        have = {(s, w) for s, w in c.execute("SELECT symbol, what FROM fetch_log")}
        if name in EQUITY:
            c.execute("CREATE TABLE IF NOT EXISTS kite_1d (symbol TEXT NOT NULL, ts INTEGER NOT NULL, "
                      "o REAL, h REAL, l REAL, c REAL, v INTEGER, PRIMARY KEY (symbol, ts)) WITHOUT ROWID")
            for sym, tok in c.execute("SELECT symbol, instrument_token FROM instruments "
                                      "WHERE status='done'").fetchall():
                if (sym, "kite_1d") not in have:
                    jobs.append(("d", name, sym, tok, None, None))
        if name.startswith("fut_"):
            c.execute("CREATE TABLE IF NOT EXISTS oi_1m (symbol TEXT NOT NULL, ts INTEGER NOT NULL, "
                      "oi INTEGER, PRIMARY KEY (symbol, ts)) WITHOUT ROWID")
            c.execute("CREATE TABLE IF NOT EXISTS oi_1d (symbol TEXT NOT NULL, ts INTEGER NOT NULL, "
                      "oi INTEGER, PRIMARY KEY (symbol, ts)) WITHOUT ROWID")
            for sym, tok, kind, first in c.execute(
                    "SELECT symbol, instrument_token, kind, first_ts FROM instruments").fetchall():
                if (sym, "oi") not in have:
                    jobs.append(("o", name, sym, tok, kind, first))
    total, q, qlock = len(jobs), list(jobs), threading.Lock()
    errs: list = []

    def worker():
        while True:
            with qlock:
                if not q:
                    return
                j = q.pop()
            kind_, name, sym, tok, kind, first = j
            c, lock = conns[name]
            try:
                if kind_ == "d":
                    _daily_ref(c, lock, sym, tok)
                else:
                    _oi(c, lock, sym, tok, kind, first)
            except SystemExit:
                raise
            except Exception as e:  # noqa: BLE001
                errs.append((name, sym, str(e)[:200]))
                log.error("S2 %s %s: %s", name, sym, str(e)[:300])

    threads = [threading.Thread(target=worker, daemon=True) for _ in range(8)]
    for t in threads:
        t.start()
    while any(t.is_alive() for t in threads):
        progress(stage="S2 fetch", s2_left=len(q), s2_total=total, s2_errors=len(errs),
                 kite=dict(bf.STATS))
        time.sleep(15)
    return {"jobs": total, "errors": errs[:50], "n_errors": len(errs), "kite": dict(bf.STATS)}


# ── S3 sessions ───────────────────────────────────────────────────────────
GROUP_OF = {"nse_1m": "NSE_EQ", "nse500_1m": "NSE_EQ", "bse_1m": "BSE_EQ", "idx_nse": "NSE_IDX", "idx_bse": "BSE_IDX",
            "idx_mcx": "MCX_IDX", "fut_nfo": "NFO", "fut_bfo": "BFO", "fut_mcx": "MCX",
            "fut_nco": "NCO", "fut_cds": "CDS"}
PER_SYMBOL = {"idx_global", "idx_nseix"}


def _measure(c, symbols, since) -> dict | None:
    hist = [0] * 1440
    for s in symbols:
        for (m,) in c.execute("SELECT ((ts+19800)%86400)/60 FROM bars WHERE symbol=? AND ts>=?", (s, since)):
            hist[m] += 1
    peak = max(hist)
    if not peak:
        return None
    active = [h >= 0.05 * peak for h in hist]
    if all(active):
        return {"open": 0, "close": 1439, "wrap": False, "gap": 0}
    best, best_end, run = 0, 0, 0
    for i in range(2 * 1440):                     # circular longest inactive run
        if not active[i % 1440]:
            run += 1
            if run > best:
                best, best_end = run, i % 1440
        else:
            run = 0
    open_ = (best_end + 1) % 1440
    close = (best_end - best) % 1440
    return {"open": open_, "close": close, "wrap": close < open_, "gap": best}


def s3_sessions() -> dict:
    since = int(END.timestamp()) - 365 * 86400
    out: dict = {"groups": {}, "symbols": {}}
    for name, path in STORES.items():
        c = con(path)
        if name in PER_SYMBOL:
            for (s,) in c.execute("SELECT symbol FROM instruments WHERE bars>0").fetchall():
                out["symbols"][s] = _measure(c, [s], since)
        elif name in GROUP_OF:
            sample = [s for (s,) in c.execute(
                "SELECT symbol FROM instruments WHERE bars>0 ORDER BY bars DESC LIMIT 150")]
            out["groups"][GROUP_OF[name]] = _measure(c, sample, since)
            # per-symbol close for futures (agri MCX closes earlier than energy)
            if name.startswith("fut_"):
                for (s,) in c.execute("SELECT symbol FROM instruments WHERE bars>0").fetchall():
                    m = _measure(c, [s], since)
                    if m:
                        out["symbols"][f"{name}|{s}"] = m
        c.close()
        log.info("S3 %s done", name)
    (FIX / "sessions.json").write_text(json.dumps(out, indent=1))
    return {"groups": out["groups"],
            "global": {k: v for k, v in out["symbols"].items() if "|" not in k}}


def session_of(sessions: dict, store: str, sym: str) -> tuple[int, bool]:
    if store in PER_SYMBOL and sessions["symbols"].get(sym):
        m = sessions["symbols"][sym]
    else:
        m = sessions["groups"].get(GROUP_OF.get(store, ""))
    m = m or {"open": 555, "wrap": False}      # unmeasured: the NSE clock, as dataserver
    return m["open"], bool(m["wrap"])


# ── S4 derive ─────────────────────────────────────────────────────────────
def trade_day(ts: int, open_: int, wrap: bool) -> int:
    return (ts + IST_OFF - (open_ * 60 if wrap else 0)) // 86400


def fold_day(rows, open_, wrap):
    out, cur = {}, None
    for ts, o, h, l, c, v in rows:
        if not (o and h and l and c):
            continue
        d = trade_day(ts, open_, wrap)
        b = out.get(d)
        if b is None:
            out[d] = [o, h, l, c, v]
        else:
            b[1] = max(b[1], h); b[2] = min(b[2], l); b[3] = c; b[4] += v
    return out


def fold_15m(rows, open_, wrap):
    out, key = [], None
    for ts, o, h, l, c, v in rows:
        if not (o and h and l and c):
            continue
        if wrap:
            sh = ts + IST_OFF - open_ * 60
            day, mod = sh // 86400, (sh % 86400) // 60
            b = mod // 15
        else:
            local = ts + IST_OFF
            day, mod = local // 86400, (local % 86400) // 60
            b = max(0, mod - open_) // 15
        k = (day, b)
        if k != key:
            out.append([day * 86400 + (open_ + b * 15) * 60 - IST_OFF, o, h, l, c, v])
            key = k
        else:
            x = out[-1]
            x[2] = max(x[2], h); x[3] = min(x[3], l); x[4] = c; x[5] += v
    return out


def ca_spans(fold: dict, ref: dict):
    """Days where Kite's adjusted high AND low both differ from the minute
    fold by one common ratio. Consecutive such days with a stable ratio form
    a span; a matching day ends it."""
    spans, cur, checked, mismatch = [], None, 0, 0
    matched: list[int] = []
    for d in sorted(fold):
        r = ref.get(d)
        if not r:
            continue
        fh, fl = fold[d][1], fold[d][2]
        kh, kl = r[1], r[2]
        if not (fh and fl and kh and kl):
            continue
        checked += 1
        rh, rl = kh / fh, kl / fl
        if abs(rh - 1) <= 0.003 and abs(rl - 1) <= 0.003:
            matched.append(d)
            if cur:
                spans.append(cur); cur = None
            continue
        if abs(rh - 1) > 0.005 and abs(rl - 1) > 0.005 and abs(rh / rl - 1) < 0.004:
            f = math.sqrt(rh * rl)
            if cur and abs(f / cur["f_med"] - 1) < 0.004:
                cur["days"].append(d); cur["fs"].append(f)
                cur["vr"].append((r[4] / fold[d][4]) if fold[d][4] and r[4] else None)
                cur["f_med"] = sorted(cur["fs"])[len(cur["fs"]) // 2]
            else:
                if cur:
                    spans.append(cur)
                cur = {"days": [d], "fs": [f], "f_med": f,
                       "vr": [(r[4] / fold[d][4]) if fold[d][4] and r[4] else None]}
            continue
        mismatch += 1
        if cur:
            spans.append(cur); cur = None
    if cur:
        spans.append(cur)
    return finalize_spans(spans, matched), checked, mismatch


def _split_like(f: float) -> bool:
    """A split, bonus or consolidation ratio: a/b with small integers."""
    return any(abs(f / (a / b) - 1) < 0.003 for a in range(1, 11) for b in range(1, 21) if a < b)


def finalize_spans(spans, matched):
    """Only what a corporate action can be.
    - A factor >= 0.995 is not one: an action scales history DOWN. Reported.
    - Same-factor spans with no day between them that matched at factor 1
      are one adjustment: the days between disagreed on one side only and
      must not be left unscaled inside the corrected period.
    - A factor that is not a split ratio needs >= 3 days to be believed (a
      lone 0.98 day is a data disagreement, not a dividend adjustment)."""
    import bisect
    spans = [s for s in spans if s["f_med"] < 0.995]
    merged = []
    for s in spans:
        if merged and abs(s["f_med"] / merged[-1]["f_med"] - 1) < 0.004:
            lo, hi = merged[-1]["days"][-1], s["days"][0]
            i = bisect.bisect_right(matched, lo)
            if i >= len(matched) or matched[i] >= hi:      # nothing matched in between
                m = merged[-1]
                m["fill_to"] = s["days"][-1]
                m["days"] += s["days"]; m["fs"] += s["fs"]; m["vr"] += s["vr"]
                m["f_med"] = sorted(m["fs"])[len(m["fs"]) // 2]
                continue
        merged.append(dict(s))
    return [s for s in merged if _split_like(s["f_med"]) or len(s["days"]) >= 3]


def derive_symbol(args):
    store, sym, sessions = args
    path = STORES[store]
    c = con(path)
    try:
        open_, wrap = session_of(sessions, store, sym)
        rows = c.execute("SELECT ts,o,h,l,c,v FROM bars WHERE symbol=? ORDER BY ts", (sym,)).fetchall()
        fold = fold_day(rows, open_, wrap)
        ref_tbl = "kite_1d"
        ref = {(ts + IST_OFF) // 86400: (o, h, l, cl, v) for ts, o, h, l, cl, v in c.execute(
            f"SELECT ts,o,h,l,c,v FROM {ref_tbl} WHERE symbol=? ORDER BY ts", (sym,))}
        report = {"minutes": len(rows), "days": len(fold), "spans": [], "checked": 0,
                  "mismatch_before": 0, "mismatch_after": 0}
        updates = []
        if store in EQUITY and not wrap and ref:
            spans, checked, mm = ca_spans(fold, ref)
            report["checked"], report["mismatch_before"] = checked, mm + sum(len(s["days"]) for s in spans)
            if spans:
                byday = {}
                for s in spans:
                    vrs = sorted(x for x in s["vr"] if x)
                    vr = vrs[len(vrs) // 2] if vrs else None
                    f = s["f_med"]
                    vol = bool(vr) and abs(vr * f - 1) < 0.15
                    for d in range(s["days"][0], s["days"][-1] + 1):
                        byday[d] = (f, vol)
                    report["spans"].append({"from": s["days"][0], "to": s["days"][-1], "n": len(s["days"]),
                                            "factor": round(f, 6), "volume": vol})
                new_rows = []
                for r in rows:
                    fv = byday.get(trade_day(r[0], open_, wrap))
                    if fv:
                        f, vol = fv
                        nr = (r[0], round(r[1] * f, 4), round(r[2] * f, 4), round(r[3] * f, 4),
                              round(r[4] * f, 4), int(round(r[5] / f)) if vol else r[5])
                        updates.append((nr[1], nr[2], nr[3], nr[4], nr[5], sym, r[0]))
                        new_rows.append(nr)
                    else:
                        new_rows.append(r)
                rows = new_rows
                fold = fold_day(rows, open_, wrap)
                _, _, mm2 = ca_spans(fold, ref)
                sp2, _, _ = ca_spans(fold, ref)
                report["mismatch_after"] = mm2 + sum(len(s["days"]) for s in sp2)
            else:
                report["mismatch_after"] = report["mismatch_before"]
        # served daily: minute fold, plus Kite's days before the first folded day
        # A session that crosses midnight is served on a clock shifted so its
        # open IS midnight — dataserver's session (0, IST_OFF - open*60) — so
        # its daily bar is stamped at the open, on the date the session opened.
        tz = IST_OFF - open_ * 60 if wrap else IST_OFF
        first_fold = min(fold) if fold else None
        d1 = [((d * 86400) - tz, *fold[d]) for d in sorted(fold)]
        pre = [((ts + IST_OFF) // 86400 * 86400 - tz, o, h, l, cl, v)
               for ts, o, h, l, cl, v in c.execute(
            "SELECT ts,o,h,l,c,v FROM kite_1d WHERE symbol=? ORDER BY ts", (sym,))
            if first_fold is None or (ts + IST_OFF) // 86400 < first_fold]
        pre = list({r[0]: r for r in pre}.values())     # one bar per day
        daily = pre + d1
        m15 = fold_15m(rows, open_, wrap)
        c.execute("BEGIN IMMEDIATE")
        if updates:
            c.executemany("UPDATE bars SET o=?,h=?,l=?,c=?,v=? WHERE symbol=? AND ts=?", updates)
            for s in report["spans"]:
                c.execute("INSERT OR REPLACE INTO adjustments VALUES (?,?,?,?,?,?,?)",
                          (sym, (s["from"] * 86400) - IST_OFF, (s["to"] * 86400) - IST_OFF, s["n"],
                           s["factor"], int(s["volume"]), "kite_daily_mismatch"))
        c.execute("DELETE FROM bars_1d WHERE symbol=?", (sym,))
        c.executemany("INSERT INTO bars_1d VALUES (?,?,?,?,?,?,?)", [(sym, *r) for r in daily])
        c.execute("DELETE FROM bars_15m WHERE symbol=?", (sym,))
        c.executemany("INSERT INTO bars_15m VALUES (?,?,?,?,?,?,?)", [(sym, *r) for r in m15])
        c.execute("INSERT OR REPLACE INTO fix_report VALUES (?,?,?,?,?,?,?,?)",
                  (sym, report["minutes"], report["days"], len(daily), len(m15), report["checked"],
                   report["mismatch_before"], json.dumps({"after": report["mismatch_after"],
                                                          "spans": report["spans"],
                                                          "open": open_, "wrap": wrap})))
        c.execute("COMMIT")
        return store, sym, len(report["spans"]), None
    except Exception as e:  # noqa: BLE001
        try:
            c.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        return store, sym, 0, f"{e}\n{traceback.format_exc()[-500:]}"
    finally:
        c.close()


def s4_prepare():
    for name, path in STORES.items():
        c = con(path)
        c.execute("CREATE TABLE IF NOT EXISTS kite_1d (symbol TEXT NOT NULL, ts INTEGER NOT NULL, "
                  "o REAL, h REAL, l REAL, c REAL, v INTEGER, PRIMARY KEY (symbol, ts)) WITHOUT ROWID")
        if name not in EQUITY:
            # phase 2 fetched Kite's daily into bars_1d; keep it as the reference
            # before bars_1d becomes the served table
            if not c.execute("SELECT 1 FROM kite_1d LIMIT 1").fetchone():
                c.execute("INSERT OR IGNORE INTO kite_1d SELECT * FROM bars_1d")
        else:
            c.execute("CREATE TABLE IF NOT EXISTS bars_1d (symbol TEXT NOT NULL, ts INTEGER NOT NULL, "
                      "o REAL, h REAL, l REAL, c REAL, v INTEGER, PRIMARY KEY (symbol, ts)) WITHOUT ROWID")
        c.execute("CREATE TABLE IF NOT EXISTS bars_15m (symbol TEXT NOT NULL, ts INTEGER NOT NULL, "
                  "o REAL, h REAL, l REAL, c REAL, v INTEGER, PRIMARY KEY (symbol, ts)) WITHOUT ROWID")
        c.execute("CREATE TABLE IF NOT EXISTS adjustments (symbol TEXT, from_ts INTEGER, to_ts INTEGER, "
                  "days INTEGER, factor REAL, volume INTEGER, source TEXT, PRIMARY KEY (symbol, from_ts))")
        c.execute("CREATE TABLE IF NOT EXISTS fix_report (symbol TEXT PRIMARY KEY, minutes INTEGER, "
                  "days INTEGER, daily INTEGER, m15 INTEGER, checked INTEGER, mismatch_before INTEGER, "
                  "detail TEXT)")
        c.close()


def s4_derive(sessions) -> dict:
    s4_prepare()
    work = []
    for name, path in STORES.items():
        c = con(path)
        doneset = {s for (s,) in c.execute("SELECT symbol FROM fix_report")}
        cols = {r[1] for r in c.execute("PRAGMA table_info(instruments)")}
        kind_sql = "COALESCE(kind,'')" if "kind" in cols else "''"
        for sym, nb in c.execute(f"SELECT symbol, COALESCE(bars,0) FROM instruments "
                                 f"WHERE {kind_sql} != 'cont'"):
            if sym not in doneset:
                work.append((nb, name, sym))
        if "kind" in cols:
            # continuous (daily only): served daily IS Kite's continuous daily
            c.execute("INSERT OR IGNORE INTO bars_1d SELECT k.* FROM kite_1d k JOIN instruments i "
                      "ON i.symbol=k.symbol WHERE i.kind='cont'")
        c.close()
    work.sort(reverse=True)                       # biggest first: no straggler at the end
    total, n_done, n_spans, errs = len(work), 0, 0, []
    with mp.get_context("fork").Pool(2, maxtasksperchild=200) as pool:
        for store, sym, spans, err in pool.imap_unordered(
                derive_symbol, [(st, s, sessions) for _, st, s in work], chunksize=4):
            n_done += 1
            n_spans += spans
            if err:
                errs.append((store, sym, err[:300]))
                log.error("S4 %s %s: %s", store, sym, err)
            if n_done % 25 == 0 or n_done == total:
                progress(stage="S4 derive", s4_done=n_done, s4_total=total, s4_spans=n_spans,
                         s4_errors=len(errs))
    return {"symbols": total, "spans": n_spans, "errors": errs[:50], "n_errors": len(errs)}


# ── S5 rolls ──────────────────────────────────────────────────────────────
def s5_rolls(sessions) -> dict:
    """Which contract WAS the front month on each day, read off Kite's own
    continuous daily series rather than inferred: Kite does not list expired
    contracts, so an expiry rule over the contracts we hold calls a far month
    "front" for most of its life (USDINR26OCTFUT from Oct 2025). A day whose
    continuous high/low matches a held contract's daily high/low within 0.1%
    names that contract; a day matching none was an expired contract's and
    gets no segment, so the stitched series starts where the truth does.
    Rank 2 is the next held monthly contract by expiry on those days.

    BFO has no continuous series on Kite: there the front is the nearest held
    monthly contract, only within 35 days of its expiry (one monthly cycle).
    Live days after the backfill roll at expiry (the feed's rule)."""
    out = {}
    for name in ("fut_nfo", "fut_bfo", "fut_cds", "fut_mcx", "fut_nco"):
        c = con(STORES[name])
        c.execute("CREATE TABLE IF NOT EXISTS rolls (root TEXT, rank INTEGER, contract TEXT, "
                  "from_ts INTEGER, to_ts INTEGER, method TEXT, PRIMARY KEY (root, rank, from_ts))")
        c.execute("DELETE FROM rolls")
        by_root = defaultdict(list)
        for sym, nm, exp in c.execute("SELECT symbol, name, expiry FROM instruments "
                                      "WHERE kind='contract' AND bars>0"):
            if MONTHLY.search(sym) and exp:
                by_root[nm].append((exp, sym))
        n, unmatched = 0, 0
        for root, cs in by_root.items():
            cs.sort()
            order = [s for _, s in cs]
            exp_day = {s: (int(datetime.strptime(e, "%Y-%m-%d").replace(tzinfo=bf.IST).timestamp())
                           + IST_OFF) // 86400 for e, s in cs}
            daily = {s: {(ts + IST_OFF) // 86400: (h, l) for ts, h, l in
                         c.execute("SELECT ts, h, l FROM bars_1d WHERE symbol=?", (s,))} for s in order}
            cont = {(ts + IST_OFF) // 86400: (h, l) for ts, h, l in
                    c.execute("SELECT ts, h, l FROM kite_1d WHERE symbol=?", (f"CONT:{root}",))}
            days = sorted({d for s in order for d in daily[s]})
            front, how = {}, {}
            cont_last = max(cont) if cont else None
            for d in days:
                if cont and d <= cont_last:
                    how[d] = "kite_continuous"
                    ch = cont.get(d)
                    if not ch:
                        continue
                    best = None
                    for s in order:
                        hl = daily[s].get(d)
                        if hl and abs(hl[0] / ch[0] - 1) < 0.001 and abs(hl[1] / ch[1] - 1) < 0.001:
                            best = s
                            break
                    if best:
                        front[d] = best
                    else:
                        unmatched += 1
                else:
                    # no continuous series on Kite for this day (BFO never;
                    # CDS stops in June 2023): nearest held monthly within one cycle
                    how[d] = "expiry_35d"
                    live = [s for s in order if exp_day[s] >= d and d in daily[s]]
                    if live and exp_day[live[0]] - d <= 35:
                        front[d] = live[0]
            segs = {1: [], 2: []}
            prev = None
            for d in sorted(front):
                f1 = front[d]
                later = [s for s in order if exp_day[s] > exp_day[f1] and d in daily[s]]
                picks = {1: f1, 2: later[0] if later else None}
                for rank, s_ in picks.items():
                    if s_ is None:
                        continue
                    if segs[rank] and segs[rank][-1][0] == s_ and segs[rank][-1][2] == prev:
                        segs[rank][-1][2] = d
                    else:
                        segs[rank].append([s_, d, d])
                prev = d
            for rank, ss in segs.items():
                for s_, d0, d1 in ss:
                    c.execute("INSERT OR REPLACE INTO rolls VALUES (?,?,?,?,?,?)",
                              (root, rank, s_, d0 * 86400 - IST_OFF, (d1 + 1) * 86400 - IST_OFF,
                               how[d0]))
                    n += 1
        out[name] = {"roots": len(by_root), "segments": n, "days_no_held_front": unmatched}
        c.close()
    return out


def main():
    STATE.mkdir(parents=True, exist_ok=True)
    while not (FIX / "ARCHIVED").exists():
        progress(stage="waiting for archive")
        time.sleep(30)
    report = json.loads((FIX / "report.json").read_text()) if (FIX / "report.json").exists() else {}

    def run(stage, fn, *a):
        if done(stage):
            return json.loads((STATE / f"{stage}.done").read_text())
        progress(stage=stage)
        t = time.time()
        info = fn(*a)
        info = {"seconds": round(time.time() - t), **(info or {})}
        mark(stage, info)
        report[stage] = info
        (FIX / "report.json").write_text(json.dumps(report, indent=1, default=str))
        return info

    run("S1", s1_repair)
    run("S2", s2_fetch)
    run("S3", s3_sessions)
    sessions = json.loads((FIX / "sessions.json").read_text())
    run("S4", s4_derive, sessions)
    run("S5", s5_rolls, sessions)
    (FIX / "COMPLETE").write_text(datetime.now(bf.IST).isoformat())
    progress(stage="complete")


if __name__ == "__main__":
    main()
