"""Sync Tijori's revenue-mix breakdowns into Charto's local store.

The company page's Segment mix panel is Pivot's, so it expects Pivot's
`/api/stock/{symbol}/mix` payload. Rather than re-derive the shares — which
would put a second, subtly different set of numbers against the same company —
this reads the same `enrich.tijori_enrichment` rows Pivot reads and stores the
assembled payload, one row per symbol.

  enrich.tijori_enrichment.revenue_mix  ->  charto.revenue_mix.payload

`revenue_mix` is a LIST of breakdowns, not one. Reliance carries seven:
product-wise, location-wise, operating-profit-wise, capex, assets, plus two
nested inside Organized Retail. Reading only the first — the obvious mistake —
throws away most of what makes the section worth drawing, so every block is
kept and the page offers the choice.

Two key names to know, both non-obvious: a block's title is `breakdown`, and a
segment's name is `fieldname`.

Run:  pivot/.venv/bin/python charto/data/sync_tijori_mix.py [SYMBOL ...]
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
import time
from pathlib import Path

HERE = Path(__file__).parent
DB = HERE / "charto_bars.db"

DDL = """
CREATE TABLE IF NOT EXISTS revenue_mix (
  symbol    TEXT PRIMARY KEY,
  payload   TEXT,
  synced_at INTEGER
);
"""

# The source carries values like 1.42e-14 — floating-point residue from a
# share that is actually zero — which passes a bare `> 0` test and then
# renders as "Others 0.0%", a segment that does not exist. A twentieth of a
# percent is below anything a reader can act on anyway.
FLOOR = 0.05


def _dsn() -> str:
    dsn = os.environ.get("ENRICH_DSN")
    if dsn:
        return dsn
    env = (HERE.parents[1] / "pivot" / ".env").read_text()
    m = re.search(r"^ENRICH_DSN=(.*)$", env, re.M)
    if not m:
        raise SystemExit("no ENRICH_DSN in pivot/.env")
    return m.group(1).strip()


def build(row) -> dict | None:
    """One enrichment row -> the mix payload, or None when nothing survives."""
    charts = []
    for block in (row["revenue_mix"] or []):
        if not isinstance(block, dict):
            continue
        current = [{"name": n, "pct": round(float(p), 2)}
                   for n, p in (block.get("current") or [])
                   if p is not None and float(p) >= FLOOR]
        series = []
        for seg in (block.get("segments") or []):
            pts = [{"t": int(t), "pct": round(float(v), 2)}
                   for t, v in (seg.get("series") or []) if v is not None]
            # A band that is zero at every point is an empty legend entry and
            # an invisible layer — drop the series, not just its label.
            if pts and any(p["pct"] >= FLOOR for p in pts):
                series.append({"name": seg.get("fieldname") or "—", "points": pts})
        if current or series:
            charts.append({"id": block.get("chart_id"),
                           "title": block.get("breakdown") or "Revenue mix",
                           "current": current, "series": series})
    if not charts:
        return None
    shares = [{"name": m.get("name"),
               "points": [{"t": int(t), "pct": float(v)}
                          for t, v in (m.get("series") or []) if v is not None]}
              for m in (row["market_share"] or []) if isinstance(m, dict)]
    return {"available": True, "source_name": row["tijori_name"],
            "charts": charts, "market_share": [s for s in shares if s["points"]]}


def main() -> None:
    import psycopg2
    import psycopg2.extras

    db = sqlite3.connect(DB)
    db.executescript(DDL)

    want = [s.upper() for s in sys.argv[1:]]
    # sc_id is the join key, and it lives in the financials payload charto
    # already stores — the same id Pivot resolved, so a mix cannot land on a
    # different company than the statements did.
    sc: dict[str, str] = {}
    for sym, payload in db.execute("SELECT symbol, payload FROM financials"):
        if want and sym not in want:
            continue
        try:
            cid = (json.loads(payload).get("company") or {}).get("sc_id")
        except (ValueError, AttributeError):
            continue
        if cid:
            sc[sym] = cid
    if not sc:
        raise SystemExit("no symbols with an sc_id — run sync_financials.py first")

    con = psycopg2.connect(_dsn())
    cur = con.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""SELECT sc_id, tijori_name, revenue_mix, market_share
                     FROM enrich.tijori_enrichment
                    WHERE has_revenue_mix AND sc_id = ANY(%s)""",
                (sorted(set(sc.values())),))
    rows = {r["sc_id"]: r for r in cur.fetchall()}

    now, wrote, empty = int(time.time()), 0, 0
    for sym, cid in sorted(sc.items()):
        row = rows.get(cid)
        payload = build(row) if row else None
        if payload is None:
            empty += 1
            continue
        db.execute("INSERT OR REPLACE INTO revenue_mix VALUES (?,?,?)",
                   (sym, json.dumps(payload), now))
        wrote += 1
    db.commit()
    print(f"revenue_mix: {wrote} symbols stored, {empty} with no Tijori mix "
          f"(of {len(sc)} asked)")


if __name__ == "__main__":
    main()
