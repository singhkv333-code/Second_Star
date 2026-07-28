#!/usr/bin/env python3
"""Mine every historical pattern instance in the bar store, with outcomes.

`tool_evaluate_pattern` answers "has this shape been reliable ON THIS CHART"
for one symbol, one interval, one horizon, over at most 2000 bars. This
script is that same question asked of the whole universe and the whole
history at once, written down so an aggregation pass can answer it without
re-running any detector.

Three commitments, all inherited rather than re-invented:

**The detectors are the shipped ones.** `patterns.candlesticks` and
`patterns.chart_patterns`, called with the same pivots (`_pivots(rows, 5)`),
the same ATR tolerance (`_tolerance`) and the same resamplers
(`_resample_intraday` / `_fold_daily`) the live server uses. A second
implementation of any of those would be a future divergence, so there isn't
one here.

**The instance rule is `tool_evaluate_pattern`'s, exactly.** Candles are
measured from the pattern bar; chart shapes only count when `status ==
'confirmed'` and are measured from the confirming break bar; instances closer
together than the horizon share a forward window and so are one piece of
evidence, not several (the first of a cluster is kept). The edge-fitted
shapes — triangles, wedges, channels, rectangles, cups, roundings — are
skipped outright: they are fitted at the live edge of a series, so mining
them here would fabricate a history that the detector cannot honestly claim.

**Every rate ships with its control.** For each (symbol, interval, horizon)
the unconditional h-bar move distribution is stored alongside the events. A
pattern rate without the base rate it must beat is decoration.

Windows and caps are disclosed, never silent: chart shapes are detected over
sliding 600-bar windows stepped 300 (the detector was written for 300-2000
bar windows, not 70k), and an instance whose forward window has not completed
gets NULL outcomes rather than a truncated-window number.

Usage
-----
  python3 sweep_patterns.py --db charto_bars.db --out sweep.db
  python3 sweep_patterns.py --db /mnt/charto/charto_bars_universe.db \\
      --out /mnt/charto/sweep.db --intervals 15m,1h,1d --procs 8

Output tables: events, controls, meta.
"""
from __future__ import annotations

import argparse
import multiprocessing as mp
import os
import sqlite3
import sys
import time
import traceback
from bisect import bisect_left
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import dataserver as ds   # noqa: E402  — helpers only; it serves under __main__
import patterns as pt     # noqa: E402


# ── policy constants, all reported into meta ──────────────────────
CHUNK_ROWS = 250_000      # 1-min rows pulled per fetchmany; bounds worker RSS
CHART_WINDOW = 600        # bars per chart-pattern detection window
CHART_STEP = 300          # window stride (50% overlap, so nothing straddles)
CHART_MIN_WINDOW = 120    # a tail shorter than this cannot hold a formation
DECLUSTER_H = 10          # horizon the stored keep flag is computed at
HORIZONS = (5, 10, 20)    # forward horizons measured per event
EXCURSION_BARS = 20       # MFE / MAE window

# The shapes with no honest history. Imported, not re-listed — a second copy
# of this set would drift from the tool that refuses to score them.
EDGE_ONLY = ds._EDGE_ONLY
CHART_MINABLE = tuple(k for k in pt.CHART_KINDS if k not in EDGE_ONLY)

POLICY_LINE = ("edge-fitted shapes excluded: no historical instance record "
               "(" + ", ".join(sorted(EDGE_ONLY)) + ")")

_DAILY = ("1d", "1w", "1mo")


# ── resampling: one streamed pass over the symbol's 1-min rows ────
def _merge_tail(dst: list, part: list) -> None:
    """Append `part` to `dst`, fusing a bucket split across a read chunk.

    Both resamplers stamp a bar with the bucket's own open ts, so two bars
    carrying the same stamp are the same bucket seen twice — never two
    buckets. Nothing here re-derives bucket arithmetic.
    """
    if not part:
        return
    if dst and dst[-1][0] == part[0][0]:
        b, p = dst[-1], part[0]
        b[2] = max(b[2], p[2])
        b[3] = min(b[3], p[3])
        b[4] = p[4]
        b[5] += p[5]
        part = part[1:]
    dst.extend(part)


