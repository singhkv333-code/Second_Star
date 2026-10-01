#!/usr/bin/env python3
"""Land the corrected Kite universe into one serving store.

Input: the per-exchange stores universe_fix.py corrected (nse_1m, bse_1m,
idx_*, fut_*) plus its sessions.json. Output: charto's own store shape —
`bars`, `bars_1d`, `bars_15m`, `oi_1m`, `oi_1d` keyed by ONE id per
instrument — and the `instrument_master` the dataserver resolves ids,
sessions and price decimals from.

The id scheme (see dataserver._master):
  NSE equity            RELIANCE              (bare, as charto always had)
  any index             NIFTY 50, SENSEX, GIFT NIFTY, US500
                        (bare: no index name repeats across exchanges —
                         checked here, prefixed if one ever does)
  BSE equity            BSE:<tradingsymbol>
  futures contract      NFO:NIFTY26OCTFUT, MCX:GOLD26OCTFUT, NCO:GOLD26OCTFUT
  continuous futures    NFO:NIFTY1! / NFO:NIFTY2!  — except the 17 MCX/CDS
                        roots charto already served bare (GOLD, USDINR, ...),
                        which keep that id for their front month so no saved
                        layout, alert or strategy has to change; the qualified
                        spelling is their alias.

Continuous series are MATERIALISED: the roll table picks which contract is
front on each trading day and those minutes are copied under the 1!/2! id,
so get_bars, alerts and patterns read a continuous future exactly like any
other symbol. Before the first held front contract the daily chart carries
Kite's own continuous daily (front month, rolled at expiry); intraday starts
where our contract minutes do and grows as the feed records.

  python3 land_universe.py --src /data/fixed --dst /data/charto_bars.db
  python3 land_universe.py --src DIR --dst sample.db --only RELIANCE "NIFTY 50" NFO:NIFTY1!
"""
from __future__ import annotations

import argparse, csv, io, json, sqlite3, sys, time, urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

IST = timezone(timedelta(hours=5, minutes=30))
IST_OFF = 19800
LEGACY_ROOTS = {"MCX": {"GOLD", "GOLDM", "SILVER", "SILVERM", "CRUDEOIL", "NATURALGAS",
                        "COPPER", "ZINC", "ALUMINIUM", "LEAD", "NICKEL", "COTTON",
                        "MENTHAOIL"},
                "CDS": {"USDINR", "EURINR", "GBPINR", "JPYINR"}}
# Symbols charto served that Kite now lists under a new name: the old id stays
# reachable as an alias of the new one.
RENAMED = {"HEGAM": ["HEG"]}
GROUP_OF = {"nse_1m": "NSE_EQ", "nse500_1m": "NSE_EQ", "bse_1m": "BSE_EQ", "idx_nse": "NSE_IDX", "idx_bse": "BSE_IDX",
            "idx_mcx": "MCX_IDX", "fut_nfo": "NFO", "fut_bfo": "BFO", "fut_mcx": "MCX",
            "fut_nco": "NCO", "fut_cds": "CDS"}
PER_SYMBOL = {"idx_global", "idx_nseix"}
OHLCV = "(symbol TEXT NOT NULL, ts INTEGER NOT NULL, o REAL, h REAL, l REAL, c REAL, v INTEGER, " \
        "PRIMARY KEY (symbol, ts)) WITHOUT ROWID"
SCHEMA = f"""
CREATE TABLE IF NOT EXISTS bars {OHLCV};
CREATE TABLE IF NOT EXISTS bars_1d (symbol TEXT NOT NULL, ts INTEGER NOT NULL, o REAL, h REAL,
  l REAL, c REAL, v INTEGER, PRIMARY KEY (symbol, ts));
CREATE TABLE IF NOT EXISTS bars_15m {OHLCV};
CREATE TABLE IF NOT EXISTS oi_1m (symbol TEXT NOT NULL, ts INTEGER NOT NULL, oi INTEGER,
  PRIMARY KEY (symbol, ts)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS oi_1d (symbol TEXT NOT NULL, ts INTEGER NOT NULL, oi INTEGER,
  PRIMARY KEY (symbol, ts)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS rolls (root TEXT, rank INTEGER, contract TEXT, from_ts INTEGER,
  to_ts INTEGER, method TEXT, PRIMARY KEY (root, rank, from_ts));
CREATE TABLE IF NOT EXISTS adjustments (symbol TEXT, from_ts INTEGER, to_ts INTEGER, days INTEGER,
  factor REAL, volume INTEGER, source TEXT, PRIMARY KEY (symbol, from_ts));
CREATE TABLE IF NOT EXISTS rollup_meta (through_ts INTEGER);
CREATE TABLE IF NOT EXISTS instrument_master (
  id TEXT PRIMARY KEY, exchange TEXT, kind TEXT, tradingsymbol TEXT, name TEXT, root TEXT,
  expiry TEXT, tick_size REAL, lot_size INTEGER, decimals INTEGER, session_open INTEGER,
  session_close INTEGER, wrap INTEGER, instrument_token INTEGER, isin TEXT,
  first_ts INTEGER, last_ts INTEGER, store TEXT, store_symbol TEXT, aliases TEXT);
"""


