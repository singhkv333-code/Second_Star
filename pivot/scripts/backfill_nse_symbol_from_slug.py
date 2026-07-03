#!/usr/bin/env python3
"""Phase 2 of the symbol repair: recover nse_symbol for still-unmapped
FUNDAMENTED companies using the un-truncated company_slug.

Phase 1 (repair_company_symbols.py) matched on mc.company_name, which is
truncated to 15 chars — so real companies whose distinctive token fell past
char 15 (e.g. "Birla Capital" for "Aditya Birla Capital") scored too low and
got NO symbol. But `mc.companies.company_slug` carries the FULL name
("adityabirlacapital"), and `enrich` already holds a yfinance-VALIDATED
`yf_symbol` for 716 of these rows. This pass CONFIRMS that validated symbol by
matching the full slug against the yfinance `long_name` (both describe the same
company from the same source, so a real match is unambiguous and a wrong
enrich guess — KRYSTAL for "Integrated Personnel" — is rejected).

Only assigns to rows that are currently NULL, only NSE (`.NS`) symbols, and
only when the symbol isn't already taken. Collision among new candidates →
highest slug/long_name confidence, then most fundamentals. Backed up + reversible.

Usage:  cd pivot
  .venv/bin/python scripts/backfill_nse_symbol_from_slug.py           # dry-run
  .venv/bin/python scripts/backfill_nse_symbol_from_slug.py --apply
  .venv/bin/python scripts/repair_company_symbols.py --revert backups/xxx.json
"""
from __future__ import annotations
import argparse, json, os, re
from datetime import datetime, timezone
from difflib import SequenceMatcher
from sqlalchemy import create_engine, text

HERE = os.path.dirname(os.path.abspath(__file__))
ACCEPT = 0.82                       # char-level slug<->long_name similarity
_NSE_RE = re.compile(r"^[A-Z0-9&-]{1,20}$")
_STOP = {"limited", "ltd", "ltd.", "the", "and", "co", "company", "india",
         "industries", "enterprises", "corporation", "corp", "pvt", "private"}


def _env():
    env = {}
    for line in open(os.path.join(HERE, "..", ".env")):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("="); env[k.strip()] = v.strip()
    return env