def _series(con: sqlite3.Connection, sym: str,
            intervals: list[str]) -> tuple[dict[str, list[tuple]], int]:
    """Read the symbol's 1-min rows ONCE, ascending → resampled series.

    Zero-priced minutes are dropped before resampling, and the count comes
    back so it can be disclosed. The store carries all-zero placeholder
    minutes for some symbols (INFY alone has 4500 of them, twelve whole
    sessions). Resampling them keeps the arithmetic legal but poisons it:
    the bucket's low becomes 0, a folded day becomes an all-zero bar, and a
    forward return measured off it reads as -100%. One symbol's zero-open
    also divides by zero inside the shipped detector. A minute with no price
    is not a bar, so it is removed here rather than measured.
    """
    acc: dict[str, list] = {iv: [] for iv in intervals}
    dropped = 0
    cur = con.execute(
        "SELECT ts,o,h,l,c,v FROM bars WHERE symbol=? ORDER BY ts", (sym,))
    while True:
        raw = cur.fetchmany(CHUNK_ROWS)
        if not raw:
            break
        chunk = [r for r in raw if r[1] > 0 and r[2] > 0 and r[3] > 0 and r[4] > 0]
        dropped += len(raw) - len(chunk)
        if not chunk:
            continue
        for iv in intervals:
            part = (ds._fold_daily(chunk) if iv == "1d"
                    else ds._resample_intraday(chunk, ds.INTRADAY_MIN[iv]))
            _merge_tail(acc[iv], part)
    return ({iv: [tuple(b) for b in bars] for iv, bars in acc.items()}, dropped)


# ── outcomes ──────────────────────────────────────────────────────
def _outcomes(rows: list[tuple], i: int, direction: str) -> tuple:
    """Forward returns and excursions measured FROM the completion bar.

    An incomplete forward window comes back NULL. Grading an event on a
    truncated window would quietly report a 3-bar move as a 20-bar one.
    """
    n = len(rows)
    c0 = rows[i][4]
    if not c0:
        return (None, None, None, None, None)
    fwd = []
    for h in HORIZONS:
        j = i + h
        fwd.append(round((rows[j][4] - c0) / c0 * 100, 4) if j < n else None)
    fav = adv = None
    if i + EXCURSION_BARS < n:
        win = rows[i + 1:i + 1 + EXCURSION_BARS]
        hi = max(r[2] for r in win)
        lo = min(r[3] for r in win)
        up = (hi - c0) / c0 * 100
        dn = (lo - c0) / c0 * 100
        if direction == "bearish":
            # favourable for a bearish shape is price falling
            fav, adv = round(-dn, 4), round(-up, 4)
        else:
            # bullish AND neutral: plain high / low excursion
            fav, adv = round(up, 4), round(dn, 4)
    return (fwd[0], fwd[1], fwd[2], fav, adv)


def _controls(rows: list[tuple], sym: str, interval: str) -> list[tuple]:
    """Every unconditional h-bar close-to-close move in the full series."""
    closes = [r[4] for r in rows]
    n = len(closes)
    out = []
    for h in HORIZONS:
        up = dn = 0
        tot = 0.0
        cnt = 0
        for j in range(n - h):
            a = closes[j]
            if not a:
                continue
            m = (closes[j + h] - a) / a * 100
            cnt += 1
            tot += abs(m)
            if m > 0:
                up += 1
            elif m < 0:
                dn += 1
        out.append((sym, interval, h, cnt,
                    round(up / cnt * 100, 4) if cnt else None,
                    round(dn / cnt * 100, 4) if cnt else None,
                    round(tot / cnt, 4) if cnt else None))
    return out


