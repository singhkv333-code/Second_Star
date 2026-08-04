"""Sync the statements behind Charto's company page into the local store.

The company page is Pivot's stock page, so its Key Metrics strip and its
Financial Performance panel expect Pivot's `/api/financials/{symbol}` and
`/api/financials/{symbol}/balance_sheet` payloads. Rather than re-derive them
— which would invent a second, subtly different set of numbers for the same
company — this runs **Pivot's own code** (`backend.market.financials_db`)
against the same Moneycontrol database and stores the assembled payloads.

  mc.statement_lines  ->  fdb.get_company_fundamentals_bulk   (latest + history)
                      ->  fdb.get_balance_sheet_statement     (the full grid)

Two rows per symbol per basis; the dataserver serves them verbatim. Fields
Moneycontrol doesn't publish for a company stay null and the page renders its
own em-dash — never a filled-in guess. yfinance is NOT consulted here: Pivot
reaches for it live, per request, and 500 live .info calls is not a sync.

Run:  pivot/.venv/bin/python charto/data/sync_financials.py [SYMBOL ...]
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

DDL = """
CREATE TABLE IF NOT EXISTS financials (
  symbol   TEXT PRIMARY KEY,
  payload  TEXT,
  synced_at INTEGER
);
CREATE TABLE IF NOT EXISTS balance_sheet (
  symbol   TEXT,
  basis    TEXT,
  payload  TEXT,
  synced_at INTEGER,
  PRIMARY KEY (symbol, basis)
);
"""

# Same list, same order, same limit the stock page's history tables read.
HISTORY_FIELDS = ("revenue", "operating_profit", "net_profit", "eps_basic",
                  "interest_expense", "cash_from_ops", "total_equity",
                  "reserves", "total_debt", "book_value_per_share")
HISTORY_LIMIT = 6


def _fv(v) -> dict | None:
    if v is None or v.value_numeric is None:
        return None
    return {"value": float(v.value_numeric),
            "period_end": v.period_end.isoformat() if v.period_end else None,
            "period_label": v.period_label, "line_item": v.line_item,
            "unit": v.unit, "basis": v.basis, "source": "moneycontrol"}


def build(sym: str) -> tuple[str, dict | None, dict]:
    """(symbol, financials payload, {basis: balance-sheet payload})."""
    from backend.market import financials_db as fdb

    company = fdb.get_company(sym)
    if company is None:
        return sym, None, {}

    latest_raw, hist_raw = fdb.get_company_fundamentals_bulk(
        company.sc_id, fields=fdb.list_supported_fields(),
        history_fields=HISTORY_FIELDS, history_limit=HISTORY_LIMIT)
    latest = {f: _fv(v) for f, v in latest_raw.items()}
    history = {f: [x for x in (_fv(r) for r in hist_raw.get(f, [])) if x]
               for f in HISTORY_FIELDS}
    # the page reads period_label off history rows, not line_item
    for rows in history.values():
        for r in rows:
            r.pop("line_item", None)

    fin = {"available": bool(any(v for v in latest.values())
                             or any(history.values())),
           "company": company.to_dict(), "latest": latest, "history": history,
           "profile": None,   # the dataserver fills this from company_profile
           "source": "moneycontrol_via_financials_db"}

    sheets: dict[str, dict] = {}
    for basis in ("consolidated", "standalone"):
        st = fdb.get_balance_sheet_statement(sym, basis=basis) or {}
        sheets[basis] = {"available": bool(st.get("rows")),
                         "company": company.to_dict(),
                         "basis": st.get("basis", basis), "unit": st.get("unit"),
                         "periods": st.get("periods", []),
                         "rows": st.get("rows", []), "source": "moneycontrol"}
    return sym, fin, sheets


def main() -> None:
    syms = [s.upper() for s in sys.argv[1:]] or json.loads(
        (HERE / "symbols.json").read_text())

    db = sqlite3.connect(HERE / "charto_bars.db")
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript(DDL)

    t0 = time.time()
    done = miss = 0
    # Azure round-trips dominate; a handful of workers turns ~15 min into ~2.
    with ThreadPoolExecutor(max_workers=6) as pool:
        for sym, fin, sheets in pool.map(build, syms):
            now = int(time.time())
            if fin is None:
                miss += 1
            else:
                db.execute("INSERT OR REPLACE INTO financials VALUES (?,?,?)",
                           (sym, json.dumps(fin), now))
                for basis, sheet in sheets.items():
                    db.execute(
                        "INSERT OR REPLACE INTO balance_sheet VALUES (?,?,?,?)",
                        (sym, basis, json.dumps(sheet), now))
                done += 1
            if (done + miss) % 25 == 0:
                db.commit()
                print(f"  {done + miss}/{len(syms)} · {time.time() - t0:.0f}s",
                      flush=True)
    db.commit()

    # coverage, reported per field so a thin sync can't pass as a full one
    n_avail = db.execute(
        "SELECT COUNT(*) FROM financials WHERE json_extract(payload,'$.available')"
    ).fetchone()[0]
    fields = ("roe", "roce", "roa", "debt_to_equity", "current_ratio",
              "ev_to_ebitda", "price_to_book", "net_profit_margin")
    cov = {f: db.execute(
        f"SELECT COUNT(*) FROM financials "
        f"WHERE json_extract(payload,'$.latest.{f}.value') IS NOT NULL"
    ).fetchone()[0] for f in fields}
    bs = db.execute("SELECT COUNT(*) FROM balance_sheet "
                    "WHERE json_extract(payload,'$.available')").fetchone()[0]
    db.close()

    print(f"financials: {done} synced · {miss} with no Moneycontrol row · "
          f"{n_avail} with data · {time.time() - t0:.0f}s")
    print("latest coverage: " + " · ".join(f"{k} {v}" for k, v in cov.items()))
    print(f"balance sheets with rows: {bs}")


if __name__ == "__main__":
    main()
