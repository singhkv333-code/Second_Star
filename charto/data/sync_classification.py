"""Sync industry classification for the chart universe into the local store.

Source: mc.companies in the Azure financials DB. The join key is
nse_symbol — the REPAIRED field (the enrich DB's ticker column is
corrupted: 655/670 dup-ticker groups are different companies, so ticker
is never used as an identity key). sector and market_cap are NULL in mc
by design; industry_slug is fully populated and is the classification.

A few symbols carry no nse_symbol in mc; where the company is
unambiguous the classification is corrected by NAME below. LTM stays
unclassified rather than guessed.

Moneycontrol's slug is NOT a controlled vocabulary and is wrong in three
measured ways — see the resolver below, which decides per row between it
and company_profile's cleaner sector rather than trusting either source
outright.

Run:  pivot/.venv/bin/python charto/data/sync_classification.py
      …/sync_classification.py --resolve-only   # no Azure DSN needed:
          re-decides industry from tables already on disk
"""
from __future__ import annotations

import difflib
import json
import re
import sqlite3
import sys
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

# Columns added when the resolver landed. `industry` stays the GROUPING KEY
# every reader already selects on, so nothing downstream had to change; what
# changed is which source that key comes from.
_ADDED = {
    "label":       "TEXT",   # the industry in words — "Asset Management"
    "sector":      "TEXT",   # the broad sector — "Financial Services"
    "industry_mc": "TEXT",   # Moneycontrol's original slug, kept for audit
    "source":      "TEXT",   # 'profile' | 'mc' — which one won, per row
}


# ══════════════════════════════════════════════════════════════════════
# THE RESOLVER
#
# Moneycontrol's industry_slug is not a controlled vocabulary, and it fails
# in three measured ways across this 559-instrument universe:
#
#   1. NAME-SLUG FALLBACK (68 symbols). Where MC has no industry for a
#      company it emits a slug of the company's NAME. 360ONE's "industry"
#      was `360onewam`, an industry of exactly one member — so the peer
#      query returned nothing and the honest tool said "no peers", which
#      was a true statement about a false input.
#   2. SYNONYM FRAGMENTATION. `fertilisers` and `fertilizers` are both
#      present, and split PARADEEP off from CHAMBLFERT/COROMANDEL/FACT on a
#      spelling. Telecom services is fragmented four ways; couriers splits
#      BLUEDART from DELHIVERY.
#   3. WRONG ENTITY. mc.companies.nse_symbol maps some tickers onto a
#      DIFFERENT company, and then the name and the industry are both that
#      other company's: TITAN carried "IAG Company"/glassglassproducts,
#      GESHIP carried "Great Western"/foodprocessing, NATIONALUM carried
#      "National Auto"/autoancillaries.
#
# company_profile already holds a clean sector/industry on a single
# controlled vocabulary, which fixes all three at once — but it has
# identity errors of its OWN, in different rows (it thinks BRITANNIA is
# "Bilcare", J&KBANK is "Canara Bank", CROMPTON is "Bajaj Consumer Care").
# So neither source can simply win. Each row is decided on evidence:
#
#   · the two sources NAME THE SAME COMPANY  -> take the profile industry.
#     This is the ordinary case (401 rows) and it is safe by construction:
#     if both agree on who this is, the better vocabulary should win.
#   · they disagree, but the TICKER reads as the profile's name -> take the
#     profile. This is what rescues TITAN and GESHIP, and it is also what
#     REFUSES Bilcare and Canara Bank, whose names the ticker rejects.
#   · otherwise -> keep Moneycontrol. Unknown is not a licence to guess.
#
# Measured on the current universe: 448 rows resolve from the profile, and
# symbols stranded alone in an "industry" fall from 115 to 54 — the rest
# being genuine one-of-a-kind instruments (India VIX) or non-equities with
# no sector at all (crypto, INR pairs, MCX futures).
# ══════════════════════════════════════════════════════════════════════

_DROP = {"the", "limited", "ltd", "company", "co", "corporation", "corp",
         "india", "indian", "of", "and", "private", "pvt", "plc"}


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _wordsets(name: str) -> list[list[str]]:
    """Both readings of a name. A ticker may spell the boilerplate out
    (ONGC's C is Corporation) or drop it entirely (GESHIP drops Company and
    Limited), so the match is tried against each."""
    w = [x for x in re.split(r"[^a-zA-Z0-9]+", (name or "").lower()) if x]
    kept = [x for x in w if x not in _DROP]
    return [w, kept] if kept and kept != w else [w]


