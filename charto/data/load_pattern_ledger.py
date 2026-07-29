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

Aggregates are keyed (scope, kind, interval, horizon). Scope is the market the
evidence came from — 500 NSE stocks trade 375 minutes a day with a gap every
night, Bitcoin trades 1440 with none, and an index prints no volume at all.
Pooling a hammer's forward return across those produces a number describing no
market in particular, so `stats` writes ONLY the scopes present in the
artifact and leaves every other scope's rows untouched. Interval is a separate
key for the same reason: a 10-bar horizon is two weeks on 1d and 2.5 hours on
15m, and a rate measured on one never transfers to the other.

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

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dataserver as ds   # noqa: E402  — scope_for only; it serves under __main__

HERE = Path(__file__).parent
LOCAL_DB = HERE / "charto_bars.db"
HORIZONS = (5, 10, 20)
MIN_N = 5
PG_WORKERS = 3


# ── aggregation (shared by both subcommands) ──────────────────────

def build_aggregates(art: sqlite3.Connection) -> list[tuple]:
    """[(scope, kind, interval, horizon, family, n_raw, n, n_symbols,
         with_direction_rate_pct|None, withheld|None, control_base_rate_pct,
         edge_pp, avg_move_pct)]

    Scope is resolved from the symbol rather than stored in the artifact, so
    a sweep produced before scopes existed aggregates correctly and no
    artifact has to be re-run to gain the dimension.
    """
    controls: dict[tuple, tuple] = {}
    for sym, iv, h, n, up, down, avg_abs in art.execute(
            "SELECT symbol, interval, h, n, up_rate_pct, down_rate_pct, "
            "avg_abs_move_pct FROM controls"):
        controls[(sym, iv, h)] = (up, down, avg_abs)

    scope_of: dict[str, str] = {}
    out = []
    for h in HORIZONS:
        col = f"fwd_ret_{h}"
        rows = art.execute(
            f"SELECT kind, interval, family, symbol, direction, {col} "
            f"FROM events WHERE keep_h10=1").fetchall()
        by_key: dict[tuple, list] = {}
        raw = {}
        for kind, iv, fam, sym, direction, fwd in rows:
            sc = scope_of.get(sym)
            if sc is None:
                sc = scope_of[sym] = ds.scope_for(sym)
            raw[(sc, kind, iv)] = raw.get((sc, kind, iv), 0) + 1
            if fwd is None:
                continue
            by_key.setdefault((sc, kind, iv, fam), []).append(
                (sym, direction, fwd))
        for (sc, kind, iv, fam), inst in by_key.items():
            n = len(inst)
            syms = {s for s, _, _ in inst}
            directional = [(s, d, f) for s, d, f in inst
                           if d in ("bullish", "bearish")]
            rate = withheld = ctrl = edge = se = None
            if directional and n >= MIN_N:
                hits = sum(1 for _, d, f in directional
                           if (f > 0) == (d == "bullish"))
                rate = round(hits / len(directional) * 100, 1)
                # Binomial standard error of the rate, in the same points the
                # edge is quoted in. Scoping made this necessary: pooling 500
                # stocks put n in the tens of thousands, where sampling noise
                # was under a tenth of a point and could be ignored. A scope
                # can now be one symbol — India VIX averages 47 graded
                # instances per kind — and there a 6-point "edge" is roughly
                # one SE, i.e. nothing. Stored so the reply can say which.
                p = hits / len(directional)
                se = round((p * (1 - p) / len(directional)) ** 0.5 * 100, 1)
                cs = [controls[(s, iv, h)][0 if d == "bullish" else 1]
                      for s, d, _ in directional if (s, iv, h) in controls]
                if cs:
                    ctrl = round(sum(cs) / len(cs), 1)
                    edge = round(rate - ctrl, 1)
            elif directional:
                withheld = (f"only {n} graded instances across "
                            f"{ds.SCOPE_LABEL.get(sc, sc)} on {iv} — below "
                            f"the {MIN_N}-sample floor, so no rate is "
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
            out.append((sc, kind, iv, h, fam, raw.get((sc, kind, iv), 0), n,
                        len(syms), rate, withheld, ctrl, edge, se, avg_move))
    return out


_STATS_DDL = """
CREATE TABLE IF NOT EXISTS pattern_stats (
  scope TEXT NOT NULL DEFAULT 'equity_in',
  kind TEXT, interval TEXT, horizon INTEGER, family TEXT,
  n_raw INTEGER, n INTEGER, n_symbols INTEGER,
  with_direction_rate_pct REAL, with_direction_rate_pct_withheld TEXT,
  control_base_rate_pct REAL, edge_pp REAL, edge_se_pp REAL,
  avg_move_pct REAL,
  PRIMARY KEY (scope, kind, interval, horizon));
CREATE TABLE IF NOT EXISTS pattern_stats_meta (
  scope TEXT NOT NULL DEFAULT 'equity_in', key TEXT, value TEXT,
  PRIMARY KEY (scope, key));
"""


def _migrate(con: sqlite3.Connection) -> None:
    """Give the stats tables the scope dimension, keeping what is there.

    The existing rows are the 500-stock equity sweep — ~40M events over 413M
    1-min bars on the VM, an artifact that no longer exists locally and is not
    cheap to reproduce. So the migration RELABELS them rather than rebuilding:
    `equity_in` is not a guess, it is what that run measured.

    Done by copy-into-a-new-table rather than ALTER TABLE ADD COLUMN, because
    the column is only half the change: the PRIMARY KEY has to widen to
    (scope, kind, interval, horizon) too. Added as a bare column, the old
    3-part key survives and the FIRST index or crypto row carrying a kind the
    equity sweep already measured fails on a uniqueness violation — the
    migration would look successful right up to the moment it blocked the
    thing it exists to enable.
    """
    old = {"pattern_stats": ("kind, interval, horizon, family, n_raw, n, "
                             "n_symbols, with_direction_rate_pct, "
                             "with_direction_rate_pct_withheld, "
                             "control_base_rate_pct, edge_pp, "
                             # exact from the columns already stored: the
                             # pre-scope artifact is gone, but SE is a closed
                             # form in (rate, n), so nothing is re-derived
                             "CASE WHEN with_direction_rate_pct IS NULL "
                             "  OR n IS NULL OR n < 1 THEN NULL ELSE "
                             "  ROUND(SQRT(with_direction_rate_pct/100.0 * "
                             "  (1 - with_direction_rate_pct/100.0) / n)"
                             "  * 100, 1) END, avg_move_pct"),
           "pattern_stats_meta": "key, value"}
    # The target shape, taken by BUILDING the schema in a scratch database and
    # asking SQLite what it made. Parsing the DDL text for column names is how
    # this went wrong once already — a split on the wrong separator reported 7
    # columns instead of 14, so the "already migrated" test never passed and
    # every call silently rebuilt both tables. sqlite3 is the only thing that
    # reads its own DDL correctly, so it does the reading.
    probe = sqlite3.connect(":memory:")
    probe.executescript(_STATS_DDL)
    want = {t: {r[1] for r in probe.execute(f"PRAGMA table_info({t})")}
            for t in ("pattern_stats", "pattern_stats_meta")}
    probe.close()
    for tbl, cols in old.items():
        info = list(con.execute(f"PRAGMA table_info({tbl})"))
        if not info:
            continue
        have = [r[1] for r in info]
        in_key = any(r[1] == "scope" and r[5] for r in info)
        # gated on the KEY and on the full column set, not on `scope` alone:
        # a table carrying a bare scope column still needs the key widened,
        # and one with the right key can still be missing a later column
        if in_key and not want[tbl] - set(have):
            continue
        n = con.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
        con.execute(f"ALTER TABLE {tbl} RENAME TO {tbl}_pre_scope")
        con.executescript(_STATS_DDL)
        sc = "COALESCE(scope,'equity_in')" if "scope" in have else "'equity_in'"
        con.execute(f"INSERT INTO {tbl} SELECT {sc}, {cols} "
                    f"FROM {tbl}_pre_scope")
        con.execute(f"DROP TABLE {tbl}_pre_scope")
        print(f"  migrated {tbl}: {n} row(s) rebuilt onto "
              f"(scope, …) key, {len(want[tbl])} columns")
    con.commit()


def stats(path: str) -> None:
    art = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    t0 = time.time()
    rows = build_aggregates(art)
    meta = dict(art.execute("SELECT key, value FROM meta"))
    policy = meta.get("detector_policy", "")
    n_events = art.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    # as_of PER SCOPE, read off the events themselves: one artifact can hold
    # markets whose data ends on different days (crypto trades the day an
    # index is shut), and a single sweep-wide date would misdate one of them.
    latest: dict[str, tuple[int, int]] = {}   # scope -> (max ts, tz offset)
    ev_by_scope: dict[str, int] = {}
    for sym, mx, cnt in art.execute(
            "SELECT symbol, MAX(ts_completion), COUNT(*) FROM events "
            "GROUP BY symbol"):
        sc = ds.scope_for(sym)
        ev_by_scope[sc] = ev_by_scope.get(sc, 0) + int(cnt)
        if not mx:
            continue
        off = ds.session_for(sym)[1]
        prev = latest.get(sc)
        if prev is None or int(mx) > prev[0]:
            latest[sc] = (int(mx), off)
    # rendered on each market's OWN clock — a crypto completion bar dated by
    # the IST calendar is the same off-by-one the read path already fixed
    as_of = {sc: time.strftime("%d %b %Y", time.gmtime(ts + off))
             for sc, (ts, off) in latest.items()}
    art.close()

    con = sqlite3.connect(LOCAL_DB)
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(_STATS_DDL)
    _migrate(con)
    scopes = sorted({r[0] for r in rows})
    # Replace only what this artifact actually measured. A DROP here would
    # silently delete the scopes it says nothing about.
    for sc in scopes:
        con.execute("DELETE FROM pattern_stats WHERE scope=?", (sc,))
        con.execute("DELETE FROM pattern_stats_meta WHERE scope=?", (sc,))
    con.executemany(
        "INSERT INTO pattern_stats VALUES "
        "(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    con.executemany("INSERT INTO pattern_stats_meta VALUES (?,?,?)", [
        (sc, k, v) for sc in scopes for k, v in (
            ("as_of", as_of.get(sc, "")),
            ("n_events", str(ev_by_scope.get(sc, 0))),
            ("policy", policy))])
    con.commit()
    for sc in scopes:
        n, nsym = con.execute(
            "SELECT COUNT(*), MAX(n_symbols) FROM pattern_stats WHERE scope=?",
            (sc,)).fetchone()
        print(f"  {sc:<14} {n:>4} aggregate rows, {nsym} symbols, "
              f"as_of {as_of.get(sc) or 'unknown'}")
    total = con.execute("SELECT COUNT(*) FROM pattern_stats").fetchone()[0]
    kept = con.execute(
        "SELECT COUNT(*) FROM pattern_stats WHERE scope NOT IN "
        f"({','.join('?' * len(scopes))})", scopes).fetchone()[0] if scopes else 0
    con.close()
    print(f"pattern_stats: {total} rows total from {n_events:,} events in "
          f"{time.time()-t0:.1f}s ({kept} row(s) in untouched scopes kept)")


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
  scope TEXT, kind TEXT, interval TEXT, horizon SMALLINT, family TEXT,
  n_raw INTEGER, n INTEGER, n_symbols INTEGER,
  with_direction_rate_pct REAL, with_direction_rate_pct_withheld TEXT,
  control_base_rate_pct REAL, edge_pp REAL, edge_se_pp REAL,
  avg_move_pct REAL);
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
            "SELECT scope, kind, interval, horizon, family, n_raw, n, "
            "n_symbols, with_direction_rate_pct, "
            "with_direction_rate_pct_withheld, control_base_rate_pct, "
            "edge_pp, edge_se_pp, avg_move_pct FROM pattern_stats").fetchall()
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


def migrate() -> None:
    """Add the scope dimension to an existing pattern_stats without an
    artifact — so the read path becomes scope-aware before any new sweep."""
    con = sqlite3.connect(LOCAL_DB)
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(_STATS_DDL)
    _migrate(con)
    for sc, n in con.execute("SELECT scope, COUNT(*) FROM pattern_stats "
                             "GROUP BY scope ORDER BY 2 DESC"):
        print(f"  {sc:<14} {n} aggregate rows")
    con.close()


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "migrate" and len(sys.argv) == 2:
        migrate()
    elif len(sys.argv) == 3 and cmd in ("stats", "pg"):
        (stats if cmd == "stats" else pg)(sys.argv[2])
    else:
        sys.exit(__doc__)
