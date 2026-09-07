"""Sync the full statement grids the solvency scores are computed from.

`sync_financials.py` already stores the balance sheet, because the Financial
Performance panel draws it. The four scores need three more grids — P&L, cash
flow and the ratio sheet — and they need them the same way: as whole
per-period columns.

That "per period" is the entire point. The `latest` snapshot the page header
uses picks each field independently, so a company with a patchy filing history
hands back Mar-25 revenue beside Mar-21 equity — measured on TATASTEEL, 23
metrics from Mar 21 sitting next to 6 from Mar 25. Fine for a stat tile,
fatal for a ratio: an Altman Z built from those two columns describes no date
at all and still looks perfectly plausible. So the grids are stored whole and
the scorer pins every input to one column.

  mc.statement_lines  ->  fdb.get_statement  ->  charto.statement.payload

Run:  pivot/.venv/bin/python charto/data/sync_statements.py [SYMBOL ...]
"""
from __future__ import annotations

import json
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).parent
PIVOT = HERE.parents[1] / "pivot"
sys.path.insert(0, str(PIVOT))

# The balance sheet is already synced by sync_financials.py into its own
# table; re-storing it here would give the page two copies to disagree with.
STATEMENTS = ("profit_loss", "cash_flow", "ratios")
BASES = ("consolidated", "standalone")
YEARS = 12

DDL = """
CREATE TABLE IF NOT EXISTS statement (
  symbol    TEXT,
  statement TEXT,
  basis     TEXT,
  payload   TEXT,
  synced_at INTEGER,
  PRIMARY KEY (symbol, statement, basis)
);
"""


def build(sym: str) -> tuple[str, dict]:
    from backend.market import financials_db as fdb

    out: dict[tuple[str, str], dict] = {}
    for statement in STATEMENTS:
        for basis in BASES:
            st = fdb.get_statement(sym, statement=statement, basis=basis,
                                   years=YEARS) or {}
            if not st.get("rows"):
                continue
            out[(statement, basis)] = {
                "available": True, "statement": statement,
                "basis": st.get("basis", basis), "unit": st.get("unit"),
                "periods": st.get("periods", []), "rows": st.get("rows", []),
                "source": "moneycontrol"}
    return sym, out


def main() -> None:
    syms = [s.upper() for s in sys.argv[1:]] or json.loads(
        (HERE / "symbols.json").read_text())

    db = sqlite3.connect(HERE / "charto_bars.db")
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript(DDL)

    t0 = time.time()
    done = miss = 0
    with ThreadPoolExecutor(max_workers=6) as pool:
        for sym, grids in pool.map(build, syms):
            now = int(time.time())
            if not grids:
                miss += 1
            else:
                for (statement, basis), payload in grids.items():
                    db.execute(
                        "INSERT OR REPLACE INTO statement VALUES (?,?,?,?,?)",
                        (sym, statement, basis, json.dumps(payload), now))
                done += 1
            if (done + miss) % 25 == 0:
                db.commit()
                print(f"  {done + miss}/{len(syms)} · {time.time() - t0:.0f}s",
                      flush=True)
    db.commit()

    for statement in STATEMENTS:
        n = db.execute("SELECT COUNT(DISTINCT symbol) FROM statement "
                       "WHERE statement=?", (statement,)).fetchone()[0]
        print(f"  {statement:14} {n} symbols")
    db.close()
    print(f"statements: {done} synced · {miss} with no grid · "
          f"{time.time() - t0:.0f}s")
    # A table this script CREATES is invisible to a dataserver that was
    # already running: its per-thread connections were opened before the
    # table existed, the read raises "no such table", and the route reports
    # `available: false` — which looks exactly like a company that files
    # nothing. Restarting is the whole fix, and it is not obvious from the
    # outside, so it is said here.
    print("restart the dataserver (./restart.sh) — a new table is invisible "
          "to its open connections")


if __name__ == "__main__":
    main()
