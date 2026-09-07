"""Move the company-page tables without moving the 29 GB of bars.

`charto_bars.db` holds two unrelated things. The bars are ~99% of the bytes
and change by append every day; the company-page tables — classification,
profiles, statements, the Tijori mix, the pattern ledger — are 74 MB and
change only when a sync runs. Shipping a sync currently means shipping the
whole file, which is why the VM has been running without `statement` and
`revenue_mix` at all.

  export  charto_bars.db      -> company_tables.db   (74 MB)
  import  company_tables.db   -> charto_bars.db      (replaces those tables)
  slim    charto_bars.db      -> charto_slim.db      (~300 MB, dev store)

`export`/`import` never read, write or copy a bar. `slim` copies a few, on
purpose: it builds a local development store out of the company tables, the
whole daily series, and minute bars for a handful of symbols — enough for the
company page and the screener to work fully and for the chart to work on
those symbols, at 2% of the size. The 29 GB of minute history stays on the
VM's data disk and in blob, which is where it was already durable.

Run:  python3 charto/data/company_tables.py export [OUT.db]
      python3 charto/data/company_tables.py import IN.db [--into DB]
      python3 charto/data/company_tables.py slim [OUT.db] [--symbols A,B,C]
"""
from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).parent
DB = HERE / "charto_bars.db"

# Everything the company page and the screener read, and nothing that holds a
# bar. `bars_1d` is deliberately absent: it is 1.2M rows of price and belongs
# with the bars, not with the company metadata.
TABLES = (
    "classification", "company_profile", "financials", "balance_sheet",
    "statement", "revenue_mix", "pattern_stats", "pattern_stats_meta",
    "instrument_logo", "results", "benchmark", "vp_screen", "screen_meta",
    "deals", "sync_state",
)


def _tables_in(con: sqlite3.Connection, schema: str = "main") -> dict[str, str]:
    return {r[0]: r[1] for r in con.execute(
        f"SELECT name, sql FROM {schema}.sqlite_master WHERE type='table'")}


def export(src: Path, out: Path) -> None:
    if out.exists():
        out.unlink()
    # Read-only so an export can never be the thing that corrupts the store,
    # and so it is safe to run against a live dataserver.
    con = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    con.execute("ATTACH ? AS out", (str(out),))
    have = _tables_in(con)
    wrote, missing = [], []
    for t in TABLES:
        ddl = have.get(t)
        if not ddl:
            missing.append(t)
            continue
        con.execute(ddl.replace(f'"{t}"', f'out."{t}"', 1)
                    if f'"{t}"' in ddl else ddl.replace(t, f"out.{t}", 1))
        con.execute(f"INSERT INTO out.{t} SELECT * FROM main.{t}")
        n = con.execute(f"SELECT COUNT(*) FROM out.{t}").fetchone()[0]
        wrote.append((t, n))
    con.commit()
    con.execute("DETACH out")
    con.close()
    for t, n in wrote:
        print(f"  {t:20} {n:>10,}")
    if missing:
        print(f"  (absent in source: {', '.join(missing)})")
    print(f"\n{out} · {out.stat().st_size / 1e6:.1f} MB")


def import_(src: Path, into: Path) -> None:
    if not src.exists():
        raise SystemExit(f"no such file: {src}")
    if not into.exists():
        raise SystemExit(f"no target store at {into}")
    con = sqlite3.connect(into)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("ATTACH ? AS src", (str(src),))
    incoming = _tables_in(con, "src")
    # One transaction for the whole swap: a reader either sees the old set of
    # tables or the new one, never a half-replaced store mid-page-load.
    con.execute("BEGIN IMMEDIATE")
    done = []
    for t, ddl in incoming.items():
        con.execute(f"DROP TABLE IF EXISTS main.{t}")
        con.execute(ddl)
        con.execute(f"INSERT INTO main.{t} SELECT * FROM src.{t}")
        done.append((t, con.execute(f"SELECT COUNT(*) FROM main.{t}").fetchone()[0]))
    con.commit()
    con.execute("DETACH src")
    con.close()
    for t, n in done:
        print(f"  {t:20} {n:>10,}")
    print(f"\nreplaced {len(done)} tables in {into}")


# Enough symbols to exercise the chart across the shapes that behave
# differently: a large cap, a bank, an IT name, an index and a crypto pair
# (which trades 1440 minutes a day with no gap, and so breaks anything that
# assumes a session).
SLIM_SYMBOLS = ("RELIANCE", "HDFCBANK", "TCS", "NIFTY 50", "BTCUSDT")


def slim(src: Path, out: Path, symbols: tuple[str, ...]) -> None:
    if out.exists():
        out.unlink()
    con = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    con.execute("ATTACH ? AS out", (str(out),))
    have = _tables_in(con)

    for t in TABLES:
        ddl = have.get(t)
        if not ddl:
            continue
        con.execute(ddl.replace(f'"{t}"', f'out."{t}"', 1)
                    if f'"{t}"' in ddl else ddl.replace(t, f"out.{t}", 1))
        con.execute(f"INSERT INTO out.{t} SELECT * FROM main.{t}")

    # The whole daily series: 1.2M rows for 67 MB, and it is what the company
    # page, the screener and every pattern sweep read. Cutting it to the slim
    # symbols would save 60 MB and break the screener.
    con.execute(have["bars_1d"].replace("bars_1d", "out.bars_1d", 1))
    con.execute("INSERT INTO out.bars_1d SELECT * FROM main.bars_1d")

    con.execute(have["bars"].replace("bars", "out.bars", 1))
    marks = ",".join("?" * len(symbols))
    con.execute(f"INSERT INTO out.bars SELECT * FROM main.bars "
                f"WHERE symbol IN ({marks})", symbols)
    con.execute("CREATE INDEX IF NOT EXISTS out.ix_bars_sym_ts ON bars(symbol, ts)")
    con.commit()

    kept = con.execute("SELECT COUNT(*) FROM out.bars").fetchone()[0]
    day = con.execute("SELECT COUNT(*) FROM out.bars_1d").fetchone()[0]
    con.execute("DETACH out")
    con.close()
    print(f"  minute bars for {', '.join(symbols)}: {kept:,}")
    print(f"  daily bars (all symbols): {day:,}")
    print(f"\n{out} · {out.stat().st_size / 1e9:.2f} GB")


def main() -> None:
    args = sys.argv[1:]
    if not args or args[0] not in ("export", "import", "slim"):
        raise SystemExit(__doc__)
    if args[0] == "slim":
        syms = SLIM_SYMBOLS
        if "--symbols" in args:
            syms = tuple(s.strip().upper()
                         for s in args[args.index("--symbols") + 1].split(","))
        pos = [a for a in args[1:] if not a.startswith("--")]
        # the value of --symbols is positional-looking; drop it
        if "--symbols" in args:
            pos = [a for a in pos if a != args[args.index("--symbols") + 1]]
        out = Path(pos[0]) if pos else HERE / "charto_slim.db"
        slim(DB, out, syms)
    elif args[0] == "export":
        out = Path(args[1]) if len(args) > 1 else HERE / "company_tables.db"
        export(DB, out)
    else:
        if len(args) < 2:
            raise SystemExit("import needs a source file")
        into = Path(args[args.index("--into") + 1]) if "--into" in args else DB
        import_(Path(args[1]), into)


if __name__ == "__main__":
    main()