# ── detection ─────────────────────────────────────────────────────
def _candle_events(rows: list[tuple], start: int, ist) -> list[dict]:
    """All candle instances in rows[start:], as {i, i0, kind, direction}.

    `limit` is deliberately absurd: `candlesticks` sorts by bars_ago and
    truncates to `limit`, which would keep only the most recent hits and
    silently drop the history this whole script exists to collect.
    """
    sub = rows[start:] if start else rows
    if len(sub) < 20:
        return []
    ns = len(sub)
    found = pt.candlesticks(sub, ds._atr(sub, 14), ist, None, limit=10 ** 9)
    out = []
    for f in found:
        i = ns - 1 - f["bars_ago"]
        bars = int(f.get("bars", 1) or 1)
        out.append({"i": start + i, "i0": start + max(0, i - bars + 1),
                    "kind": f["pattern"], "direction": f["direction"]})
    return out


def _chart_events(rows: list[tuple], ist) -> list[dict]:
    """Confirmed chart formations, mined over sliding windows.

    `chart_patterns` was written for the 300-2000 bar window a chart shows,
    not for 70k bars: its pivots and its ATR tolerance are properties of the
    window handed to it. Running it once over a decade of 15m bars would
    measure a swing against a tolerance set by a different market. So it is
    run over overlapping windows, each with its own pivots and tolerance, and
    the same formation seen from two windows is kept once.
    """
    n = len(rows)
    want = set(CHART_MINABLE)
    seen: set = set()
    out: list[dict] = []
    starts = list(range(0, max(1, n - CHART_WINDOW + 1), CHART_STEP))
    if starts and starts[-1] + CHART_WINDOW < n:
        starts.append(max(0, n - CHART_WINDOW))
    for s in starts:
        w = rows[s:s + CHART_WINDOW]
        if len(w) < CHART_MIN_WINDOW:
            continue
        nw = len(w)
        found = pt.chart_patterns(w, ds._pivots(w, 5), ds._tolerance(w),
                                  ist, want, limit=10 ** 6)
        for f in found:
            # an unconfirmed shape has no completion bar to measure from
            if f.get("status") != "confirmed":
                continue
            i2 = s + (nw - 1 - f["bars_ago"])
            i1 = i2 - int(f.get("span_bars", 0) or 0)
            key = (f["pattern"], rows[i1][0])
            if key in seen:
                continue
            seen.add(key)
            comp = min(n - 1, i2 + int(f.get("bars_to_break", 0) or 0))
            out.append({"i": comp, "i0": i1, "i1": i2, "kind": f["pattern"],
                        "direction": f["direction"]})
    return out


def _decluster(events: list[dict]) -> None:
    """Stamp keep_h10 per (kind) — instances inside DECLUSTER_H bars of the
    one before them share their forward window, so the first of a cluster
    carries the evidence and the rest are marked, not deleted. Storing the
    flag instead of filtering lets an aggregation pass choose a horizon
    without re-ordering anything."""
    by_kind: dict[str, list[dict]] = {}
    for e in events:
        by_kind.setdefault(e["kind"], []).append(e)
    for group in by_kind.values():
        group.sort(key=lambda x: x["i"])
        last = -10 ** 9
        for e in group:
            if e["i"] - last >= DECLUSTER_H:
                e["keep"] = 1
                last = e["i"]
            else:
                e["keep"] = 0


# ── per-symbol worker ─────────────────────────────────────────────
_CFG: dict = {}


def _init(cfg: dict) -> None:
    _CFG.update(cfg)


