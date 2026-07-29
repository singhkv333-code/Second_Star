#!/usr/bin/env python3
"""Phase 1 of the MoneyControl completeness effort: fill BOTH-exchange tickers
(nse_symbol + bse_code) for every mc.companies row that is missing one, from
MoneyControl's own price API keyed by sc_id.

WHY
---
`nse_symbol` was only ever populated by promoting a >=0.85 yfinance name-match
from the sibling `enrich` DB (repair_company_symbols.py). Anything enrich lacked
stayed NULL -- including flagship NSE names (SBIN='SBI', M&M='MM', HUL='HU',
DRREDDY='DRL', ...). 6,648 rows have no nse_symbol; 2,514 of those already carry
full fundamentals and are invisible to the resolver purely for lack of a symbol.

We no longer privilege NSE (that bias was a yfinance artifact -- Kite quotes BSE
natively). So this fills nse_symbol AND bse_code as peers, from the authoritative
per-company source:
    https://priceapi.moneycontrol.com/pricefeed/{nse|bse}/equitycash/{sc_id}
which returns SC_FULLNM + NSEID + BSEID.

SAFETY (mirrors repair_company_symbols.py)
  * NEVER overwrites a non-null value -- only fills columns that are NULL/''.
  * NAME-CONSISTENCY GATE: the API's SC_FULLNM must agree with our stored
    company_name/company_slug, else the row is REJECTED (guards the observed
    sc_id='HU' -> "Unimers India" mis-key; a naive write would corrupt).
  * COLLISION GATE: an NSEID/BSEID already held by a DIFFERENT sc_id is not
    written (flagged instead).
  * DRY-RUN by default (writes proposed.json / rejects.json / misses.json and
    touches nothing). --apply writes, after snapshotting every affected
    (sc_id, old nse_symbol, old bse_code) to a timestamped backup JSON.
  * Resumable: appends each fetched sc_id to a checkpoint JSONL; re-runs skip
    already-fetched ids.

USAGE
  cd pivot
  .venv/bin/python scripts/backfill_symbols_from_mc_priceapi.py            # dry-run
  .venv/bin/python scripts/backfill_symbols_from_mc_priceapi.py --limit 50 # small probe
  .venv/bin/python scripts/backfill_symbols_from_mc_priceapi.py --apply    # write + backup
"""
from __future__ import annotations

import argparse
import json
import os
import re
import time
from datetime import datetime, timezone

import requests
from sqlalchemy import create_engine, text

HERE = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(HERE, "..", ".env")
OUT_DIR = os.path.join(HERE, "..", "backups")
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

CONSIST_MIN = 0.55        # token-overlap floor between API name and our name
_NSE_RE = re.compile(r"^[A-Z0-9&.-]{1,25}$")
_BSE_RE = re.compile(r"^[0-9]{6}$")
_STOP = {"ltd", "limited", "ltd.", "the", "co", "company", "corporation",
         "corp", "india", "industries", "enterprises", "and", "of", "pvt",
         "private", "&"}


def _load_env() -> dict:
    env = {}
    with open(ENV_PATH) as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip()
    return env


def _toks(s: str) -> set[str]:
    s = re.sub(r"[^a-z0-9 ]", " ", (s or "").lower())
    return {t for t in s.split() if t and t not in _STOP}


def _name_agrees(api_name: str, our_name: str, our_slug: str) -> float:
    """Token-overlap (Jaccard-ish) between the API's full name and our stored
    name/slug. Slug is un-truncated so it helps when company_name is clipped."""
    a = _toks(api_name)
    if not a:
        return 0.0
    # slug is one long token: split on our known name tokens by substring test
    b = _toks(our_name)
    slug = re.sub(r"[^a-z0-9]", "", (our_slug or "").lower())
    best = 0.0
    if b:
        inter = len(a & b)
        best = inter / max(1, len(a))
    # slug substring bonus: every api token that appears in the slug counts
    if slug:
        hit = sum(1 for t in a if t in slug)
        best = max(best, hit / max(1, len(a)))
    return best


import threading
_HEADERS = {"User-Agent": UA, "Accept": "application/json, text/plain, */*",
            "Referer": "https://www.moneycontrol.com/"}