def _alnum(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _long_norm(name: str) -> str:
    toks = [t for t in re.split(r"[^a-z0-9]+", (name or "").lower()) if t and t not in _STOP]
    return "".join(toks)


def confidence(slug: str, long_name: str) -> float:
    a, b = _alnum(slug), _long_norm(long_name)
    if not a or not b:
        return 0.0
    if len(min(a, b, key=len)) >= 6 and (a.startswith(b) or b.startswith(a)):
        return 1.0
    return SequenceMatcher(None, a, b).ratio()


CORRECT = 0.90   # min winner confidence to take a symbol AWAY from a current holder


def build(fin, enr):
    """Reconcile nse_symbol ownership from the authoritative slug<->long_name
    signal. For every FUNDAMENTED company with a validated NSE yf_symbol, the
    rightful owner of that symbol is the highest slug-confidence claimant.
    Produces additive assigns (currently NULL) and corrections (symbol moves
    off a wrong pass-1 holder), each backed up."""
    with fin.connect() as c:
        fund = {r[0] for r in c.execute(text("SELECT DISTINCT sc_id FROM mc.statement_lines"))}
        rows = {r[0]: {"name": r[1], "slug": r[2], "cur": r[3]} for r in c.execute(text(
            "SELECT sc_id, company_name, company_slug, nse_symbol FROM mc.companies"))}
    cur_holder = {}                     # SYMBOL(upper) -> sc_id currently holding it
    for sc, m in rows.items():
        if m["cur"]:
            cur_holder[str(m["cur"]).upper()] = sc
    with enr.connect() as c:
        en = {r[0]: {"yf": r[1], "long": r[2]} for r in c.execute(text(
            "SELECT sc_id, yf_symbol, long_name FROM enrich.company_profile "
            "WHERE fetch_status='ok' AND yf_symbol IS NOT NULL"))}

    # candidates: fundamented + enrich .NS yf_symbol. Track EVERY sc_id's conf
    # per symbol (incl. the current holder) so we never swap a symbol off a row
    # that is itself a valid slug-match (those are duplicate MC listings of the
    # SAME company — e.g. RELIANCE on RI vs RI38 — not a wrong assignment).
    conf_by = {}                       # (symbol, sc_id) -> conf
    cands_by_sym, rejected = {}, []
    for sc in fund:
        e = en.get(sc)
        if not e:
            continue
        yf = (e["yf"] or "")
        if not yf.upper().endswith(".NS"):
            continue
        base = yf[:-3].upper()
        if not _NSE_RE.match(base) or sc not in rows:
            continue
        conf = confidence(rows[sc]["slug"], e["long"])
        conf_by[(base, sc)] = conf
        rec = {"sc_id": sc, "symbol": base, "conf": conf,
               "slug": rows[sc]["slug"], "long": e["long"], "name": rows[sc]["name"],
               "fund_n": len(rows)}   # placeholder; real count filled below
        if conf >= ACCEPT:
            cands_by_sym.setdefault(base, []).append(rec)
        else:
            rejected.append(rec)

    # statement counts for candidate rows (fundamented tie-break)
    fund_count = {}
    with fin.connect() as c:
        ids = [r["sc_id"] for cs in cands_by_sym.values() for r in cs]
        for i in range(0, len(ids), 1000):
            for r in c.execute(text("SELECT sc_id, count(*) FROM mc.statement_lines "
                                    "WHERE sc_id = ANY(:ids) GROUP BY 1"),
                               {"ids": ids[i:i + 1000]}):
                fund_count[r[0]] = r[1]

    assigns, corrections, blocked = {}, [], []
    for sym, cands in cands_by_sym.items():
        for r in cands:
            r["fund_n"] = fund_count.get(r["sc_id"], 0)
        cands.sort(key=lambda r: (r["conf"], r["fund_n"]), reverse=True)
        winner = cands[0]
        held_by = cur_holder.get(sym)
        if held_by == winner["sc_id"]:
            continue                              # already correct
        if held_by is None:
            assigns[winner["sc_id"]] = sym        # additive (symbol free)
            continue
        # Someone else holds it. If THAT holder is itself a valid slug-match
        # (>=ACCEPT), it's a legit duplicate — leave it, never swap. Only correct
        # when the holder fails the match (wrong company) and the winner is sure.
        holder_conf = conf_by.get((sym, held_by), 0.0)
        if holder_conf >= ACCEPT:
            continue                              # duplicate listing — keep holder
        if winner["conf"] >= CORRECT:
            corrections.append({"sym": sym, "from": held_by, "to": winner["sc_id"],
                                "conf": winner["conf"], "holder_conf": holder_conf,
                                "from_name": rows[held_by]["name"], "to_name": winner["name"]})
        else:
            blocked.append({"sym": sym, "held_by": held_by, "conf": winner["conf"]})
    return {"assigns": assigns, "corrections": corrections, "blocked": blocked,
            "rejected": rejected, "rows": rows}


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    env = _env()
    fin = create_engine(env["FINANCIALS_DSN"], pool_pre_ping=True)
    enr = create_engine(env["ENRICH_DSN"], pool_pre_ping=True)
    plan = build(fin, enr)
    assigns, corrections, rows = plan["assigns"], plan["corrections"], plan["rows"]
    print("=" * 60)
    print("phase-2 slug reconciliation —", "APPLY" if args.apply else "DRY-RUN")
    print("=" * 60)
    print(f"  additive assigns (was NULL)           : {len(assigns)}")
    print(f"  corrections (symbol moved off wrong)  : {len(corrections)}")
    print(f"  blocked (challenger conf<{CORRECT})       : {len(plan['blocked'])}")
    print(f"  rejected (slug!=long_name, conf<{ACCEPT})  : {len(plan['rejected'])}")
    print("\n  sample ADDS:")
    for sc, sym in list(assigns.items())[:10]:
        print(f"    {sym:12s} <- {sc:10s} {rows[sc]['name'][:18]!r}")
    print("  sample CORRECTIONS (pass-1 errors the slug signal fixes):")
    for cr in sorted(corrections, key=lambda x: -x["conf"])[:10]:
        print(f"    {cr['sym']:12s} {cr['from']}({cr['from_name'][:14]!r}) -> {cr['to']}({cr['to_name'][:14]!r}) conf={cr['conf']:.2f}")

    if not args.apply:
        print("\n(dry-run — re-run with --apply to persist.)")
        return

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    bdir = os.path.join(HERE, "backups"); os.makedirs(bdir, exist_ok=True)
    bpath = os.path.join(bdir, f"nse_symbol_slug_backup_{stamp}.json")
    prev = [{"sc_id": sc, "nse_symbol": None} for sc in assigns]
    prev += [{"sc_id": cr["from"], "nse_symbol": cr["sym"]} for cr in corrections]  # restore old holder
    prev += [{"sc_id": cr["to"], "nse_symbol": rows[cr["to"]]["cur"]} for cr in corrections]  # restore new holder's old val
    json.dump({"created": stamp, "previous": prev}, open(bpath, "w"), indent=2)
    with fin.begin() as c:
        for sc, sym in assigns.items():
            c.execute(text("UPDATE mc.companies SET nse_symbol=:v WHERE sc_id=:s"), {"v": sym, "s": sc})
        for cr in corrections:
            c.execute(text("UPDATE mc.companies SET nse_symbol=NULL WHERE sc_id=:s"), {"s": cr["from"]})
            c.execute(text("UPDATE mc.companies SET nse_symbol=:v WHERE sc_id=:s"), {"v": cr["sym"], "s": cr["to"]})
    print(f"\n  APPLIED {len(assigns)} adds + {len(corrections)} corrections. backup -> {bpath}")


if __name__ == "__main__":
    main()
