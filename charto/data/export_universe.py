#!/usr/bin/env python3
"""Export Pivot's securities universe to a local cache for the demo seeder.

WHY A CACHE. The universe lives in Pivot's Azure Postgres (Central India), so
every read is RTT-bound and needs the gitignored .env. The seeder should not
depend on either: it is run to rebuild demo data, often, and a 5,000-row join
across two databases is not something to pay for each time. This writes
`demo_universe.json` once; seed_demo.py reads that.

WHERE THE ROWS COME FROM.

  identity  public.company_identity in pivot_db — 5,206 rows, ISIN-keyed, the
            repo's identity source. NOT mc.companies, whose name is truncated
            at 15 characters and whose nse_symbol column holds BSE codes.

  sector    enrich.company_profile in pivot_enrich, joined BY NAME.
            Deliberately not by ticker: that column is known-corrupt in enrich
            (655 of 670 duplicate ticker groups are different companies), so
            using it as a key silently attaches one company's sector to
            another's symbol. Name matching is fuzzy and misses some rows;
            missing is the correct failure here, and unmatched rows keep
            'Unclassified' rather than borrowing a neighbour's sector.

    python3 export_universe.py            # refresh the cache
    python3 export_universe.py --stats    # just report what is cached
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
CACHE = HERE / "demo_universe.json"
PIVOT_ENV = HERE.parents[1] / "pivot" / ".env"

# Corporate-form noise that differs between the two sources for the same
# company ("Reliance Industries Limited" vs "Reliance Industries Ltd").
_SUFFIX = re.compile(
    r"\b(limited|ltd|private|pvt|public|company|co|corporation|corp|inc|"
    r"industries|enterprises|holdings|group|india|the)\b", re.I)


def norm(name: str) -> str:
    """A join key that survives the difference between the two name spellings."""
    s = (name or "").lower()
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    s = _SUFFIX.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip()


def env(path: Path) -> dict:
    out = {}
    for line in path.read_text().splitlines():
        m = re.match(r"^([A-Z_]+)=(.*)$", line.strip())
        if m:
            out[m.group(1)] = m.group(2).strip("\"'")
    return out


def fetch() -> list[dict]:
    import psycopg2                                    # pivot's venv supplies it

    cfg = env(PIVOT_ENV)
    pg = lambda k: cfg[k].replace("postgresql+psycopg2://", "postgresql://")

    t0 = time.perf_counter()
    with psycopg2.connect(pg("DATABASE_URL"), connect_timeout=20) as c, c.cursor() as cur:
        cur.execute("""
            SELECT verified_symbol, verified_name, verified_exchange, isin
            FROM   public.company_identity
            WHERE  verified_symbol IS NOT NULL AND verified_symbol <> ''
              AND  verified_name   IS NOT NULL AND verified_name   <> ''
        """)
        ident = cur.fetchall()
    print(f"  company_identity   {len(ident):>6,} rows  ({time.perf_counter()-t0:.1f}s)")

    t1 = time.perf_counter()
    with psycopg2.connect(pg("ENRICH_DSN"), connect_timeout=20) as c, c.cursor() as cur:
        cur.execute("""
            SELECT company_name, long_name, sector, industry, market_cap
            FROM   enrich.company_profile
            WHERE  sector IS NOT NULL AND sector <> ''
        """)
        prof = cur.fetchall()
    print(f"  enrich profiles    {len(prof):>6,} rows  ({time.perf_counter()-t1:.1f}s)")

    # Name -> (sector, industry, mcap). Both spellings indexed; first wins, so a
    # later duplicate cannot overwrite a match that already looked good.
    by_name: dict[str, tuple] = {}
    for cname, lname, sector, industry, mcap in prof:
        for n in (norm(cname), norm(lname)):
            if n and n not in by_name:
                by_name[n] = (sector, industry, mcap)

    rows, hit = [], 0
    seen = set()
    for sym, name, exch, isin in ident:
        if sym in seen:                                 # 5,206 rows, 5,019 symbols
            continue
        seen.add(sym)
        s = by_name.get(norm(name))
        if s:
            hit += 1
        rows.append({
            "symbol": sym, "name": name, "exchange": exch or "NSE", "isin": isin,
            "sector": (s[0] if s else "Unclassified"),
            "industry": (s[1] if s else "Unclassified"),
            "mcap": float(s[2]) if s and s[2] else 0.0,
        })
    print(f"  sector matched     {hit:>6,} / {len(rows):,} "
          f"({100.0*hit/max(len(rows),1):.1f}%)")
    return rows


def report(rows: list[dict]) -> None:
    ex = Counter(r["exchange"] for r in rows)
    se = Counter(r["sector"] for r in rows)
    print(f"\n  {len(rows):,} securities")
    print("  exchanges:", ", ".join(f"{k} {v:,}" for k, v in ex.most_common(6)))
    print("  sectors:  ", ", ".join(f"{k} {v:,}" for k, v in se.most_common(8)))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stats", action="store_true", help="report the cache, do not refresh")
    a = ap.parse_args()

    if a.stats:
        if not CACHE.exists():
            sys.exit("no cache — run without --stats first")
        report(json.loads(CACHE.read_text())["securities"])
        return

    if not PIVOT_ENV.exists():
        sys.exit(f"pivot .env not found at {PIVOT_ENV}")
    print("reading Pivot's universe (Azure PG, Central India)…")
    rows = fetch()
    CACHE.write_text(json.dumps(
        {"source": "pivot_db.public.company_identity + pivot_enrich.enrich.company_profile",
         "exported_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
         "count": len(rows), "securities": rows}, separators=(",", ":")))
    report(rows)
    print(f"\n  -> {CACHE.name} ({CACHE.stat().st_size/1024:,.0f} KB)")


if __name__ == "__main__":
    main()