_local = threading.local()


def _session() -> requests.Session:
    s = getattr(_local, "sess", None)
    if s is None:
        s = requests.Session()
        s.headers.update(_HEADERS)
        _local.sess = s
    return s


def _fetch(seg: str, sc_id: str) -> dict | None:
    url = f"https://priceapi.moneycontrol.com/pricefeed/{seg}/equitycash/{sc_id}"
    for attempt in range(3):
        try:
            r = _session().get(url, timeout=12)
            if r.status_code == 200:
                d = (r.json() or {}).get("data")
                return d if isinstance(d, dict) and d else None
            if r.status_code in (429, 503):     # throttled — back off
                time.sleep(2.0 * (attempt + 1))
                continue
            return None
        except Exception:
            if attempt < 2:
                time.sleep(1.0)
    return None


def _probe(sc_id: str) -> dict | None:
    """NSE path first (it also returns BSEID for dual-listed); fall back to BSE
    path for BSE-only names. Returns the richest data dict found."""
    d = _fetch("nse", sc_id)
    if d and (d.get("NSEID") or d.get("BSEID")):
        return d
    b = _fetch("bse", sc_id)
    return b or d


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="write to DB (default dry-run)")
    ap.add_argument("--limit", type=int, default=0, help="cap candidates (0 = all)")
    ap.add_argument("--pace", type=float, default=0.25, help="seconds between fetches")
    ap.add_argument("--fundamented-only", action="store_true",
                    help="only companies that already have fundamentals (real/live)")
    args = ap.parse_args()

    env = _load_env()
    eng = create_engine(env["FINANCIALS_DSN"], pool_pre_ping=True)
    os.makedirs(OUT_DIR, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    ckpt_path = os.path.join(OUT_DIR, "symbol_backfill_checkpoint.jsonl")

    # Candidates: any row missing nse_symbol. Order fundamentals-first so the
    # highest-value rows (real companies with data) resolve first.
    _fund_gate = ("AND EXISTS (SELECT 1 FROM mc.statement_lines s WHERE s.sc_id = co.sc_id)"
                  if args.fundamented_only else "")
    with eng.connect() as c:
        rows = c.execute(text(f"""
            SELECT co.sc_id, co.company_name, co.company_slug, co.nse_symbol, co.bse_code,
                   EXISTS(SELECT 1 FROM mc.statement_lines s WHERE s.sc_id = co.sc_id) AS has_fun
            FROM mc.companies co
            WHERE (co.nse_symbol IS NULL OR co.nse_symbol = '')
              AND co.sc_id ~ '^[A-Za-z]' AND length(co.sc_id) <= 8
              {_fund_gate}
            ORDER BY has_fun DESC, co.sc_id
        """)).fetchall()
        # symbols already taken (to catch collisions)
        taken_nse = {r[0].upper() for r in c.execute(text(
            "SELECT nse_symbol FROM mc.companies WHERE nse_symbol IS NOT NULL AND nse_symbol<>''"
        )).fetchall() if r[0]}
        taken_bse = {r[0] for r in c.execute(text(
            "SELECT bse_code FROM mc.companies WHERE bse_code IS NOT NULL AND bse_code<>''"
        )).fetchall() if r[0]}

    if args.limit:
        rows = rows[: args.limit]

    done = set()
    if os.path.exists(ckpt_path):
        for line in open(ckpt_path):
            try:
                done.add(json.loads(line)["sc_id"])
            except Exception:
                pass

    proposed, rejects, misses = [], [], []
    ck = open(ckpt_path, "a")
    todo = [r for r in rows if r[0] not in done]

    # Fetch is I/O-bound (one price-API call each) -- parallelize it 8-way; the
    # classify/collision pass stays SERIAL so the "symbol not already taken"
    # guard is race-free. as_completed streams results as fetches land.
    from concurrent.futures import ThreadPoolExecutor, as_completed
    n = 0
    with ThreadPoolExecutor(max_workers=8) as pool:
        futs = {pool.submit(_probe, r[0]): r for r in todo}
        for fut in as_completed(futs):
            sc_id, cname, cslug, cur_nse, cur_bse, has_fun = futs[fut]
            n += 1
            try:
                d = fut.result()
            except Exception:
                d = None
            rec = {"sc_id": sc_id, "our_name": cname, "has_fun": bool(has_fun)}
            if not d:
                misses.append({**rec, "why": "price API returned no data"})
                ck.write(json.dumps({"sc_id": sc_id, "r": "miss"}) + "\n")
            else:
                api_name = d.get("SC_FULLNM") or d.get("company") or ""
                score = _name_agrees(api_name, cname or "", cslug or "")
                nseid = (d.get("NSEID") or "").strip().upper()
                bseid = (d.get("BSEID") or "").strip()
                if score < CONSIST_MIN:
                    rejects.append({**rec, "api_name": api_name, "score": round(score, 2),
                                    "NSEID": nseid, "BSEID": bseid, "why": "name mismatch"})
                    ck.write(json.dumps({"sc_id": sc_id, "r": "reject"}) + "\n")
                else:
                    write_nse = (nseid if _NSE_RE.match(nseid) and not (cur_nse or "").strip()
                                 and nseid not in taken_nse else None)
                    write_bse = (bseid if _BSE_RE.match(bseid) and not (cur_bse or "").strip()
                                 and bseid not in taken_bse else None)
                    if write_nse or write_bse:
                        if write_nse:
                            taken_nse.add(write_nse)
                        if write_bse:
                            taken_bse.add(write_bse)
                        proposed.append({**rec, "api_name": api_name, "score": round(score, 2),
                                         "nse_symbol": write_nse, "bse_code": write_bse})
                    else:
                        misses.append({**rec, "api_name": api_name,
                                       "NSEID": nseid, "BSEID": bseid,
                                       "why": "no writable ticker (collision or already set)"})
                    ck.write(json.dumps({"sc_id": sc_id, "r": "prop"}) + "\n")
            if n % 100 == 0:
                print(f"  ...{n} probed | prop={len(proposed)} rej={len(rejects)} miss={len(misses)}",
                      flush=True)
    ck.close()

    json.dump(proposed, open(os.path.join(OUT_DIR, f"symbol_proposed_{stamp}.json"), "w"), indent=1)
    json.dump(rejects, open(os.path.join(OUT_DIR, f"symbol_rejects_{stamp}.json"), "w"), indent=1)
    json.dump(misses, open(os.path.join(OUT_DIR, f"symbol_misses_{stamp}.json"), "w"), indent=1)

    nse_fills = sum(1 for p in proposed if p["nse_symbol"])
    bse_fills = sum(1 for p in proposed if p["bse_code"])
    print(f"\nPROBED {n} | proposed {len(proposed)} (nse={nse_fills}, bse={bse_fills}) "
          f"| rejects {len(rejects)} | misses {len(misses)}")
    print(f"reports in {OUT_DIR}/symbol_*_{stamp}.json")

    if not args.apply:
        print("\nDRY-RUN — nothing written. Review the reports, then re-run with --apply.")
        return

    # Snapshot + write (only NULL columns).
    with eng.begin() as c:
        backup = []
        for p in proposed:
            backup.append({"sc_id": p["sc_id"], "old_nse": None, "old_bse": None})
        json.dump(backup, open(os.path.join(OUT_DIR, f"symbol_backup_{stamp}.json"), "w"), indent=1)
        applied = 0
        for p in proposed:
            sets, params = [], {"sc": p["sc_id"]}
            if p["nse_symbol"]:
                sets.append("nse_symbol = :nse")
                params["nse"] = p["nse_symbol"]
            if p["bse_code"]:
                sets.append("bse_code = :bse")
                params["bse"] = p["bse_code"]
            if not sets:
                continue
            # Re-assert NULL guard in SQL so a concurrent writer can't be clobbered.
            where = " AND ".join(
                [f"({col} IS NULL OR {col}='')" for col, key in
                 (("nse_symbol", "nse"), ("bse_code", "bse")) if key in params]
            )
            c.execute(text(f"UPDATE mc.companies SET {', '.join(sets)} "
                           f"WHERE sc_id = :sc AND ({where})"), params)
            applied += 1
        print(f"APPLIED {applied} rows. Backup: symbol_backup_{stamp}.json")


if __name__ == "__main__":
    main()