def kite_dump(path: str | None) -> dict[int, dict]:
    raw = Path(path).read_text() if path else urllib.request.urlopen(
        "https://api.kite.trade/instruments", timeout=60).read().decode()
    return {int(r["instrument_token"]): r for r in csv.DictReader(io.StringIO(raw))}


def tick_decimals(tick: str | None) -> int | None:
    try:
        t = float(tick or 0)
    except ValueError:
        return None
    if t <= 0:
        return None
    s = f"{t:.6f}".rstrip("0").rstrip(".")
    return len(s.split(".")[1]) if "." in s else 0


def _dec(tick) -> int:
    d = tick_decimals(tick)
    return 2 if d is None else d          # 0 is a real answer: gold quotes in whole rupees


def measured_decimals(con, table, sym) -> int:
    """Decimals an index actually prints: the most any of its recent closes
    needs (US10YRYIELD quotes to 3), never fewer than 2."""
    need = 2
    for (c,) in con.execute(f"SELECT c FROM {table} WHERE symbol=? ORDER BY ts DESC LIMIT 400", (sym,)):
        if c is None:
            continue
        frac = f"{c:.6f}".rstrip("0").split(".")[1] if "." in f"{c:.6f}".rstrip("0") else ""
        need = max(need, min(len(frac), 4))
    return need


def session(sessions, store, sym):
    if store in PER_SYMBOL and sessions["symbols"].get(sym):
        m = sessions["symbols"][sym]
    else:
        m = sessions["groups"].get(GROUP_OF.get(store, "")) or {"open": 555, "close": 929, "wrap": False}
    close = (sessions["symbols"].get(f"{store}|{sym}") or m).get("close", m.get("close"))
    return m["open"], close, bool(m["wrap"])


