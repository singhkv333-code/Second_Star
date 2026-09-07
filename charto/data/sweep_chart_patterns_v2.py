#!/usr/bin/env python3
"""Mine an enriched, chart-pattern-only, point-in-time historical ledger.

The existing sweep is intentionally candle-heavy.  This companion pipeline
keeps chart research isolated and stores flexible JSON feature/outcome payloads
alongside the indexed facts needed by product queries.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import multiprocessing as mp
import os
import sqlite3
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import chart_patterns_v2 as v2  # noqa: E402
import dataserver as ds         # noqa: E402
import patterns as pt           # noqa: E402
import sweep_patterns as base   # noqa: E402

SCHEMA_VERSION = 2
DETECTOR_VERSION = v2.DETECTOR_VERSION
HORIZONS = (1, 3, 5, 10, 20, 40)
EDGE_STEP = 10
EDGE_LOOKAHEAD = 60
V2_WINDOW = 2_000
V2_STEP = 1_500
EDGE_ONLY = frozenset(ds._EDGE_ONLY)
NATIVE_EXISTING = frozenset(k for k in pt.CHART_KINDS if k not in EDGE_ONLY)

_CFG: dict = {}


def _init(cfg: dict) -> None:
    _CFG.update(cfg)


def _jsonable(value):
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()
                if not str(k).startswith("_")}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, float):
        return round(value, 8)
    return value


def _context(rows: list[tuple], i0: int, i1: int, comp: int,
             tol: float) -> dict:
    closes = [r[4] for r in rows]
    vols = [float(r[5] or 0) for r in rows]
    atr = ds._atr(rows, 14)
    before = closes[max(0, i0 - 20)]
    incoming = (closes[i0] - before) / before * 100 if before else None
    form_vol = [v for v in vols[i0:i1 + 1] if v > 0]
    prior_vol = [v for v in vols[max(0, i0 - 60):i0] if v > 0]
    bv = vols[comp]
    rank = None
    hist = [v for v in vols[max(0, comp - 120):comp] if v > 0]
    if bv > 0 and hist:
        rank = round(sum(v <= bv for v in hist) / len(hist) * 100, 1)
    return {
        "atr": round(atr[comp], 6) if comp < len(atr) and atr[comp] else None,
        "tolerance": round(tol, 6),
        "incoming_return_pct": round(incoming, 3) if incoming is not None else None,
        "formation_avg_volume": round(sum(form_vol) / len(form_vol), 2)
        if form_vol else None,
        "prior_avg_volume": round(sum(prior_vol) / len(prior_vol), 2)
        if prior_vol else None,
        "breakout_volume": bv or None,
        "breakout_volume_percentile": rank,
    }


def _outcomes(rows: list[tuple], comp: int, direction: str,
              measured_move: float | None = None,
              breakout_level: float | None = None) -> dict:
    n = len(rows); c0 = rows[comp][4]
    out = {"entry_close": round(c0, 6)}
    for h in HORIZONS:
        out[f"fwd_ret_{h}"] = (round((rows[comp + h][4] - c0) / c0 * 100, 4)
                                if comp + h < n and c0 else None)
    end = min(n, comp + 41)
    if end > comp + 1 and c0:
        win = rows[comp + 1:end]
        up = (max(r[2] for r in win) - c0) / c0 * 100
        down = (min(r[3] for r in win) - c0) / c0 * 100
        if direction == "bearish":
            out["mfe_40_pct"], out["mae_40_pct"] = round(-down, 4), round(-up, 4)
            fav = [(-((r[3] - c0) / c0 * 100), j + 1) for j, r in enumerate(win)]
        else:
            out["mfe_40_pct"], out["mae_40_pct"] = round(up, 4), round(down, 4)
            fav = [(((r[2] - c0) / c0 * 100), j + 1) for j, r in enumerate(win)]
        out["bars_to_mfe_40"] = max(fav)[1]
    if measured_move is not None:
        hit = next((j for j in range(comp + 1, end)
                    if (rows[j][2] >= measured_move if direction == "bullish"
                        else rows[j][3] <= measured_move)), None)
        out["measured_move"] = round(float(measured_move), 6)
        out["measured_move_hit_40"] = hit is not None
        out["bars_to_measured_move"] = hit - comp if hit is not None else None
    if breakout_level is not None:
        ret = next((j for j in range(comp + 1, min(n, comp + 21))
                    if rows[j][3] <= breakout_level <= rows[j][2]), None)
        out["retest_within_20"] = ret is not None
        out["bars_to_retest"] = ret - comp if ret is not None else None
        if ret is not None:
            out["retest_held"] = (rows[ret][4] >= breakout_level
                                  if direction == "bullish"
                                  else rows[ret][4] <= breakout_level)
    return out


def _event(rows: list[tuple], sym: str, interval: str, p: dict,
           i0: int, i1: int, comp: int, first_seen: int,
           breakout_direction: str | None = None) -> tuple:
    tol = ds._tolerance(rows[:comp + 1])
    direction = p.get("direction", "neutral")
    measured = p.get("measured_move")
    level = p.get("breakout_level", p.get("neckline"))
    payload = _jsonable(p)
    # Retain the native-series position so downstream statistics can remove
    # overlapping forward windows exactly in bar space (calendar timestamps
    # are not equivalent to bars around weekends and exchange holidays).
    payload["completion_bar_index"] = int(comp)
    payload["context"] = _context(rows, i0, i1, comp, tol)
    outcomes = _outcomes(rows, comp, direction, measured, level)
    raw_id = f"{sym}|{interval}|{p['pattern']}|{rows[i0][0]}|{rows[comp][0]}"
    pid = hashlib.sha1(raw_id.encode()).hexdigest()[:20]
    return (
        pid, sym, ds.scope_for(sym), interval, p["pattern"], direction,
        breakout_direction, int(rows[i0][0]), int(rows[i1][0]),
        int(rows[first_seen][0]), int(rows[comp][0]), p.get("status", "confirmed"),
        DETECTOR_VERSION, SCHEMA_VERSION, json.dumps(payload, separators=(",", ":")),
        json.dumps(outcomes, separators=(",", ":")),
        outcomes.get("fwd_ret_5"), outcomes.get("fwd_ret_10"),
        outcomes.get("fwd_ret_20"), outcomes.get("mfe_40_pct"),
        outcomes.get("mae_40_pct"),
    )


def _native_events(rows: list[tuple], sym: str, interval: str, ist) -> list[tuple]:
    """Existing confirmed shapes plus V2 extensions, with rich payloads."""
    n = len(rows); candidates: list[tuple] = []
    starts = list(range(0, max(1, n - base.CHART_WINDOW + 1), base.CHART_STEP))
    if starts and starts[-1] + base.CHART_WINDOW < n:
        starts.append(max(0, n - base.CHART_WINDOW))
    seen = set()
    for s in starts:
        w = rows[s:s + base.CHART_WINDOW]
        if len(w) < base.CHART_MIN_WINDOW:
            continue
        found = pt.chart_patterns(w, ds._pivots(w, 5), ds._tolerance(w), ist,
                                  set(NATIVE_EXISTING), limit=100_000)
        for p in found:
            if p.get("status") != "confirmed":
                continue
            i1 = s + len(w) - 1 - int(p["bars_ago"])
            i0 = max(s, i1 - int(p.get("span_bars", 0)))
            comp = min(n - 1, i1 + int(p.get("bars_to_break", 0)))
            key = (p["pattern"], rows[i0][0], rows[comp][0])
            if key in seen:
                continue
            seen.add(key)
            candidates.append(_event(rows, sym, interval, p, i0, i1, comp, i1))
    # V2 definitions are bounded-window algorithms.  Running them over a full
    # multi-year 1m series creates quadratic pivot work and unbounded memory.
    # A 500-bar overlap exceeds every definition's 121-bar confirmation
    # lookahead, so formations crossing a boundary are still seen and the
    # deterministic event id removes overlap duplicates.
    vstarts = list(range(0, max(1, n - V2_WINDOW + 1), V2_STEP))
    if vstarts and vstarts[-1] + V2_WINDOW < n:
        vstarts.append(max(0, n - V2_WINDOW))
    for s in vstarts:
        w = rows[s:s + V2_WINDOW]
        if len(w) < 60:
            continue
        for raw in v2.additional_chart_patterns(
                w, ds._pivots(w, 5), ds._tolerance(w), ist):
            p = dict(raw)
            comp = s + int(p["completion_i"])
            i1 = s + len(w) - 1 - int(p["bars_ago"])
            i0 = max(s, i1 - int(p.get("span_bars", 0)))
            key = (p["pattern"], rows[i0][0], rows[comp][0])
            if key in seen:
                continue
            seen.add(key)
            p["completion_i"] = comp
            candidates.append(_event(rows, sym, interval, p, i0, i1, comp, i1))
    return candidates


def _edge_break(rows: list[tuple], p: dict, cut: int) -> tuple[int, str] | None:
    """First native-bar close beyond an edge visible at ``cut-1``."""
    pts = p.get("points") or {}
    kind = p["pattern"]
    up_bias = p.get("direction") == "bullish"
    down_bias = p.get("direction") == "bearish"
    for j in range(cut, min(len(rows), cut + EDGE_LOOKAHEAD)):
        dt = j - (cut - 1)
        upper = pts.get("upper_now")
        lower = pts.get("lower_now")
        if upper is not None:
            upper += float(pts.get("upper_slope_per_bar") or 0) * dt
        if lower is not None:
            lower += float(pts.get("lower_slope_per_bar") or 0) * dt
        if kind in ("cup_and_handle", "rounding_bottom", "rounding_top"):
            pp = p.get("points") or {}
            level = pp.get("right_rim")
            if level is None:
                continue
            if kind != "rounding_top" and rows[j][4] > level:
                return j, "up"
            if kind == "rounding_top" and rows[j][4] < level:
                return j, "down"
        if upper is not None and rows[j][4] > upper and not down_bias:
            return j, "up"
        if lower is not None and rows[j][4] < lower and not up_bias:
            return j, "down"
    return None


def _edge_events(rows: list[tuple], sym: str, interval: str, ist,
                 step: int) -> list[tuple]:
    """Replay live-edge detectors at historical cutoffs, then grade later."""
    n = len(rows); raw: list[dict] = []
    for cut in range(base.CHART_MIN_WINDOW, n - 1, max(1, step)):
        s = max(0, cut - base.CHART_WINDOW)
        w = rows[s:cut]
        found = pt.chart_patterns(w, ds._pivots(w, 5), ds._tolerance(w), ist,
                                  set(EDGE_ONLY), limit=100)
        for p in found:
            br = _edge_break(rows, p, cut)
            if not br:
                continue
            comp, bd = br
            i1 = cut - 1
            i0 = max(s, i1 - int(p.get("span_bars", 0)))
            raw.append({"p": p, "i0": i0, "i1": i1, "comp": comp,
                        "first": i1, "bd": bd})
    raw.sort(key=lambda x: (x["comp"], x["p"]["pattern"], x["i0"]))
    kept: list[dict] = []
    for x in raw:
        dup = False
        for y in reversed(kept):
            if y["comp"] < x["comp"] - max(120, x["i1"] - x["i0"]):
                break
            if y["p"]["pattern"] != x["p"]["pattern"]:
                continue
            inter = max(0, min(x["i1"], y["i1"]) - max(x["i0"], y["i0"]))
            short = max(1, min(x["i1"] - x["i0"], y["i1"] - y["i0"]))
            if inter >= 0.6 * short:
                # Preserve the earliest moment this formation was knowable.
                y["first"] = min(y["first"], x["first"])
                dup = True
                break
        if not dup:
            kept.append(x)
    return [_event(rows, sym, interval, x["p"], x["i0"], x["i1"],
                   x["comp"], x["first"], x["bd"]) for x in kept]


def _sweep(sym: str) -> dict:
    t0 = time.time()
    try:
        con = sqlite3.connect(f"file:{_CFG['db']}?mode=ro", uri=True)
        try:
            series, dropped = base._series(con, sym, _CFG["intervals"])
        finally:
            con.close()
        events = []; controls = []
        for iv, rows in series.items():
            if len(rows) < 60:
                continue
            wt = iv not in base._DAILY
            fmt = "%d %b %Y %H:%M" if wt else "%d %b %Y"
            off = ds.session_for(sym)[1]
            ist = lambda ts, _f=fmt, _o=off: datetime.fromtimestamp(  # noqa: E731
                ts + _o, tz=timezone.utc).strftime(_f)
            events.extend(_native_events(rows, sym, iv, ist))
            if not _CFG.get("skip_edge"):
                events.extend(_edge_events(rows, sym, iv, ist, _CFG["edge_step"]))
            for rec in base._controls(rows, sym, iv):
                controls.append((ds.scope_for(sym),) + tuple(rec))
        # pattern_id is a deterministic primary key, so cross-window duplicates
        # collapse without depending on process order.
        unique = {e[0]: e for e in events}
        return {"symbol": sym, "events": list(unique.values()),
                "controls": controls,
                "dropped": dropped, "secs": time.time() - t0, "error": None}
    except Exception:
        return {"symbol": sym, "events": [], "controls": [], "dropped": 0,
                "secs": time.time() - t0,
                "error": traceback.format_exc(limit=10)}


DDL = """
CREATE TABLE IF NOT EXISTS chart_pattern_events_v2 (
  pattern_id TEXT PRIMARY KEY,
  symbol TEXT NOT NULL, scope TEXT NOT NULL, interval TEXT NOT NULL,
  kind TEXT NOT NULL, direction TEXT NOT NULL, breakout_direction TEXT,
  ts_start INTEGER NOT NULL, ts_end INTEGER NOT NULL,
  ts_first_detectable INTEGER NOT NULL, ts_completion INTEGER NOT NULL,
  status TEXT NOT NULL, detector_version TEXT NOT NULL,
  schema_version INTEGER NOT NULL, features_json TEXT NOT NULL,
  outcomes_json TEXT NOT NULL,
  fwd_ret_5 REAL, fwd_ret_10 REAL, fwd_ret_20 REAL,
  mfe_40_pct REAL, mae_40_pct REAL
);
CREATE TABLE IF NOT EXISTS chart_pattern_meta_v2 (
  key TEXT PRIMARY KEY, value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chart_pattern_controls_v2 (
  scope TEXT NOT NULL, symbol TEXT NOT NULL, interval TEXT NOT NULL,
  horizon INTEGER NOT NULL, n INTEGER NOT NULL,
  up_rate_pct REAL, down_rate_pct REAL, avg_abs_move_pct REAL,
  PRIMARY KEY(symbol,interval,horizon)
);
"""
INS = "INSERT OR REPLACE INTO chart_pattern_events_v2 VALUES (" + ",".join("?" * 21) + ")"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--intervals", default="5m,15m,1h,1d")
    ap.add_argument("--symbols", default="")
    ap.add_argument("--procs", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    ap.add_argument("--edge-step", type=int, default=EDGE_STEP,
                    help="native bars between live-edge historical cutoffs")
    ap.add_argument("--skip-edge", action="store_true",
                    help="mine confirmed/V2 patterns only; edge replay can be sharded separately")
    a = ap.parse_args(argv)
    intervals = [x.strip() for x in a.intervals.split(",") if x.strip()]
    bad = [iv for iv in intervals if iv != "1d" and iv not in ds.INTRADAY_MIN]
    if bad:
        ap.error("unsupported intervals: " + ", ".join(bad))
    db = str(Path(a.db).expanduser().resolve())
    src = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    have = {r[0] for r in src.execute("SELECT DISTINCT symbol FROM bars")}
    src.close()
    syms = ([x.strip().upper() for x in a.symbols.split(",") if x.strip()]
            if a.symbols else sorted(have))
    syms = [s for s in syms if s in have]
    if not syms:
        ap.error("no matching symbols")
    out = str(Path(a.out).expanduser().resolve())
    con = sqlite3.connect(out)
    con.executescript(DDL)
    con.execute("DELETE FROM chart_pattern_events_v2")
    con.execute("DELETE FROM chart_pattern_meta_v2")
    con.execute("DELETE FROM chart_pattern_controls_v2")
    con.commit()
    cfg = {"db": db, "intervals": intervals,
           "edge_step": max(1, int(a.edge_step)), "skip_edge": a.skip_edge}
    procs = max(1, min(int(a.procs), len(syms), os.cpu_count() or 2))
    print(f"chart-v2: {len(syms)} symbols x {len(intervals)} intervals "
          f"on {procs} workers; edge step={cfg['edge_step']} → {out}", flush=True)
    t0 = time.time(); total = 0; failures = []
    ctx = mp.get_context("spawn")
    with ctx.Pool(procs, initializer=_init, initargs=(cfg,)) as pool:
        for done, res in enumerate(pool.imap_unordered(_sweep, syms), 1):
            if res["error"]:
                failures.append((res["symbol"], res["error"]))
                print(f"[{done}/{len(syms)}] {res['symbol']} FAILED\n{res['error']}",
                      flush=True)
                continue
            con.executemany(INS, res["events"]); con.commit()
            con.executemany("INSERT OR REPLACE INTO chart_pattern_controls_v2 "
                            "VALUES (?,?,?,?,?,?,?,?)", res["controls"])
            con.commit()
            total += len(res["events"])
            print(f"[{done}/{len(syms)}] {res['symbol']:<14} "
                  f"{len(res['events']):>8} events {res['secs']:7.1f}s",
                  flush=True)
    meta = {
        "schema_version": str(SCHEMA_VERSION),
        "detector_version": DETECTOR_VERSION,
        "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source_db": db, "intervals": ",".join(intervals),
        "symbols": str(len(syms)), "events": str(total),
        "edge_step": str(cfg["edge_step"]), "failures": str(len(failures)),
        "edge_replay": "skipped" if cfg["skip_edge"] else "included",
    }
    con.executemany("INSERT INTO chart_pattern_meta_v2 VALUES (?,?)", meta.items())
    con.executescript("CREATE INDEX IF NOT EXISTS ix_cpev2_kind_iv ON "
                      "chart_pattern_events_v2(kind,interval);"
                      "CREATE INDEX IF NOT EXISTS ix_cpev2_sym_iv ON "
                      "chart_pattern_events_v2(symbol,interval,ts_completion);")
    con.commit(); con.close()
    print(f"done: {total:,} events in {time.time()-t0:.1f}s; "
          f"{len(failures)} failures", flush=True)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
