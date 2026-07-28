"""Sync industry classification for the chart universe into the local store.

Source: mc.companies in the Azure financials DB. The join key is
nse_symbol — the REPAIRED field (the enrich DB's ticker column is
corrupted: 655/670 dup-ticker groups are different companies, so ticker
is never used as an identity key). sector and market_cap are NULL in mc
by design; industry_slug is fully populated and is the classification.

A few symbols carry no nse_symbol in mc; where the company is
unambiguous the classification is corrected by NAME below. LTM stays
unclassified rather than guessed.

Run:  pivot/.venv/bin/python charto/data/sync_classification.py
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

HERE = Path(__file__).parent

# name-resolved corrections for symbols mc carries without an nse_symbol
_FIXUPS = {
    "ACC": ("ACC", "cementmajor"),
    "ICICIPRULI": ("ICICI Prudential Life", "insurancelife"),
    "LTFOODS": ("LT Foods", "foodprocessing"),
    "PAYTM": ("One97 Communications", "financepayments"),
    "RKFORGE": ("Ramkrishna Forgings", "castingsforgings"),
    "SUMICHEM": ("Sumitomo Chemical India", "pesticidesagrochemicals"),
    "TTML": ("Tata Teleservices (Maharashtra)", "telecomservices"),
    "UPL": ("UPL", "pesticidesagrochemicals"),
}

DDL = """
CREATE TABLE IF NOT EXISTS classification (
  symbol   TEXT PRIMARY KEY,
  name     TEXT,
  industry TEXT
);
"""


def main() -> None:
    import psycopg2
    from dotenv import dotenv_values

    env = dotenv_values(HERE.parents[1] / "pivot" / ".env")
    syms = json.loads((HERE / "symbols.json").read_text())

    pg = psycopg2.connect(env["FINANCIALS_DSN"])
    cur = pg.cursor()
    cur.execute(
        "SELECT nse_symbol, company_name, industry_slug FROM mc.companies "
        "WHERE nse_symbol = ANY(%s) AND is_active IS NOT FALSE", (syms,))
    rows = {r[0]: (r[1], r[2]) for r in cur.fetchall()}
    pg.close()
    for s, v in _FIXUPS.items():
        rows.setdefault(s, v)

    db = sqlite3.connect(HERE / "charto_bars.db")
    db.executescript(DDL)
    db.execute("DELETE FROM classification")
    db.executemany(
        "INSERT INTO classification VALUES (?,?,?)",
        [(s, rows[s][0], rows[s][1]) for s in syms if s in rows])
    db.commit()
    n, ind = db.execute(
        "SELECT COUNT(*), COUNT(DISTINCT industry) FROM classification").fetchone()
    missing = sorted(set(syms) - set(rows))
    db.close()
    print(f"classification: {n} symbols, {ind} industries; "
          f"unclassified: {missing or 'none'}")


if __name__ == "__main__":
    main()