def _sweep(sym: str) -> dict:
    t0 = time.time()
    try:
        con = sqlite3.connect(f"file:{_CFG['db']}?mode=ro", uri=True)
        con.execute("PRAGMA query_only=ON")
        try:
            series, dropped = _series(con, sym, _CFG["intervals"])
        finally:
            con.close()

        events: list[tuple] = []
        controls: list[tuple] = []
        spans: dict[str, tuple] = {}
        for interval in _CFG["intervals"]:
            rows = series.get(interval) or []
            if len(rows) < 60:
                continue
            spans[interval] = (len(rows), rows[0][0], rows[-1][0])
            wt = interval not in _DAILY
            ist = (lambda ts, _wt=wt: ds._ist(ts, _wt))

            cstart = 0
            years = _CFG["candle_years"]
            if years:
                cut = rows[-1][0] - int(years * 365.25 * 86400)
                cstart = bisect_left([r[0] for r in rows], cut)

            found = [("candlestick", e) for e in _candle_events(rows, cstart, ist)]
            found += [("chart", e) for e in _chart_events(rows, ist)]

            flat = [e for _f, e in found]
            _decluster(flat)

            for family, e in found:
                o = _outcomes(rows, e["i"], e["direction"])
                events.append((
                    sym, interval, family, e["kind"], e["direction"],
                    rows[e["i0"]][0],
                    rows[e.get("i1", e["i"])][0],
                    rows[e["i"]][0],
                    "confirmed" if family == "chart" else "detected",
                    e["keep"], o[0], o[1], o[2], o[3], o[4]))
            controls.extend(_controls(rows, sym, interval))

        return {"symbol": sym, "events": events, "controls": controls,
                "spans": spans, "dropped": dropped,
                "secs": time.time() - t0, "error": None}
    except Exception:  # noqa: BLE001 — one bad symbol must not kill the pool
        return {"symbol": sym, "events": [], "controls": [], "spans": {},
                "dropped": 0, "secs": time.time() - t0,
                "error": traceback.format_exc(limit=8)}


# ── output store ──────────────────────────────────────────────────
_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
  symbol TEXT, interval TEXT, family TEXT, kind TEXT, direction TEXT,
  ts_start INTEGER, ts_end INTEGER, ts_completion INTEGER,
  status TEXT, keep_h10 INTEGER,
  fwd_ret_5 REAL, fwd_ret_10 REAL, fwd_ret_20 REAL,
  fav_exc_20 REAL, adv_exc_20 REAL);