def build_master(src: Path, dump: dict, sessions: dict) -> list[dict]:
    out, nse_eq = [], set()
    stores = sorted(p.stem for p in src.glob("*.db"))
    for st in stores:
        c = sqlite3.connect(f"file:{src / (st + '.db')}?mode=ro", uri=True)
        cols = {r[1] for r in c.execute("PRAGMA table_info(instruments)")}
        q = ("SELECT symbol, exchange, tradingsymbol, name, instrument_token, isin, first_ts, last_ts, "
             + ("kind, expiry" if "kind" in cols else "'equity', NULL") + " FROM instruments")
        ex_store = st.split("_")[1].upper() if st.startswith(("fut_", "idx_")) else st[:3].upper()
        for sym, ex, ts_, name, tok, isin, f, l, kind, exp in c.execute(q):
            has = c.execute("SELECT 1 FROM bars WHERE symbol=? LIMIT 1", (sym,)).fetchone() or \
                c.execute("SELECT 1 FROM bars_1d WHERE symbol=? LIMIT 1", (sym,)).fetchone()
            if not has:
                continue
            k = dump.get(int(tok or 0), {})
            o, cl, wrap = session(sessions, st, sym)
            row = {"store": st, "store_symbol": sym, "exchange": ex or ex_store,
                   "tradingsymbol": ts_ or sym, "instrument_token": tok, "isin": isin or None,
                   "first_ts": f, "last_ts": l, "expiry": exp or None,
                   "tick_size": float(k.get("tick_size") or 0) or None,
                   "lot_size": int(float(k.get("lot_size") or 0)) or None,
                   "session_open": o, "session_close": cl, "wrap": int(wrap), "aliases": []}
            if st in ("nse_1m", "nse500_1m"):
                row.update(id=sym, kind="equity", name=name or sym, root=None,
                           decimals=_dec(k.get("tick_size")))
                row["aliases"] = [f"NSE:{sym}", *RENAMED.get(sym, [])]
                nse_eq.add(sym)
            elif st == "bse_1m":
                row.update(id=f"BSE:{sym}", kind="equity", name=name or sym, root=None,
                           decimals=_dec(k.get("tick_size")))
            elif st.startswith("idx_"):
                row.update(id=sym, kind="index", name=name or sym, root=None,
                           decimals=measured_decimals(c, "bars" if f else "bars_1d", sym))
                row["aliases"] = [f"{row['exchange']}:{sym}", sym.replace(" ", "")]
            elif kind == "contract":
                exch = st.split("_")[1].upper()
                when = datetime.strptime(exp, "%Y-%m-%d").strftime("%b %Y") if exp else ""
                row.update(id=f"{exch}:{sym}", kind="future", name=f"{name} {when} futures".strip(),
                           root=name, decimals=_dec(k.get("tick_size")))
            elif kind == "cont":
                exch = st.split("_")[1].upper()
                root = name
                legacy = root in LEGACY_ROOTS.get(exch, ())
                for rank in (1, 2):
                    rid = root if (legacy and rank == 1) else f"{exch}:{root}{rank}!"
                    r2 = dict(row, id=rid, kind="continuous", root=root,
                              name=f"{root} continuous futures ({'front' if rank == 1 else 'next'} month)",
                              tradingsymbol=f"{root}{rank}!", rank=rank, exch=exch,
                              aliases=[f"{exch}:{root}{rank}!"] if rid != f"{exch}:{root}{rank}!" else [])
                    out.append(r2)
                continue
            out.append(row)
        c.close()
    # an index name that collides with an equity gets its exchange prefix
    for r in out:
        if r["kind"] == "index" and r["id"] in nse_eq:
            r["aliases"].append(r["id"])
            r["id"] = f"{r['exchange']}:{r['id']}"
    # continuous: decimals from the root's contracts on the same exchange
    dec = {(r["exchange"], r["root"]): r["decimals"] for r in out if r["kind"] == "future"}
    for r in out:
        if r["kind"] == "continuous":
            r["decimals"] = dec.get((r["exchange"], r["root"]), 2)
    from collections import Counter
    ids = Counter(r["id"] for r in out)
    dup = [i for i, n in ids.items() if n > 1]
    assert not dup, f"duplicate ids: {sorted(dup)[:10]}"
    # An alias that spells another instrument's id would silently redirect it
    # (NIFTYIT must never swallow a ticker called NIFTYIT): such aliases go.
    spelled = Counter(a for r in out for a in set(r["aliases"]))
    for r in out:
        r["aliases"] = [a for a in r["aliases"]
                        if a not in ids and a != r["id"] and spelled[a] == 1]
    return out


def copy_symbol(dst, alias, table, sid, sym, extra="", args=()):
    dst.execute(f"INSERT OR REPLACE INTO main.{table} SELECT ?, * FROM "
                f"(SELECT ts,{'o,h,l,c,v' if table.startswith('bars') else 'oi'} FROM {alias}.{table} "
                f"WHERE symbol=? {extra} ORDER BY ts)", (sid, sym, *args))


