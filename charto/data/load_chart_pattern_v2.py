#!/usr/bin/env python3
"""Load an enriched chart-pattern artifact into versioned Azure PG tables.

The loader writes ``*_staging`` first, validates row counts, then swaps the
tables in one transaction.  Existing V1 pattern tables are never named.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import sqlite3
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _dsn() -> str:
    from dotenv import dotenv_values
    env = dotenv_values(HERE.parents[1] / "pivot" / ".env")
    if not env.get("FINANCIALS_DSN"):
        raise RuntimeError("FINANCIALS_DSN is missing from pivot/.env")
    return env["FINANCIALS_DSN"]


def aggregates(db: sqlite3.Connection) -> list[tuple]:
    """Build horizon-specific statistics from independent event windows.

    Same-kind signals for a security are retained only when their completion
    bars are at least ``horizon`` native bars apart.  This prevents a long
    formation that is rediscovered on adjacent bars from masquerading as many
    independent observations.
    """
    controls = {(s, iv, h): (up, dn, avg, n)
                for s, iv, h, n, up, dn, avg in db.execute(
                    "SELECT symbol,interval,horizon,n,up_rate_pct,"
                    "down_rate_pct,avg_abs_move_pct FROM chart_pattern_controls_v2")}
    groups = defaultdict(list)
    last_kept: dict[tuple, int] = {}
    for row in db.execute(
            "SELECT scope,symbol,interval,kind,direction,ts_completion,features_json,outcomes_json,"
            "fwd_ret_5,fwd_ret_10,fwd_ret_20,mfe_40_pct,mae_40_pct "
            "FROM chart_pattern_events_v2 WHERE status='confirmed' "
            "ORDER BY symbol,interval,kind,ts_completion,pattern_id"):
        sc, sym, iv, kind, direction, ts, fj, oj, r5, r10, r20, mfe, mae = row
        features = json.loads(fj)
        outcome = json.loads(oj)
        bar_i = features.get("completion_bar_index")
        if bar_i is None:
            # Backward-compatible fallback for early research artifacts.  It
            # is deterministic, but newly generated artifacts always carry
            # the exact native bar index.
            seconds = {"1m": 60, "3m": 180, "5m": 300, "15m": 900,
                       "30m": 1800, "1h": 3600, "1d": 86400}.get(iv, 1)
            bar_i = int(ts) // seconds
        for h, ret in ((5, r5), (10, r10), (20, r20)):
            if ret is not None:
                key = (sym, iv, kind, h)
                if key in last_kept and int(bar_i) - last_kept[key] < h:
                    continue
                last_kept[key] = int(bar_i)
                groups[(sc, kind, iv, h)].append(
                    (sym, direction, float(ret), mfe, mae, outcome))
    out = []
    for (sc, kind, iv, h), vals in groups.items():
        directional = [v for v in vals if v[1] in ("bullish", "bearish")]
        hits = sum((r > 0) == (d == "bullish") for _, d, r, *_ in directional)
        rate = round(hits / len(directional) * 100, 2) if directional else None
        ctrl_values = []
        control_n = 0
        for sym, d, *_ in directional:
            c = controls.get((sym, iv, h))
            if c:
                ctrl_values.append(c[0 if d == "bullish" else 1])
        for sym in {v[0] for v in directional}:
            c = controls.get((sym, iv, h))
            if c:
                control_n += c[3]
        ctrl = round(sum(ctrl_values) / len(ctrl_values), 2) if ctrl_values else None
        edge = round(rate - ctrl, 2) if rate is not None and ctrl is not None else None
        edge_se = None
        if directional and rate is not None:
            p = rate / 100
            variance = p * (1 - p) / len(directional)
            if ctrl is not None and control_n:
                pc = ctrl / 100
                variance += pc * (1 - pc) / control_n
            edge_se = round(variance ** 0.5 * 100, 2)
        fav = [r if d == "bullish" else -r for _, d, r, *_ in directional]
        mfe = [float(v[3]) for v in vals if v[3] is not None]
        mae = [float(v[4]) for v in vals if v[4] is not None]
        target = [bool(v[5]["measured_move_hit_40"]) for v in vals
                  if v[5].get("measured_move_hit_40") is not None]
        retest = [bool(v[5]["retest_held"]) for v in vals
                  if v[5].get("retest_held") is not None]
        out.append((
            sc, kind, iv, h, len(vals), len({v[0] for v in vals}), rate,
            ctrl, edge, edge_se,
            round(sum(fav) / len(fav), 4) if fav else None,
            round(sum(mfe) / len(mfe), 4) if mfe else None,
            round(sum(mae) / len(mae), 4) if mae else None,
            round(sum(target) / len(target) * 100, 2) if target else None,
            len(target), round(sum(retest) / len(retest) * 100, 2) if retest else None,
            len(retest),
        ))
    return out


EVENT_DDL = """
CREATE TABLE charto.chart_pattern_events_v2_staging (
  pattern_id TEXT PRIMARY KEY, symbol TEXT NOT NULL, scope TEXT NOT NULL,
  interval TEXT NOT NULL, kind TEXT NOT NULL, direction TEXT NOT NULL,
  breakout_direction TEXT, ts_start BIGINT NOT NULL, ts_end BIGINT NOT NULL,
  ts_first_detectable BIGINT NOT NULL, ts_completion BIGINT NOT NULL,
  status TEXT NOT NULL, detector_version TEXT NOT NULL,
  schema_version INTEGER NOT NULL, features JSONB NOT NULL, outcomes JSONB NOT NULL,
  fwd_ret_5 DOUBLE PRECISION, fwd_ret_10 DOUBLE PRECISION,
  fwd_ret_20 DOUBLE PRECISION, mfe_40_pct DOUBLE PRECISION,
  mae_40_pct DOUBLE PRECISION, loaded_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE charto.chart_pattern_controls_v2_staging (
  scope TEXT, symbol TEXT, interval TEXT, horizon INTEGER, n INTEGER,
  up_rate_pct DOUBLE PRECISION, down_rate_pct DOUBLE PRECISION,
  avg_abs_move_pct DOUBLE PRECISION
);
CREATE TABLE charto.chart_pattern_aggregates_v2_staging (
  scope TEXT, kind TEXT, interval TEXT, horizon INTEGER,
  n INTEGER, n_symbols INTEGER, with_direction_rate_pct DOUBLE PRECISION,
  control_base_rate_pct DOUBLE PRECISION, edge_pp DOUBLE PRECISION,
  edge_se_pp DOUBLE PRECISION, avg_favourable_move_pct DOUBLE PRECISION,
  avg_mfe_40_pct DOUBLE PRECISION, avg_mae_40_pct DOUBLE PRECISION,
  measured_move_hit_rate_pct DOUBLE PRECISION, measured_move_n INTEGER,
  retest_hold_rate_pct DOUBLE PRECISION, retest_n INTEGER,
  PRIMARY KEY(scope,kind,interval,horizon)
);
CREATE TABLE charto.chart_pattern_meta_v2_staging (key TEXT PRIMARY KEY, value TEXT);
"""


EVENT_COLUMNS = (
    "pattern_id,symbol,scope,interval,kind,direction,breakout_direction,"
    "ts_start,ts_end,ts_first_detectable,ts_completion,status,detector_version,"
    "schema_version,features,outcomes,fwd_ret_5,fwd_ret_10,fwd_ret_20,"
    "mfe_40_pct,mae_40_pct"
)


def _copy_event_slice(path: str, dsn: str, lo: int, hi: int, worker: int) -> int:
    """Stream one SQLite rowid slice to PG without retaining it in memory."""
    import psycopg2
    art = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    pg = psycopg2.connect(dsn)
    cur = pg.cursor(); loaded = 0
    for start in range(lo, hi, 100_000):
        buf = io.StringIO()
        writer = csv.writer(buf, delimiter="\t", lineterminator="\n",
                            quoting=csv.QUOTE_MINIMAL)
        end = min(start + 100_000, hi)
        for row in art.execute(
                "SELECT * FROM chart_pattern_events_v2 "
                "WHERE rowid > ? AND rowid <= ? ORDER BY rowid", (start, end)):
            writer.writerow([r"\N" if value is None else value for value in row])
            loaded += 1
        buf.seek(0)
        cur.copy_expert(
            f"COPY charto.chart_pattern_events_v2_staging ({EVENT_COLUMNS}) "
            "FROM STDIN WITH (FORMAT csv, DELIMITER E'\\t', NULL '\\N')", buf)
        pg.commit()
    art.close(); pg.close()
    return loaded


def load(path: str, publish: bool, workers: int = 4) -> None:
    import psycopg2
    from psycopg2.extras import execute_values
    path = str(Path(path).resolve())
    art = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    n_events, max_rowid = art.execute(
        "SELECT COUNT(*),COALESCE(MAX(rowid),0) FROM chart_pattern_events_v2"
    ).fetchone()
    controls = art.execute("SELECT * FROM chart_pattern_controls_v2").fetchall()
    meta = art.execute("SELECT * FROM chart_pattern_meta_v2").fetchall()
    aggs = aggregates(art)
    art.close()
    dsn = _dsn()
    con = psycopg2.connect(dsn); cur = con.cursor()
    cur.execute("CREATE SCHEMA IF NOT EXISTS charto")
    for table in ("events", "controls", "aggregates", "meta"):
        cur.execute(f"DROP TABLE IF EXISTS charto.chart_pattern_{table}_v2_staging")
    cur.execute(EVENT_DDL); con.commit()
    workers = max(1, min(int(workers), 8, max(1, n_events)))
    step = max(1, -(-max_rowid // workers))
    bounds = [(i * step, min((i + 1) * step, max_rowid), i)
              for i in range(workers) if i * step < max_rowid]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        loaded_stream = sum(pool.map(
            lambda b: _copy_event_slice(path, dsn, b[0], b[1], b[2]), bounds))
    execute_values(cur, "INSERT INTO charto.chart_pattern_controls_v2_staging "
                   "VALUES %s", controls, page_size=5000)
    execute_values(cur, "INSERT INTO charto.chart_pattern_aggregates_v2_staging "
                   "VALUES %s", aggs, page_size=5000)
    execute_values(cur, "INSERT INTO charto.chart_pattern_meta_v2_staging VALUES %s",
                   meta)
    cur.execute("SELECT COUNT(*),COUNT(DISTINCT pattern_id) FROM "
                "charto.chart_pattern_events_v2_staging")
    loaded, distinct = cur.fetchone()
    if loaded != n_events or distinct != loaded or loaded_stream != loaded:
        con.rollback(); con.close()
        raise RuntimeError(f"staging validation failed: {loaded=} {distinct=} "
                           f"streamed={loaded_stream} artifact={n_events}")
    cur.execute("CREATE INDEX ix_cpev2_kind_iv_staging ON "
                "charto.chart_pattern_events_v2_staging(kind,interval)")
    cur.execute("CREATE INDEX ix_cpev2_sym_iv_ts_staging ON "
                "charto.chart_pattern_events_v2_staging"
                "(symbol,interval,ts_completion)")
    con.commit()
    if publish:
        cur.execute("BEGIN")
        for table in ("events", "controls", "aggregates", "meta"):
            live = f"charto.chart_pattern_{table}_v2"
            staging = f"charto.chart_pattern_{table}_v2_staging"
            backup = f"charto.chart_pattern_{table}_v2_previous"
            cur.execute(f"DROP TABLE IF EXISTS {backup}")
            cur.execute("SELECT to_regclass(%s)", (live,))
            if cur.fetchone()[0]:
                cur.execute(f"ALTER TABLE {live} RENAME TO "
                            f"chart_pattern_{table}_v2_previous")
            cur.execute(f"ALTER TABLE {staging} RENAME TO chart_pattern_{table}_v2")
        con.commit()
    con.close()
    print(f"PG chart V2: {loaded:,} events, {len(controls):,} controls, "
          f"{len(aggs):,} aggregates — {'published' if publish else 'staged'}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("artifact")
    ap.add_argument("--publish", action="store_true",
                    help="atomically replace V2 live tables after staging validation")
    ap.add_argument("--workers", type=int, default=4,
                    help="parallel PostgreSQL COPY streams (default: 4, max: 8)")
    a = ap.parse_args(); t = time.time()
    load(a.artifact, a.publish, a.workers)
    print(f"elapsed {time.time()-t:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
