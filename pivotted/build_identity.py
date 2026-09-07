"""Build `company_identity` — one verified row per company, from the exchanges.

WHY THIS EXISTS

`mc.companies` cannot say who a company is. Measured today, on 11,256 rows:

  company_name   truncated at 15 CHARACTERS. Max length is 15; 3,052 rows sit
                 exactly on it. "Avenue Supermarts" is stored "Avenue Supermar",
                 which is why a search for its real name returned nothing.
  company_slug   the only intact name — "avenuesupermarts", "gailindia".
  nse_symbol     45% populated, and only 2,432 of 5,114 are NSE symbols at all.
                 2,000 are BSE symbols sitting in a column called nse_symbol.
  sc_id          Moneycontrol's internal code, which COLLIDES with real
                 tickers: "GAIL" is Gurunanak Agric, "ACC" is Active Clothing,
                 "BEL" is BLS E-Services.
  ticker         26% populated and polluted (Active Clothing carries 'ACC').
  sector         0% populated.  market_cap  0% populated.
  is_active      True on all 11,256 rows.  listed_on / delisted_on: NULL on
                 all 11,256. The table cannot express "this company delisted".

So identity is resolved HERE, against the exchanges' own published lists, and
written to one table everything else can join. `mc` is left untouched — it is
owned by pivot-mc-scraper and read live by Pivot's stock page and screener, so
dropping or rewriting its columns would break production and be undone by the
next scrape. The disruption was never that the columns exist; it is that code
trusted the wrong ones.

SOURCES (official, fetched at run time)

  NSE mainboard  nsearchives.nseindia.com/content/equities/EQUITY_L.csv
  NSE SME        nsearchives.nseindia.com/emerge/corporates/content/SME_EQUITY_L.csv
  BSE            api.bseindia.com/BseIndiaAPI/api/ListofScripData (Equity, Active)

All three carry ISIN, which `mc` has no column for at all and which is the only
identity key that survived today's audits — sc_id collides, ticker is polluted,
nse_symbol is mislabelled, and enrich's ticker returns the right NAME attached
to the wrong company.

    pivot/.venv/bin/python pivotted/build_identity.py [--dry-run]
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import sys
import urllib.request
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path

PIVOT = Path(__file__).resolve().parent.parent / "pivot"
sys.path.insert(0, str(PIVOT))

NSE_EQ = "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv"
NSE_SME = "https://nsearchives.nseindia.com/emerge/corporates/content/SME_EQUITY_L.csv"
BSE_LIST = ("https://api.bseindia.com/BseIndiaAPI/api/ListofScripData/w"
            "?Group=&Scripcode=&industry=&segment=Equity&status=Active")
NAME_FLOOR = 0.9   # exchange name must agree; see resolve_all
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

# The column NAMES carry the trust level, and every one also carries a DB
# COMMENT, so `\d+ company_identity` in psql — or any DB browser — answers
# "which column do I trust for symbols and mapping" without anyone having to
# remember this conversation. That is the whole point of the naming scheme:
#
#   verified_*   confirmed against the exchange's own published list TODAY
#   mc_*         copied from Moneycontrol, UNVERIFIED, evidence only
#   match_*      how the row was resolved and how strongly
#
# Anything without a `mc_` prefix has been checked. Anything with one has not.
DDL = """
CREATE TABLE IF NOT EXISTS company_identity (
  isin                TEXT,
  verified_symbol     TEXT NOT NULL,
  verified_exchange   TEXT NOT NULL,
  verified_name       TEXT NOT NULL,
  verified_bse_code   TEXT,
  mc_sc_id            TEXT PRIMARY KEY,
  mc_slug             TEXT,
  mc_is_primary       BOOLEAN NOT NULL DEFAULT TRUE,
  mc_latest_period    DATE,
  mc_metric_count     INTEGER,
  match_route         TEXT NOT NULL,
  match_name_score    REAL NOT NULL,
  verified_at         TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS company_identity_symbol_idx
  ON company_identity (upper(verified_symbol));
CREATE INDEX IF NOT EXISTS company_identity_isin_idx ON company_identity (isin);

COMMENT ON TABLE company_identity IS
 'Verified company identity. THE source of truth for symbol/exchange/name. '
 'Built by pivotted/build_identity.py from the exchanges own published lists '
 '(NSE EQUITY_L.csv, NSE SME_EQUITY_L.csv, BSE ListofScripData). '
 'DO NOT resolve symbols from mc.companies directly: its nse_symbol column '
 'holds ~2000 BSE symbols, its ticker column is polluted (Active Clothing '
 'carries ticker=ACC), its sc_id collides with real tickers (sc_id GAIL is '
 'Gurunanak Agric, ACC is Active Clothing, BEL is BLS E-Services), and its '
 'company_name is truncated at 15 characters.';

COMMENT ON COLUMN company_identity.isin IS
 'TRUST: HIGHEST. Exchange-issued, cross-exchange, stable. The join key to '
 'use for anything new (XBRL filings, corporate actions). mc has no ISIN.';
COMMENT ON COLUMN company_identity.verified_symbol IS
 'TRUST: HIGH. Tradeable symbol confirmed present in verified_exchange''s '
 'official list. Use THIS for quotes/bars, never mc.nse_symbol or mc.ticker.';
COMMENT ON COLUMN company_identity.verified_exchange IS
 'TRUST: HIGH. NSE | NSE_SME | BSE — where verified_symbol actually trades. '
 'mc.nse_symbol lies about this: ~2000 of its values are BSE symbols.';
COMMENT ON COLUMN company_identity.verified_name IS
 'TRUST: HIGH. The exchange''s own company name, full length. Use for display '
 'and search. mc.company_name is truncated at 15 chars and unusable.';
COMMENT ON COLUMN company_identity.verified_bse_code IS
 'TRUST: HIGH. Numeric BSE scrip code, BSE rows only. Equals Kite''s BSE '
 'exchange_token.';
COMMENT ON COLUMN company_identity.mc_sc_id IS
 'JOIN KEY ONLY — NOT AN IDENTIFIER. Joins mc.companies / mc.statement_lines / '
 'enrich.company_profile. Never resolve a user-supplied symbol against it: '
 'these codes collide with other companies real tickers.';
COMMENT ON COLUMN company_identity.mc_is_primary IS
 'FILTER ON THIS for anything per-company. Moneycontrol carries SEVERAL sc_ids '
 'for one security — Tata Steel has 6, Reliance 4 — so 149 ISINs appear on more '
 'than one row and an extraction that ignores this fetches Tata Steel six '
 'times. TRUE = the sc_id holding the deepest, most recent filings for that '
 'ISIN. 118 of the 333 duplicate sc_ids carry no filings at all.';
COMMENT ON COLUMN company_identity.mc_latest_period IS
 'Newest period_end Moneycontrol holds for this sc_id. The primary-selection '
 'evidence, and a staleness check: a row years behind is a dead listing.';
COMMENT ON COLUMN company_identity.mc_metric_count IS
 'Rows in mc.growth_metrics_mat for this sc_id — filing depth, the tie-break '
 'when two sc_ids share a latest period.';
COMMENT ON COLUMN company_identity.mc_slug IS
 'UNVERIFIED. Moneycontrol name slug, kept as the evidence this row was '
 'matched on. Not authoritative — use verified_name.';
COMMENT ON COLUMN company_identity.match_route IS
 'Which rule resolved this row. ticker->* routes agreed with the exchange '
 'name only 22%% of the time before the name floor was applied.';
COMMENT ON COLUMN company_identity.match_name_score IS
 'Agreement between mc_slug and verified_name, 0-1. Rows below 0.9 were '
 'REJECTED, not stored: the 0.6-0.9 band was sampled and is ~half wrong '
 '(greatwesternindustries matched Great Eastern Shipping).';
COMMENT ON COLUMN company_identity.verified_at IS
 'When the exchange lists were fetched. Listings change; re-run the builder '
 'rather than trusting a stale row.';
"""


def _get(url: str, referer: str) -> bytes:
    req = urllib.request.Request(url, headers={
        "User-Agent": UA, "Referer": referer, "Accept": "*/*"})
    import ssl
    try:
        import certifi
        ctx = ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=90, context=ctx) as r:
        return r.read()


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


_SUFFIX = re.compile(
    r"(limited|ltd|india|indian|company|co|corporation|corp|enterprises"
    r"|industries|the)$")


def key(s: str) -> str:
    """Normalised match key — suffix words stripped repeatedly.

    'GAIL (India) Limited' and slug 'gailindia' must land on the same key, so
    the strip runs several times: one pass leaves 'gailindia' from the slug and
    'gailindia' from the name only after both 'limited' and 'india' are gone.
    """
    s = norm(s)
    for _ in range(4):
        nxt = _SUFFIX.sub("", s)
        if nxt == s:
            break
        s = nxt
    return s


def sim(a: str, b: str) -> float:
    a, b = key(a), key(b)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if a.startswith(b) or b.startswith(a):
        return 0.95
    return round(SequenceMatcher(None, a, b).ratio(), 3)


def load_official() -> dict:
    """Every listed security the exchanges themselves publish."""
    out = {"nse": {}, "sme": {}, "bse_sym": {}, "bse_cd": {}, "by_name": {}}

    for tag, url, sym_c, name_c, isin_c, exch in (
            ("nse", NSE_EQ, "SYMBOL", "NAME OF COMPANY", "ISIN NUMBER", "NSE"),
            ("sme", NSE_SME, "SYMBOL", "NAME_OF_COMPANY", "ISIN_NUMBER", "NSE_SME")):
        raw = _get(url, "https://www.nseindia.com/").decode("utf-8", "replace")
        for row in csv.DictReader(io.StringIO(raw)):
            row = {(k or "").strip(): (v or "").strip() for k, v in row.items()}
            s = row.get(sym_c, "").upper()
            if not s:
                continue
            rec = {"exchange": exch, "symbol": s, "name": row.get(name_c, ""),
                   "isin": row.get(isin_c) or None, "scrip_cd": None}
            out[tag][s] = rec
            out["by_name"].setdefault(key(rec["name"]), []).append(rec)

    data = json.loads(_get(BSE_LIST, "https://www.bseindia.com/"))
    for r in data:
        sym = (r.get("scrip_id") or "").strip().upper()
        cd = str(r.get("SCRIP_CD") or "").strip()
        name = (r.get("Issuer_Name") or r.get("Scrip_Name") or "").strip()
        if not (sym or cd):
            continue
        rec = {"exchange": "BSE", "symbol": sym or cd, "name": name,
               "isin": (r.get("ISIN_NUMBER") or "").strip() or None,
               "scrip_cd": cd or None}
        if sym:
            out["bse_sym"][sym] = rec
        if cd:
            out["bse_cd"][cd] = rec
        out["by_name"].setdefault(key(name), []).append(rec)
    return out


def resolve_all(rows, off) -> tuple[list, dict]:
    """One row per company, by the first route that verifies. Order matters.

    ISIN-bearing exchange records are the authority; `mc`'s own columns are
    only ever used as a LOOKUP KEY into them, never as the answer. That is the
    whole point: nse_symbol is right 48% of the time about the exchange, so it
    is treated as "a string that might be some symbol somewhere" and the
    exchange's list decides what it actually is.
    """
    stamp = datetime.now(timezone.utc)
    resolved, stats = [], {}

    def bump(r):
        stats[r] = stats.get(r, 0) + 1

    for sc_id, slug, name15, nse_s, bse_c, ticker in rows:
        u = (nse_s or "").strip().upper()
        t = (ticker or "").strip().upper()
        b = str(bse_c).strip() if bse_c else ""
        hit = route = None

        if u and u in off["nse"]:
            hit, route = off["nse"][u], "nse_symbol->NSE"
        elif u and u in off["sme"]:
            hit, route = off["sme"][u], "nse_symbol->NSE_SME"
        elif b and b in off["bse_cd"]:
            hit, route = off["bse_cd"][b], "bse_code->BSE"
        elif u and u in off["bse_sym"]:
            hit, route = off["bse_sym"][u], "nse_symbol->BSE(mislabelled)"
        elif t and t in off["nse"]:
            hit, route = off["nse"][t], "ticker->NSE"
        elif t and t in off["bse_sym"]:
            hit, route = off["bse_sym"][t], "ticker->BSE"
        else:
            cands = off["by_name"].get(key(slug)) or []
            uniq = {(c["exchange"], c["symbol"]) for c in cands}
            if len(uniq) == 1:
                hit, route = cands[0], "slug-name->exchange"
            elif cands:
                # Prefer the NSE listing of a dual-listed name; it is the more
                # liquid line and the one every other symbol column means.
                nse = [c for c in cands if c["exchange"] == "NSE"]
                if len({c["symbol"] for c in nse}) == 1:
                    hit, route = nse[0], "slug-name->NSE(dual)"
                else:
                    bump("AMBIGUOUS")
                    continue
        if not hit:
            bump("unresolved")
            continue
        # THE NAME MUST AGREE WITH THE EXCHANGE'S OWN. A symbol column only
        # ever proposes a candidate; the exchange's published name confirms or
        # refuses it. Without this the polluted columns walk straight in:
        # mc.ticker='SCHNEIDER' sits on Eider Electronics' row and matched
        # NSE's real SCHNEIDER, and ticker->NSE as a whole agreed on the name
        # only 22% of the time with 39% flatly disagreeing.
        #
        # The floor is 0.9 because the 0.6-0.9 band was sampled and is roughly
        # half wrong: 'greatwesternindustries' -> "Great Eastern Shipping",
        # 'geiindustrialsystems' -> "N.B.I. Industrial Finance", ICICI Pru
        # LIFE INSURANCE -> ICICI Pru ASSET MANAGEMENT. It also drops the ETF
        # rows, whose official issuer name is just the fund house
        # ("ICICI Prudential Mutual Fund") — correct, since those are schemes,
        # not companies with financial statements.
        score = sim(slug, hit["name"])
        if score < NAME_FLOOR:
            bump(f"REJECTED name<{NAME_FLOOR} ({route})")
            continue
        bump(route)
        resolved.append((sc_id, hit["exchange"], hit["symbol"], hit["isin"],
                         hit["name"], slug, hit["scrip_cd"], route,
                         score, stamp))
    return resolved, stats


def mark_primary(resolved, depth):
    """One primary sc_id per ISIN: newest filing wins, then filing depth.

    Rows are (sc_id, exchange, symbol, isin, name, slug, scrip_cd, route,
    score, stamp); this returns them with (is_primary, latest, count) appended.

    Rows without an ISIN cannot be grouped, so they stay primary — they are
    their own identity as far as anything here can tell.
    """
    by_isin = {}
    for r in resolved:
        if r[3]:
            by_isin.setdefault(r[3], []).append(r)
    winner = {}
    for isin, group in by_isin.items():
        if len(group) == 1:
            continue
        best = max(group, key=lambda r: (
            depth.get(r[0], (0, None))[1] or __import__("datetime").date.min,
            depth.get(r[0], (0, None))[0]))
        winner[isin] = best[0]
    out = []
    for r in resolved:
        cnt, latest = depth.get(r[0], (0, None))
        primary = True if not r[3] else winner.get(r[3], r[0]) == r[0]
        out.append((*r, primary, latest, cnt))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    try:
        from dotenv import load_dotenv
        load_dotenv(PIVOT / ".env")
    except ImportError:
        pass
    import psycopg2

    print("fetching official exchange lists…", flush=True)
    off = load_official()
    print(f"  NSE {len(off['nse'])}  NSE-SME {len(off['sme'])}  "
          f"BSE {len(off['bse_cd'])}")

    fin = psycopg2.connect(os.environ["FINANCIALS_DSN"])
    fc = fin.cursor()
    fc.execute('SET statement_timeout="300s"')
    fc.execute("SELECT sc_id, company_slug, company_name, nse_symbol, "
               "bse_code, ticker FROM mc.companies")
    rows = fc.fetchall()
    # Depth + recency per sc_id, off the materialised growth table rather than
    # the 18.3M-row statement_lines (which times out). Enough to pick a
    # primary: where Moneycontrol carries several sc_ids for one security, the
    # duplicates are either empty or a year behind.
    fc.execute("SELECT sc_id, count(*), max(latest_end) "
               "FROM mc.growth_metrics_mat GROUP BY sc_id")
    depth = {r[0]: (r[1], r[2]) for r in fc.fetchall()}
    have_fin = set(depth)
    fin.close()
    print(f"  mc.companies {len(rows)}")

    resolved, stats = resolve_all(rows, off)
    resolved = mark_primary(resolved, depth)
    print("\nROUTES")
    for k, v in sorted(stats.items(), key=lambda kv: -kv[1]):
        print(f"  {k:<32} {v:>6}")

    import collections
    ex = collections.Counter(r[1] for r in resolved)
    isin = sum(1 for r in resolved if r[3])
    fin_n = sum(1 for r in resolved if r[0] in have_fin)
    weak = [r for r in resolved if (r[8] or 0) < 0.6]
    print(f"\nresolved            {len(resolved)}")
    print(f"  by exchange       {dict(ex)}")
    print(f"  with ISIN         {isin} ({100*isin/max(len(resolved),1):.1f}%)")
    print(f"  with financials   {fin_n}")
    prim = sum(1 for r in resolved if r[10])
    print(f"  primary rows      {prim}  (dupes suppressed: {len(resolved)-prim})")
    print(f"  name agreement <0.6 {len(weak)}  (mc slug vs official name)")
    for r in weak[:8]:
        print(f"     {r[2]:<12} slug={r[5][:26]:<26} official={r[4][:34]} "
              f"({r[8]})")

    if a.dry_run:
        print("\n--dry-run: nothing written")
        return 0

    db = psycopg2.connect(os.environ["DATABASE_URL"])
    cur = db.cursor()
    cur.execute(DDL)
    cur.execute("TRUNCATE company_identity")
    cur.executemany(
        "INSERT INTO company_identity (mc_sc_id,verified_exchange,"
        "verified_symbol,isin,verified_name,mc_slug,verified_bse_code,"
        "match_route,match_name_score,verified_at,"
        "mc_is_primary,mc_latest_period,mc_metric_count) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", resolved)
    db.commit()
    cur.execute("SELECT count(*) FROM company_identity")
    print(f"\nwrote company_identity: {cur.fetchone()[0]} rows")
    db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
