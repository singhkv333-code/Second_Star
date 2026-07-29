"""Turn the pattern sweep artifact into the numbers the product serves.

Two subcommands, deliberately separable because they have different owners:

  stats <artifact.db>   aggregate the events into `pattern_stats` inside the
                        local charto_bars.db — the tiny table the stdlib
                        dataserver reads. No network. This is what makes
                        "how reliable is X across the market" answerable.
  pg <artifact.db>      load the full events/controls ledger into the Azure
                        Postgres, entirely inside a dedicated `charto`
                        schema. CREATE SCHEMA IF NOT EXISTS + TRUNCATE of
                        charto.* only — nothing outside that schema is ever
                        named. Parallel COPY streams (one per worker) because
                        ~40M rows over WAN single-streamed is an hour.

Aggregation semantics (mirrors tool_evaluate_pattern, pooled):
- graded instance = keep_h10=1 AND fwd_ret_h IS NOT NULL
- directional instance counts as with-direction when sign(fwd_ret_h) matches
  its OWN direction; neutral instances carry no direction rate
- control for a (kind, interval, h) = instance-count-weighted average of the
  per-symbol control rate for each instance's direction
- below 5 graded instances the rate is withheld, never a bare small-n number
- avg_move_pct: directional kinds = mean fwd_ret signed toward the pattern
  (favorable positive); neutral kinds = mean |fwd_ret|

Run under the pivot venv (psycopg2 + dotenv):
  pivot/.venv/bin/python charto/data/load_pattern_ledger.py stats sweep.db
  pivot/.venv/bin/python charto/data/load_pattern_ledger.py pg sweep.db
"""
from __future__ import annotations

import io
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).parent
LOCAL_DB = HERE / "charto_bars.db"
HORIZONS = (5, 10, 20)
MIN_N = 5
PG_WORKERS = 3


# ── aggregation (shared by both subcommands) ──────────────────────

def build_aggregates(art: sqlite3.Connection) -> list[tuple]:
    """[(kind, interval, horizon, family, n_raw, n, n_symbols,
         with_direction_rate_pct|None, withheld|None, control_base_rate_pct,
         edge_pp, avg_move_pct)]"""
    controls: dict[tuple, tuple] = {}
    for sym, iv, h, n, up, down, avg_abs in art.execute(
            "SELECT symbol, interval, h, n, up_rate_pct, down_rate_pct, "
            "avg_abs_move_pct FROM controls"):
        controls[(sym, iv, h)] = (up, down, avg_abs)

    out = []
    for h in HORIZONS:
        col = f"fwd_ret_{h}"
        rows = art.execute(
            f"SELECT kind, interval, family, symbol, direction, {col} "
            f"FROM events WHERE keep_h10=1").fetchall()
        by_key: dict[tuple, list] = {}
        raw = {}
        for kind, iv, fam, sym, direction, fwd in rows:
            raw[(kind, iv)] = raw.get((kind, iv), 0) + 1
            if fwd is None:
                continue
            by_key.setdefault((kind, iv, fam), []).append((sym, direction, fwd))
        for (kind, iv, fam), inst in by_key.items():
            n = len(inst)
            syms = {s for s, _, _ in inst}
            directional = [(s, d, f) for s, d, f in inst
                           if d in ("bullish", "bearish")]
            rate = withheld = ctrl = edge = None
            if directional and n >= MIN_N:
                hits = sum(1 for _, d, f in directional
                           if (f > 0) == (d == "bullish"))
                rate = round(hits / len(directional) * 100, 1)
                cs = [controls[(s, iv, h)][0 if d == "bullish" else 1]
                      for s, d, _ in directional if (s, iv, h) in controls]
                if cs:
                    ctrl = round(sum(cs) / len(cs), 1)
                    edge = round(rate - ctrl, 1)
            elif directional:
                withheld = (f"only {n} graded instances across the universe — "
                            f"below the {MIN_N}-sample floor, so no rate is "
                            f"claimed; say the record is too thin, not weak")
            else:
                withheld = ("a neutral-direction pattern has no directional "
                            "success rate — compare its average absolute move "
                            "with the control instead")
            if directional:
                moves = [f if d == "bullish" else -f for _, d, f in directional]
            else:
                moves = [abs(f) for _, _, f in inst]
            avg_move = round(sum(moves) / len(moves), 2) if moves else None
            out.append((kind, iv, h, fam, raw.get((kind, iv), 0), n,
                        len(syms), rate, withheld, ctrl, edge, avg_move))
    return out


