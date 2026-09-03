"""Sync `quarterly_metrics` for charto's universe.

The Quarterly Results tab on the company page was never a tab this page could
draw: `/stock/…/quarters` is answered by `_api_unavailable`, so it came back
empty for every symbol and the tab hid itself on every symbol. It was not a
per-company coverage problem — it was that charto had no quarterly store at
all, and the panel's "coverage decides the tab" rule then read the absence as
"this company files nothing".

The numbers exist in Pivot's `quarterly_metrics`, computed once and stored as
columns — margins, YoY, QoQ and TTM among them. Nothing is recomputed here for
the same reason `/quarters` computes nothing: a second derivation is a second,
silently different set of numbers.

Identity is resolved exactly the way Pivot's route resolves it — ISIN first,
`sc_id` only if ISIN finds nothing, never OR'd. `mc_sc_id` is an ALIAS and
collides across companies, so an OR here would quietly file one company's
quarters under another's symbol.

  pg.quarterly_metrics  ->  charto.quarters   (one row per symbol/basis/period)

Run:  pivot/.venv/bin/python charto/data/sync_quarters.py [SYMBOL ...]
"""
from __future__ import annotations

import json
import sqlite3
import sys
import time
from decimal import Decimal
from pathlib import Path

HERE = Path(__file__).parent
PIVOT = HERE.parents[1] / "pivot"
sys.path.insert(0, str(PIVOT))

# Every column the page reads, and nothing it does not: the tab draws seven
# rows and the statements page owns the full quarterly P&L.
COLS = (
    "period_end", "period_label", "basis", "revenue", "total_income",
    "other_income", "ebitda", "ebit", "depreciation", "interest", "pbt", "tax",
    "net_profit", "eps_basic", "eps_diluted", "operating_margin_pct",
    "ebitda_margin_pct", "net_margin_pct", "pbt_margin_pct", "tax_rate_pct",
    "revenue_yoy_pct", "net_profit_yoy_pct", "revenue_qoq_pct",
    "net_profit_qoq_pct", "rev_ttm", "np_ttm", "eps_ttm",
    "gross_npa_pct", "net_npa_pct", "roa_pct",
)

# 24 quarters — six years, which is as far back as the page's own longest span
# reaches. More would be storing rows nothing asks for.
LIMIT = 24

DDL = """
CREATE TABLE IF NOT EXISTS quarters (
  symbol     TEXT,
  basis      TEXT,
  period_end TEXT,
  payload    TEXT,
  synced_at  INTEGER,
  PRIMARY KEY (symbol, basis, period_end)
);
CREATE INDEX IF NOT EXISTS quarters_sym ON quarters(symbol, basis, period_end DESC);
"""


def _plain(v):
    """Decimal and date are not JSON. Floats and ISO strings are."""
    if isinstance(v, Decimal):
        return float(v)
    if hasattr(v, "isoformat"):
        return v.isoformat()
    return v


def main() -> None:
    from sqlalchemy import text

    from backend.database import SessionLocal

    syms = [s.upper() for s in sys.argv[1:]] or json.loads(
        (HERE / "symbols.json").read_text())

    db = sqlite3.connect(HERE / "charto_bars.db")
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript(DDL)

    t0 = time.time()
    cols = ", ".join(COLS)

    with SessionLocal() as pg:
        # One read for the whole universe rather than 500 round trips to Azure
        # Central India, where the cost of this job is entirely RTT.
        ident = {
            s: (isin, sc)
            for s, isin, sc in pg.execute(text("""
                SELECT DISTINCT ON (verified_symbol)
                       verified_symbol, isin, mc_sc_id
                  FROM company_identity
                 WHERE verified_symbol = ANY(:s)
              ORDER BY verified_symbol, mc_is_primary DESC,
                       mc_metric_count DESC NULLS LAST, mc_sc_id"""),
                {"s": syms}).fetchall()
        }

        done = miss = rows_in = 0
        for sym in syms:
            isin, sc_id = ident.get(sym, (None, None))
            rows = []
            if isin:
                rows = pg.execute(text(f"""
                    SELECT {cols} FROM quarterly_metrics
                     WHERE isin = :i
                  ORDER BY period_end DESC LIMIT :n"""),
                    {"i": isin, "n": LIMIT * 2}).mappings().all()
            if not rows and sc_id:
                rows = pg.execute(text(f"""
                    SELECT {cols} FROM quarterly_metrics
                     WHERE sc_id = :s
                  ORDER BY period_end DESC LIMIT :n"""),
                    {"s": sc_id, "n": LIMIT * 2}).mappings().all()
            if not rows:
                miss += 1
                continue

            now = int(time.time())
            for r in rows:
                d = {k: _plain(v) for k, v in dict(r).items()}
                db.execute("INSERT OR REPLACE INTO quarters VALUES (?,?,?,?,?)",
                           (sym, d.get("basis") or "consolidated",
                            d.get("period_end") or "", json.dumps(d), now))
                rows_in += 1
            done += 1
            if (done + miss) % 50 == 0:
                db.commit()
                print(f"  {done + miss}/{len(syms)} · {time.time() - t0:.0f}s",
                      flush=True)

    db.commit()
    have = db.execute("SELECT COUNT(DISTINCT symbol) FROM quarters").fetchone()[0]
    db.close()
    print(f"quarters: {done} synced · {miss} with none filed · "
          f"{rows_in} rows · {have} symbols · {time.time() - t0:.0f}s")
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