def land(src: Path, dst_path: Path, dump_path: str | None, only: set[str] | None) -> dict:
    sessions = json.loads((src / "sessions.json").read_text())
    dump = kite_dump(dump_path)
    master = build_master(src, dump, sessions)
    if only:
        roots = {(r["exchange"], r["root"]) for r in master if r["id"] in only and r["kind"] == "continuous"}
        master = [r for r in master if r["id"] in only or (r["kind"] == "future" and (r["exchange"], r["root"]) in roots)]
    dst = sqlite3.connect(dst_path, isolation_level=None)
    dst.execute("PRAGMA journal_mode=WAL")
    dst.execute("PRAGMA synchronous=OFF")
    dst.execute("PRAGMA cache_size=-1000000")
    dst.executescript(SCHEMA)
    stats = defaultdict(int)
    by_store = defaultdict(list)
    for r in master:
        by_store[r["store"]].append(r)
    t0 = time.time()
    contract_id = {}
    for st, rows in sorted(by_store.items()):
        dst.execute(f"ATTACH DATABASE ? AS s", (f"file:{src / (st + '.db')}?mode=ro",))
        tables = {t for (t,) in dst.execute("SELECT name FROM s.sqlite_master WHERE type='table'")}
        dst.execute("BEGIN")
        for r in sorted(rows, key=lambda x: x["id"]):
            if r["kind"] == "future":
                contract_id[(st, r["store_symbol"])] = r["id"]
            if r["kind"] == "continuous":
                continue
            for t in ("bars", "bars_1d", "bars_15m"):
                if t in tables:
                    copy_symbol(dst, "s", t, r["id"], r["store_symbol"])
            for t in ("oi_1m", "oi_1d"):
                if t in tables:
                    copy_symbol(dst, "s", t, r["id"], r["store_symbol"])
            if "adjustments" in tables:
                dst.execute("INSERT OR REPLACE INTO main.adjustments SELECT ?, from_ts, to_ts, days, factor, "
                            "volume, source FROM s.adjustments WHERE symbol=?", (r["id"], r["store_symbol"]))
            stats["instruments"] += 1
        # continuous: materialise from the roll table
        conts = [r for r in rows if r["kind"] == "continuous"]
        if conts and "rolls" in tables:
            for r in sorted(conts, key=lambda x: x["id"]):
                segs = dst.execute("SELECT contract, from_ts, to_ts FROM s.rolls WHERE root=? AND rank=? "
                                   "ORDER BY from_ts", (r["root"], r["rank"])).fetchall()
                first = segs[0][1] if segs else None
                if r["rank"] == 1 and "kite_1d" in tables:
                    # Kite's continuous daily before our first front segment
                    cut = first if first is not None else 2 ** 62
                    copy_symbol(dst, "s", "bars_1d", r["id"], r["store_symbol"], "AND ts<?", (cut,))
                    if "oi_1d" in tables:
                        copy_symbol(dst, "s", "oi_1d", r["id"], r["store_symbol"], "AND ts<?", (cut,))
                for con_sym, f, t in segs:
                    for tb in ("bars", "bars_15m", "bars_1d", "oi_1m", "oi_1d"):
                        if tb in tables:
                            copy_symbol(dst, "s", tb, r["id"], con_sym, "AND ts>=? AND ts<?", (f, t))
                    dst.execute("INSERT OR REPLACE INTO main.rolls VALUES (?,?,?,?,?,?)",
                                (r["id"], r["rank"], contract_id.get((st, con_sym), con_sym), f, t,
                                 "volume" if st in ("fut_mcx", "fut_nco") else "expiry"))
                stats["continuous"] += 1
        dst.execute("COMMIT")
        dst.execute("DETACH DATABASE s")
        print(f"  {st}: {len(rows)} instruments, {time.time() - t0:.0f}s", flush=True)
    dst.execute("BEGIN")
    dst.execute("DELETE FROM instrument_master")
    for r in master:
        dst.execute("INSERT INTO instrument_master VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (r["id"], r["exchange"], r["kind"], r["tradingsymbol"], r["name"], r["root"],
                     r["expiry"], r["tick_size"], r["lot_size"], r["decimals"], r["session_open"],
                     r["session_close"], r["wrap"], r["instrument_token"] if r["kind"] != "continuous" else None,
                     r["isin"], r["first_ts"], r["last_ts"], r["store"], r["store_symbol"],
                     json.dumps(sorted(set(r["aliases"])))))
    # The rollup is trusted through the start of the backfill's last day; that
    # day and everything after is read from minutes (see dataserver._intraday_rows).
    end = json.loads((src / "end.json").read_text())["end_ts"] if (src / "end.json").exists() else None
    if end:
        through = (end + IST_OFF) // 86400 * 86400 - IST_OFF
        dst.execute("DELETE FROM rollup_meta")
        dst.execute("INSERT INTO rollup_meta VALUES (?)", (through,))
    dst.execute("COMMIT")
    stats["seconds"] = round(time.time() - t0)
    return dict(stats)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--dst", required=True)
    ap.add_argument("--instruments", help="Kite instrument dump CSV (default: fetch it)")
    ap.add_argument("--only", nargs="*", help="land only these ids (a sample)")
    a = ap.parse_args(argv)
    print(json.dumps(land(Path(a.src), Path(a.dst), a.instruments, set(a.only) if a.only else None)))


if __name__ == "__main__":
    sys.exit(main())