def stats(path: str) -> None:
    art = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    t0 = time.time()
    rows = build_aggregates(art)
    meta = dict(art.execute("SELECT key, value FROM meta"))
    # span_1d looks like "02 Feb 2015 → 22 Jul 2026 IST, ..." — the sweep's
    # end date is the honest as_of for every pooled number
    span = meta.get("span_1d") or meta.get("span_15m") or ""
    as_of = span.split("→")[-1].split("IST")[0].strip() if "→" in span else ""
    policy = meta.get("detector_policy", "")
    n_events = art.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    art.close()

    con = sqlite3.connect(LOCAL_DB)
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript("""
      DROP TABLE IF EXISTS pattern_stats;
      CREATE TABLE pattern_stats (
        kind TEXT, interval TEXT, horizon INTEGER, family TEXT,
        n_raw INTEGER, n INTEGER, n_symbols INTEGER,
        with_direction_rate_pct REAL, with_direction_rate_pct_withheld TEXT,
        control_base_rate_pct REAL, edge_pp REAL, avg_move_pct REAL,
        PRIMARY KEY (kind, interval, horizon));
      DROP TABLE IF EXISTS pattern_stats_meta;
      CREATE TABLE pattern_stats_meta (key TEXT PRIMARY KEY, value TEXT);
    """)
    con.executemany("INSERT INTO pattern_stats VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    rows)
    con.executemany("INSERT INTO pattern_stats_meta VALUES (?,?)", [
        ("as_of", as_of), ("n_events", str(n_events)), ("policy", policy)])
    con.commit()
    n = con.execute("SELECT COUNT(*) FROM pattern_stats").fetchone()[0]
    con.close()
    print(f"pattern_stats: {n} aggregate rows from {n_events:,} events "
          f"in {time.time()-t0:.1f}s (as_of {as_of or 'unknown'})")


# ── PG ledger ─────────────────────────────────────────────────────

DDL = """
CREATE SCHEMA IF NOT EXISTS charto;
CREATE TABLE IF NOT EXISTS charto.pattern_events (
  symbol TEXT, interval TEXT, family TEXT, kind TEXT, direction TEXT,
  ts_start BIGINT, ts_end BIGINT, ts_completion BIGINT,
  status TEXT, keep_h10 SMALLINT,
  fwd_ret_5 REAL, fwd_ret_10 REAL, fwd_ret_20 REAL,
  fav_exc_20 REAL, adv_exc_20 REAL,
  loaded_at TIMESTAMPTZ DEFAULT now());
CREATE TABLE IF NOT EXISTS charto.pattern_controls (
  symbol TEXT, interval TEXT, h SMALLINT, n INTEGER,
  up_rate_pct REAL, down_rate_pct REAL, avg_abs_move_pct REAL);
CREATE TABLE IF NOT EXISTS charto.pattern_aggregates (
  kind TEXT, interval TEXT, horizon SMALLINT, family TEXT,
  n_raw INTEGER, n INTEGER, n_symbols INTEGER,
  with_direction_rate_pct REAL, with_direction_rate_pct_withheld TEXT,
  control_base_rate_pct REAL, edge_pp REAL, avg_move_pct REAL);
CREATE TABLE IF NOT EXISTS charto.pattern_meta (key TEXT, value TEXT);
"""

_EV_COLS = ("symbol,interval,family,kind,direction,ts_start,ts_end,"
            "ts_completion,status,keep_h10,fwd_ret_5,fwd_ret_10,fwd_ret_20,"
            "fav_exc_20,adv_exc_20")


def _dsn() -> str:
    from dotenv import dotenv_values
    return dotenv_values(HERE.parents[1] / "pivot" / ".env")["FINANCIALS_DSN"]


def _copy_slice(path: str, dsn: str, lo: int, hi: int, wid: int) -> int:
    """COPY one rowid slice of events through its own connection."""
    import psycopg2
    art = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    pg = psycopg2.connect(dsn)
    cur = pg.cursor()
    total = 0
    t0 = time.time()
    for start in range(lo, hi, 500_000):
        buf = io.StringIO()
        for r in art.execute(
                f"SELECT {_EV_COLS} FROM events "
                "WHERE rowid > ? AND rowid <= ?",
                (start, min(start + 500_000, hi))):
            buf.write("\t".join(r"\N" if x is None else str(x) for x in r))
            buf.write("\n")
        buf.seek(0)
        cur.copy_expert(
            f"COPY charto.pattern_events ({_EV_COLS}) FROM STDIN", buf)
        pg.commit()
        total += buf.getvalue().count("\n")
        print(f"  [w{wid}] {total:,} rows  {time.time()-t0:.0f}s", flush=True)
    art.close()
    pg.close()
    return total


def pg(path: str) -> None:
    import psycopg2
    dsn = _dsn()
    art = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    n_events = art.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    max_rowid = art.execute("SELECT MAX(rowid) FROM events").fetchone()[0]
    controls = art.execute("SELECT symbol, interval, h, n, up_rate_pct, "
                           "down_rate_pct, avg_abs_move_pct "
                           "FROM controls").fetchall()
    meta = art.execute("SELECT key, value FROM meta").fetchall()
    # `stats` normally ran first and its table IS the aggregation — rebuilding
    # here would repeat ~45 min of event-table passes for identical rows
    try:
        loc = sqlite3.connect(f"file:{LOCAL_DB}?mode=ro", uri=True)
        aggregates = loc.execute(
            "SELECT kind, interval, horizon, family, n_raw, n, n_symbols, "
            "with_direction_rate_pct, with_direction_rate_pct_withheld, "
            "control_base_rate_pct, edge_pp, avg_move_pct "
            "FROM pattern_stats").fetchall()
        loc.close()
    except sqlite3.Error:
        aggregates = []
    if not aggregates:
        aggregates = build_aggregates(art)
    art.close()

    con = psycopg2.connect(dsn)
    cur = con.cursor()
    cur.execute(DDL)
    # idempotent reload — only ever our own schema
    cur.execute("TRUNCATE charto.pattern_events, charto.pattern_controls, "
                "charto.pattern_aggregates, charto.pattern_meta")
    con.commit()

    t0 = time.time()
    step = -(-max_rowid // PG_WORKERS)
    bounds = [(i * step, min((i + 1) * step, max_rowid))
              for i in range(PG_WORKERS)]
    with ThreadPoolExecutor(PG_WORKERS) as ex:
        loaded = sum(ex.map(
            lambda b: _copy_slice(path, dsn, b[0][0], b[0][1], b[1]),
            zip(bounds, range(PG_WORKERS))))

    from psycopg2.extras import execute_values
    execute_values(cur, "INSERT INTO charto.pattern_controls VALUES %s",
                   controls)
    execute_values(cur,
                   "INSERT INTO charto.pattern_aggregates VALUES %s",
                   aggregates)
    execute_values(cur, "INSERT INTO charto.pattern_meta VALUES %s", meta)
    con.commit()
    cur.execute("CREATE INDEX IF NOT EXISTS ix_pe_kind_iv "
                "ON charto.pattern_events (kind, interval)")
    cur.execute("CREATE INDEX IF NOT EXISTS ix_pe_sym_iv "
                "ON charto.pattern_events (symbol, interval)")
    con.commit()
    cur.execute("SELECT COUNT(*) FROM charto.pattern_events")
    n_pg = cur.fetchone()[0]
    con.close()
    ok = "OK" if n_pg == n_events == loaded else "MISMATCH"
    print(f"PG ledger: {n_pg:,} events (artifact {n_events:,}, copied "
          f"{loaded:,}) [{ok}], {len(controls)} controls, "
          f"{len(aggregates)} aggregates, {len(meta)} meta — "
          f"{time.time()-t0:.0f}s with {PG_WORKERS} COPY streams")


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] not in ("stats", "pg"):
        sys.exit(__doc__)
    (stats if sys.argv[1] == "stats" else pg)(sys.argv[2])