def _ticker_matches_name(symbol: str, name: str) -> bool:
    """True when the ticker reads as word-prefixes of the name, in order.

    NATIONALUM <- National Aluminium, GESHIP <- Great Eastern Shipping,
    ABCAPITAL <- Aditya Birla Capital. Every ticker in this universe is
    built that way, which is precisely why a row naming a DIFFERENT company
    stops matching — "National Auto" cannot produce NATIONALUM.

    The first word may not be skipped. Without that anchor "City Union
    Bank" answers to UNIONBANK by dropping City, which is the one shape of
    false positive that matters here: two real companies, one of them a
    plausible-looking impostor.
    """
    t = _norm(symbol)
    if not t:
        return False

    def walk(i: int, w: int, words: list[str], first: bool) -> bool:
        if i >= len(t):
            return True
        if w >= len(words):
            return False
        if not first and walk(i, w + 1, words, False):
            return True
        word = words[w]
        for ln in range(1, min(len(word), len(t) - i) + 1):
            if t[i:i + ln] == word[:ln] and walk(i + ln, w + 1, words, False):
                return True
        return False

    return any(words and walk(0, 0, words, True) for words in _wordsets(name))


def _names_agree(mc_name: str, long_name: str) -> bool:
    """Do the two sources describe the same company?

    A prefix test, not equality: mc.companies truncates company_name to
    about 15 characters, so "Cholamandalam Investment and Finance" arrives
    as "Chola Invest.".
    """
    a, b = _norm(mc_name), _norm(long_name)
    if not a or not b:
        return False
    k = min(len(a), len(b))
    if k < 4:
        return a == b
    return (b.startswith(a[:k]) or a.startswith(b[:k])
            or difflib.SequenceMatcher(None, a[:18], b[:18]).ratio() > 0.86)


def resolve(db: sqlite3.Connection) -> dict:
    """Recompute industry/label/sector/source for every classified symbol.

    Reads only local tables, so it runs without the Azure DSN — and it is
    idempotent, because it always decides from `industry_mc` rather than
    from whatever it wrote last time.
    """
    have = {r[1] for r in db.execute("PRAGMA table_info(classification)")}
    for col, typ in _ADDED.items():
        if col not in have:
            db.execute(f"ALTER TABLE classification ADD COLUMN {col} {typ}")
    # First run after the migration: MC's slug is whatever `industry` holds.
    db.execute("UPDATE classification SET industry_mc = industry "
               "WHERE industry_mc IS NULL")

    try:
        prof = {r[0]: (r[1], r[2], r[3]) for r in db.execute(
            "SELECT symbol, long_name, sector, industry FROM company_profile")}
    except sqlite3.Error:
        prof = {}          # table absent — every row simply keeps MC

    tally = {"profile": 0, "mc": 0}
    out = []
    for sym, mc_name, mc_ind in db.execute(
            "SELECT symbol, name, industry_mc FROM classification").fetchall():
        long_name, sector, ind = prof.get(sym, (None, None, None))
        take = bool(ind and long_name) and (
            _names_agree(mc_name, long_name)
            or _ticker_matches_name(sym, long_name))
        if take:
            out.append((_norm(ind), ind, sector, "profile", sym))
            tally["profile"] += 1
        else:
            # No label to invent: MC's slug IS the only wording we have.
            out.append((mc_ind, mc_ind, None, "mc", sym))
            tally["mc"] += 1
    db.executemany(
        "UPDATE classification SET industry=?, label=?, sector=?, source=? "
        "WHERE symbol=?", out)
    db.commit()
    return tally


def main() -> None:
    resolve_only = "--resolve-only" in sys.argv
    db = sqlite3.connect(HERE / "charto_bars.db")
    db.executescript(DDL)

    if not resolve_only:
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

        for col, typ in _ADDED.items():
            if col not in {r[1] for r in db.execute(
                    "PRAGMA table_info(classification)")}:
                db.execute(f"ALTER TABLE classification ADD COLUMN {col} {typ}")
        db.execute("DELETE FROM classification")
        # industry starts as MC's slug and industry_mc keeps it; resolve()
        # then decides what industry should actually be.
        db.executemany(
            "INSERT INTO classification (symbol, name, industry, industry_mc) "
            "VALUES (?,?,?,?)",
            [(s, rows[s][0], rows[s][1], rows[s][1]) for s in syms if s in rows])
        db.commit()
        missing = sorted(set(syms) - set(rows))
    else:
        missing = None

    tally = resolve(db)
    n, ind = db.execute(
        "SELECT COUNT(*), COUNT(DISTINCT industry) FROM classification"
    ).fetchone()
    alone = db.execute(
        "SELECT COUNT(*) FROM (SELECT industry FROM classification "
        "GROUP BY industry HAVING COUNT(*)=1)").fetchone()[0]
    db.close()
    print(f"classification: {n} symbols, {ind} industries "
          f"({tally['profile']} from company_profile, {tally['mc']} from "
          f"Moneycontrol); {alone} alone in their industry"
          + ("" if missing is None else f"; unclassified: {missing or 'none'}"))


if __name__ == "__main__":
    main()