CREATE TABLE IF NOT EXISTS controls (
  symbol TEXT, interval TEXT, h INTEGER, n INTEGER,
  up_rate_pct REAL, down_rate_pct REAL, avg_abs_move_pct REAL,
  PRIMARY KEY (symbol, interval, h)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""

_INS_E = ("INSERT INTO events VALUES (" + ",".join("?" * 15) + ")")
_INS_C = "INSERT OR REPLACE INTO controls VALUES (?,?,?,?,?,?,?)"


def _open_out(path: str) -> sqlite3.Connection:
    con = sqlite3.connect(path)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=OFF")
    con.executescript(_SCHEMA)
    con.commit()
    return con


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", required=True, help="bar store (read-only)")
    ap.add_argument("--out", required=True, help="sweep sqlite to write")
    ap.add_argument("--intervals", default="15m,1h,1d")
    ap.add_argument("--symbols", default="", help="comma list; default = all")
    ap.add_argument("--procs", type=int, default=4)
    ap.add_argument("--candle-limit-years", type=float, default=0,
                    help="scan CANDLES only over the last N years (0 = all); "
                         "chart shapes and controls stay full-history. "
                         "Candles are ~97%% of the row count — this is the "
                         "lever if the output db gets too large")
    a = ap.parse_args(argv)

    db = str(Path(a.db).expanduser().resolve())
    if not Path(db).exists():
        print(f"no such db: {db}", file=sys.stderr)
        return 2

    intervals = [x.strip() for x in a.intervals.split(",") if x.strip()]
    bad = [iv for iv in intervals if iv != "1d" and iv not in ds.INTRADAY_MIN]
    if bad:
        print(f"unsupported interval(s): {', '.join(bad)} — "
              f"use 1d or one of {', '.join(ds.INTRADAY_MIN)}", file=sys.stderr)
        return 2

    src = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    have = [r[0] for r in src.execute(
        "SELECT DISTINCT symbol FROM bars ORDER BY symbol")]
    src.close()
    if a.symbols:
        want = [s.strip().upper() for s in a.symbols.split(",") if s.strip()]
        missing = [s for s in want if s not in have]
        if missing:
            print(f"not in {Path(db).name}: {', '.join(missing)}", file=sys.stderr)
        syms = [s for s in want if s in have]
    else:
        syms = have
    if not syms:
        print("no symbols to sweep", file=sys.stderr)
        return 2

    out_path = str(Path(a.out).expanduser().resolve())
    con = _open_out(out_path)
    con.execute("DELETE FROM events")
    con.execute("DELETE FROM controls")
    con.execute("DELETE FROM meta")
    con.commit()

    cfg = {"db": db, "intervals": intervals,
           "candle_years": float(a.candle_limit_years or 0)}
    procs = max(1, min(int(a.procs or 1), len(syms), (os.cpu_count() or 2)))
    print(f"sweep: {len(syms)} symbols x {len(intervals)} intervals "
          f"({', '.join(intervals)}) on {procs} procs → {out_path}")
    print(f"       chart shapes: {len(CHART_MINABLE)} minable, "
          f"{len(EDGE_ONLY)} edge-fitted skipped; "
          f"candles: {len(pt.CANDLE_KINDS)} kinds")

    t_all = time.time()
    n_ev = n_ct = 0
    failures: list[tuple[str, str]] = []
    span_lo: dict[str, int] = {}
    span_hi: dict[str, int] = {}
    span_bars: dict[str, int] = {}
    n_dropped = 0
    dropped_syms: list[str] = []
    done = 0

    ctx = mp.get_context("spawn")
    with ctx.Pool(procs, initializer=_init, initargs=(cfg,)) as pool:
        for res in pool.imap_unordered(_sweep, syms):
            done += 1
            if res["error"]:
                failures.append((res["symbol"], res["error"].strip().splitlines()[-1]))
                print(f"[{done}/{len(syms)}] {res['symbol']:<12} FAILED "
                      f"{res['secs']:6.1f}s  {failures[-1][1][:90]}")
                continue
            if res["events"]:
                con.executemany(_INS_E, res["events"])
            if res["controls"]:
                con.executemany(_INS_C, res["controls"])
            con.commit()
            n_ev += len(res["events"])
            n_ct += len(res["controls"])
            for iv, (nb, lo, hi) in res["spans"].items():
                span_bars[iv] = span_bars.get(iv, 0) + nb
                span_lo[iv] = min(span_lo.get(iv, lo), lo)
                span_hi[iv] = max(span_hi.get(iv, hi), hi)
            if res["dropped"]:
                n_dropped += res["dropped"]
                dropped_syms.append(f"{res['symbol']}:{res['dropped']}")
            print(f"[{done}/{len(syms)}] {res['symbol']:<12} "
                  f"{len(res['events']):>7} events  {res['secs']:6.1f}s"
                  + (f"  ({res['dropped']} zero-price minutes dropped)"
                     if res["dropped"] else ""))

    wall = time.time() - t_all
    print("indexing…")
    con.executescript(
        "CREATE INDEX IF NOT EXISTS ix_ev_kind ON events"
        " (kind, interval, keep_h10);"
        "CREATE INDEX IF NOT EXISTS ix_ev_sym ON events (symbol, interval, kind);"
        "CREATE INDEX IF NOT EXISTS ix_ev_ts ON events (ts_completion);")

    # Corporate actions are not adjusted for in the 1-min store, so a split,
    # bonus or price-scale change lands as a single impossible forward return.
    # Counted and named here rather than quietly winsorised away — an
    # aggregation pass should exclude these, and it can only do that if it
    # knows they exist.
    ca = con.execute(
        "SELECT COUNT(*) FROM events WHERE ABS(COALESCE(fwd_ret_5,0))>50 "
        "OR ABS(COALESCE(fwd_ret_10,0))>50 OR ABS(COALESCE(fwd_ret_20,0))>50"
    ).fetchone()[0]
    ca_syms = [f"{s}:{n}" for s, n in con.execute(
        "SELECT symbol, COUNT(*) FROM events WHERE ABS(COALESCE(fwd_ret_5,0))>50 "
        "OR ABS(COALESCE(fwd_ret_10,0))>50 OR ABS(COALESCE(fwd_ret_20,0))>50 "
        "GROUP BY 1 ORDER BY 2 DESC")]

    meta = {
        "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source_db": db,
        "intervals": ",".join(intervals),
        "symbols_requested": str(len(syms)),
        "symbols_ok": str(len(syms) - len(failures)),
        "symbols_failed": str(len(failures)),
        "failures": "; ".join(f"{s}: {e}" for s, e in failures) or "none",
        "events_rows": str(n_ev),
        "controls_rows": str(n_ct),
        "wall_seconds": str(round(wall, 1)),
        "detector_policy": POLICY_LINE,
        "zero_price_minutes_dropped": str(n_dropped),
        "zero_price_by_symbol": ", ".join(dropped_syms) or "none",
        "corporate_action_outliers": str(ca),
        "corporate_action_by_symbol": ", ".join(ca_syms) or "none",
        "corporate_action_caveat": (
            "the 1-min store is UNADJUSTED for corporate actions, so a split, "
            "bonus or price-scale change shows up as one impossible forward "
            "return rather than as a gap. Events with |fwd_ret| > 50 are "
            "counted above and should be excluded when aggregating; they are "
            "kept here because deleting them would hide the artefact."),
        "data_hygiene": (
            "1-min rows with any non-positive OHLC are dropped before "
            "resampling — they are placeholders, not bars. Left in, a bucket "
            "inherits a 0 low and a folded day becomes an all-zero bar, which "
            "reads downstream as a -100% forward return."),
        "candle_kinds": str(len(pt.CANDLE_KINDS)),
        "chart_kinds_minable": ",".join(sorted(CHART_MINABLE)),
        "chart_kinds_skipped": ",".join(sorted(EDGE_ONLY)),
        "candle_limit_years": (
            f"{a.candle_limit_years or 0} (candles only; chart shapes and "
            "controls always cover the full series)"),
        "chart_window_bars": str(CHART_WINDOW),
        "chart_window_step": str(CHART_STEP),
        "decluster_horizon_bars": str(DECLUSTER_H),
        "horizons": ",".join(str(h) for h in HORIZONS),
        "excursion_bars": str(EXCURSION_BARS),
        "instance_rule": (
            "candles: the pattern bar; chart shapes: status=='confirmed' only, "
            "measured from the confirming break bar (end + bars_to_break). "
            "Mirrors tool_evaluate_pattern exactly."),
        "decluster_rule": (
            f"per (symbol, interval, kind), instances closer than "
            f"{DECLUSTER_H} bars keep the FIRST; keep_h10=0 marks the rest. "
            "Rows are never dropped — filter on keep_h10 when aggregating at "
            f"h<={DECLUSTER_H}."),
        "outcome_rule": (
            "fwd_ret_h = (close[i+h]-close[i])/close[i]*100 from the "
            "completion bar i. fav_exc_20 / adv_exc_20 = best / worst "
            f"excursion over the next {EXCURSION_BARS} bars, signed toward "
            "the pattern's direction (bearish flipped; neutral stores plain "
            "high/low excursions). An incomplete forward window is NULL "
            "(too_recent), never a truncated-window value."),
        "control_rule": (
            "controls hold every unconditional h-bar close-to-close move in "
            "the same series. The aggregate control for a (kind, interval, h) "
            "is the instance-count-weighted average of the per-symbol control "
            "rate for that pattern's direction — a rate without a control is "
            "decoration."),
        "chart_window_caveat": (
            f"chart shapes are detected in {CHART_WINDOW}-bar windows stepped "
            f"{CHART_STEP}, each with its own pivots and ATR tolerance, "
            "matching how the live tool sees a chart. A break landing more "
            "than a window past its formation is therefore not counted."),
    }
    for iv in intervals:
        if iv in span_bars:
            meta[f"span_{iv}"] = (
                f"{ds._ist(span_lo[iv], False)} → {ds._ist(span_hi[iv], False)} "
                f"IST, {span_bars[iv]} bars across {len(syms) - len(failures)} symbols")
    con.executemany("INSERT OR REPLACE INTO meta VALUES (?,?)",
                    sorted(meta.items()))
    con.commit()
    con.close()

    ok = len(syms) - len(failures)
    print(f"\ndone: {n_ev} events, {n_ct} control rows, {ok}/{len(syms)} symbols, "
          f"{wall:.1f}s wall ({wall / max(1, ok):.1f}s/symbol at {procs} procs)")
    if failures:
        print(f"failed: {', '.join(s for s, _ in failures)}")
    print(f"policy: {POLICY_LINE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
