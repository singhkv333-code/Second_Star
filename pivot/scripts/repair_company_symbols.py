#!/usr/bin/env python3
"""Repair mc.companies.nse_symbol from trustworthy enrichment, killing the
sc_id-as-symbol dead-ends and the mis-assigned scraper tickers.

WHY
---
`mc.companies.ticker` (the Moneycontrol scraper column) is polluted: distinct
shell rows carry a *stolen* trading symbol (e.g. `Jay Electric` and
`Bharat Hotels` both have ticker='BHEL' while the real BHEL, sc_id='BHE', has
NO ticker at all). The FE navigates to COALESCE(nse_symbol, ticker, sc_id), so
the real company dead-ends at `/stock/BHE` (`BHE.NSE` → no quote) and the
symbol `BHEL` resolves to a shell. The correct symbol already exists in the
sibling `pivot_enrich` DB (BHE was yfinance-name-matched to `BHEL.NS`,
score 0.86). This script promotes that verified mapping into the near-empty
`mc.companies.nse_symbol` column, which the read path now prefers.

SOURCES & TRUST (per sc_id, highest wins)
  1. enrich name-match (source=yfinance_namematch, match_score >= 0.85) — HIGH.
  2. enrich scraper-inherited ticker (source=yfinance) that PASSES a
     name-consistency check: yfinance long_name must agree with the (15-char
     truncated) mc company_name. Agree → keep; disagree → this is the
     BHEL/Jay-Electric corruption → do NOT assign.
Collisions: one winner per symbol — has_fundamentals, then trust, then mcap.

SAFETY
  * Writes ONLY mc.companies.nse_symbol (additive: the column is NULL on
    11,246/11,256 rows today). Never touches `ticker`, statements, or enrich.
  * --apply first snapshots every affected (sc_id, old nse_symbol) to a
    timestamped backup JSON, so `--revert <file>` is exact.
  * Default is DRY-RUN (no writes); pass --apply to write.

USAGE
  cd pivot
  .venv/bin/python scripts/repair_company_symbols.py            # dry-run report
  .venv/bin/python scripts/repair_company_symbols.py --apply    # write + backup
  .venv/bin/python scripts/repair_company_symbols.py --revert backups/xxx.json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from difflib import SequenceMatcher

from sqlalchemy import create_engine, text

HERE = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(HERE, "..", ".env")

NAMEMATCH_MIN = 0.85     # trust enrich name-match at/above this score
CONSIST_MIN = 0.72       # scraper-ticker name-consistency keep threshold

_STOP = {"ltd", "limited", "ltd.", "the", "co", "company", "corporation", "corp",
         "india", "industries", "enterprises", "&", "and", "of", "pvt", "private"}


def _load_env() -> dict:
    env = {}
    with open(ENV_PATH) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip()
    return env


def _norm(s: str) -> str:
    s = (s or "").lower()
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _toks(s: str):
    return [t for t in _norm(s).split() if t not in _STOP]


def name_consistency(mc_name: str, yf_name: str) -> float:
    """Agreement between the (<=15-char truncated) mc name and the full
    yfinance long_name. Prefix-aware + token-overlap + ratio, 0..1."""
    a, b = _norm(mc_name), _norm(yf_name)
    if not a or not b:
        return 0.0
    if len(a) >= 5 and b.startswith(a):
        return 1.0
    if a.startswith(b) and len(b) >= 5:
        return 1.0
    ta, tb = set(_toks(mc_name)), set(_toks(yf_name))
    jac = len(ta & tb) / len(ta | tb) if (ta | tb) else 0.0
    first = 1.0 if (_toks(mc_name) and _toks(yf_name)
                    and _toks(mc_name)[0] == _toks(yf_name)[0]) else 0.0
    ratio = SequenceMatcher(None, a, b[:len(a) + 4]).ratio()
    return max(jac, ratio, 0.5 * first + 0.5 * jac)


def compute_plan(fin, enr) -> dict:
    """Return {winners: {sc_id: symbol}, report: {...}} from the two DBs."""
    mc = {}
    with fin.connect() as c:
        for r in c.execute(text(
            "SELECT sc_id, company_name, ticker, nse_symbol FROM mc.companies"
        )):
            mc[r[0]] = {"name": r[1], "scraper_ticker": r[2], "nse_symbol": r[3]}
        fund = {row[0] for row in c.execute(
            text("SELECT DISTINCT sc_id FROM mc.statement_lines"))}

    en = {}
    with enr.connect() as c:
        for r in c.execute(text(
            "SELECT sc_id, ticker, long_name, match_score, source, market_cap "
            "FROM enrich.company_profile WHERE fetch_status='ok'"
        )):
            en[r[0]] = {
                "ticker": r[1], "long_name": r[2],
                "score": float(r[3]) if r[3] is not None else None,
                "source": r[4],
                "market_cap": float(r[5]) if r[5] is not None else None,
            }

    proposals, corrupt = [], []
    for sc_id, e in en.items():
        sym = (e["ticker"] or "").strip().upper()
        if not sym or sc_id not in mc:
            continue
        mc_name = mc[sc_id]["name"]
        yf_name = e["long_name"] or ""
        fundamented = sc_id in fund
        mcap = e["market_cap"] or 0.0
        if e["source"] == "yfinance_namematch":
            if e["score"] is not None and e["score"] >= NAMEMATCH_MIN:
                proposals.append(dict(sc_id=sc_id, symbol=sym, trust=e["score"],
                                      fundamented=fundamented, mcap=mcap,
                                      reason=f"namematch {e['score']:.2f}"))
        else:
            cons = name_consistency(mc_name, yf_name)
            if cons >= CONSIST_MIN:
                proposals.append(dict(sc_id=sc_id, symbol=sym,
                                      trust=0.80 + 0.2 * cons,
                                      fundamented=fundamented, mcap=mcap,
                                      reason=f"scraper cons={cons:.2f}"))
            else:
                corrupt.append(dict(sc_id=sc_id, symbol=sym, mc_name=mc_name,
                                    yf_name=yf_name, cons=cons))

    bysym = {}
    for p in proposals:
        bysym.setdefault(p["symbol"], []).append(p)
    winners, collision_losers = {}, 0
    for sym, cands in bysym.items():
        cands.sort(key=lambda p: (p["fundamented"], p["trust"], p["mcap"]),
                   reverse=True)
        winners[cands[0]["sc_id"]] = sym
        collision_losers += len(cands) - 1

    newly = sum(1 for sc in winners if not mc[sc]["nse_symbol"])
    changed = {sc: sym for sc, sym in winners.items()
               if (mc[sc]["nse_symbol"] or "").upper() != sym.upper()}
    return {
        "winners": winners, "changed": changed, "mc": mc,
        "report": {
            "assignments": len(winners), "newly_backfilled": newly,
            "changes_to_write": len(changed),
            "corrupt_detected": len(corrupt),
            "collision_losers": collision_losers,
        },
        "corrupt_sample": sorted(corrupt, key=lambda x: x["symbol"])[:20],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="write nse_symbol")
    ap.add_argument("--revert", metavar="BACKUP_JSON",
                    help="restore nse_symbol from a backup file")
    args = ap.parse_args()
    env = _load_env()
    fin = create_engine(env["FINANCIALS_DSN"], pool_pre_ping=True)

    if args.revert:
        with open(args.revert) as f:
            backup = json.load(f)
        rows = backup["previous"]  # [{sc_id, nse_symbol(old or null)}]
        with fin.begin() as c:
            for it in rows:
                c.execute(text("UPDATE mc.companies SET nse_symbol=:v WHERE sc_id=:s"),
                          {"v": it["nse_symbol"], "s": it["sc_id"]})
        print(f"reverted {len(rows)} rows from {args.revert}")
        return

    enr = create_engine(env["ENRICH_DSN"], pool_pre_ping=True)
    plan = compute_plan(fin, enr)
    rep = plan["report"]
    print("=" * 60)
    print("mc.companies.nse_symbol repair —",
          "APPLY" if args.apply else "DRY-RUN")
    print("=" * 60)
    for k, v in rep.items():
        print(f"  {k:22s} {v}")
    print("\n  corrupt scraper-ticker sample (NOT assigned):")
    for d in plan["corrupt_sample"]:
        print(f"    {d['symbol']:12s} mc='{d['mc_name'][:16]:16s}' "
              f"yf='{(d['yf_name'] or '')[:32]:32s}' cons={d['cons']:.2f}")

    if not args.apply:
        print("\n(dry-run — no writes. Re-run with --apply to persist.)")
        return

    changed = plan["changed"]
    mc = plan["mc"]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    bdir = os.path.join(HERE, "backups")
    os.makedirs(bdir, exist_ok=True)
    bpath = os.path.join(bdir, f"nse_symbol_backup_{stamp}.json")
    backup = {"created": stamp, "previous": [
        {"sc_id": sc, "nse_symbol": mc[sc]["nse_symbol"]} for sc in changed
    ]}
    with open(bpath, "w") as f:
        json.dump(backup, f, indent=2)
    print(f"\n  backup of {len(changed)} prior values -> {bpath}")

    with fin.begin() as c:
        for sc, sym in changed.items():
            c.execute(text("UPDATE mc.companies SET nse_symbol=:v WHERE sc_id=:s"),
                      {"v": sym, "s": sc})
    print(f"  APPLIED: nse_symbol set on {len(changed)} rows.")

    # Guard: a newly-assigned symbol can collide with a PRE-EXISTING nse_symbol
    # this run didn't touch. For any symbol on >1 sc_id, keep it on the row with
    # the most statement_lines (the real primary listing) and NULL the rest.
    nulled = _resolve_collisions(fin, backup, bpath)
    if nulled:
        print(f"  collision guard: NULLed {nulled} duplicate-symbol loser rows.")
    print(f"  revert with: python scripts/repair_company_symbols.py --revert {bpath}")


def _resolve_collisions(fin, backup: dict, bpath: str) -> int:
    with fin.connect() as c:
        dups = [r[0] for r in c.execute(text(
            "SELECT upper(nse_symbol) FROM mc.companies "
            "WHERE nse_symbol IS NOT NULL AND nse_symbol<>'' "
            "GROUP BY 1 HAVING count(*)>1"))]
        to_null = []
        for s in dups:
            rows = c.execute(text(
                "SELECT co.sc_id, co.nse_symbol, (SELECT count(*) FROM "
                "mc.statement_lines sl WHERE sl.sc_id=co.sc_id) f "
                "FROM mc.companies co WHERE upper(nse_symbol)=:s ORDER BY f DESC"),
                {"s": s}).fetchall()
            to_null += [(r[0], r[1]) for r in rows[1:]]  # keep rows[0]
    if not to_null:
        return 0
    # Extend the same backup file so --revert restores these too.
    backup["previous"].extend({"sc_id": sc, "nse_symbol": old} for sc, old in to_null)
    with open(bpath, "w") as f:
        json.dump(backup, f, indent=2)
    with fin.begin() as c:
        for sc, _ in to_null:
            c.execute(text("UPDATE mc.companies SET nse_symbol=NULL WHERE sc_id=:s"),
                      {"s": sc})
    return len(to_null)


if __name__ == "__main__":
    main()
