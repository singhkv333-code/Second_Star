"""Sync the company profile behind Charto's company page into the local store.

Two Postgres databases, joined on sc_id — the Moneycontrol id — and never on
ticker: enrich.company_profile.ticker is corrupted (dup-ticker groups hold
different companies), so it is read as an attribute, never as identity.
The join is verified on the way in: 491 of our 500 symbols resolve and zero
of them disagree on company name across the two databases.

  mc.companies        nse_symbol -> sc_id, company_name, industry_slug, logo
  enrich.company_prof sc_id      -> business summary, market cap, sector,
                                    website, employees, HQ, and raw_info
                                    (CEO, P/B, EV/Sales, EV/EBITDA)
  mc.v_latest_pl      sc_id      -> EPS (consolidated preferred) for P/E

Logos follow Pivot's ladder exactly (backend/market/company_logos.py): the
curated override domain first, then the company's real website domain, and
only then the precomputed mc.companies.logo_url — whose domain was *guessed*
from the name, so it serves the wrong brand for names like RELIANCE
(reliance.com is not ril.com). No guess is ever preferred over a known domain.

Prices are NOT copied: the page reads them from charto's own bars, so the
number on the company page and the number on the chart cannot disagree.

Run:  pivot/.venv/bin/python charto/data/sync_company_profile.py
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

HERE = Path(__file__).parent

DDL = """
CREATE TABLE IF NOT EXISTS company_profile (
  symbol        TEXT PRIMARY KEY,
  sc_id         TEXT,
  name          TEXT,
  long_name     TEXT,
  industry_slug TEXT,
  sector        TEXT,
  industry      TEXT,
  market_cap    REAL,
  summary       TEXT,
  website       TEXT,
  employees     INTEGER,
  city          TEXT,
  country       TEXT,
  logo_url      TEXT,
  eps           REAL,
  eps_basis     TEXT,
  eps_period    TEXT,
  ceo           TEXT,
  pb            REAL,
  ev_sales      REAL,
  ev_ebitda     REAL,
  synced_at     INTEGER
);
"""

PIVOT_MARKET = HERE.parents[1] / "pivot" / "backend" / "market"
# Pivot's publishable logo.dev token (backend/config.py default). Publishable
# by design; attribution is the Logo.dev link the page footer carries.
LOGO_TOKEN = "pk_X3WtLGU0RTuTq-o9GTLEsg"


def _domain(website: str | None) -> str | None:
    """Bare domain out of a website string: https://www.ril.com/x -> ril.com."""
    from urllib.parse import urlsplit
    raw = (website or "").strip()
    if not raw:
        return None
    host = (urlsplit(raw if "//" in raw else "//" + raw).hostname or "").lower()
    host = host[4:] if host.startswith("www.") else host
    return host if "." in host else None


def _logo(sym: str, website: str | None, precomputed: str | None,
          overrides: dict) -> str | None:
    dom = overrides.get(sym.upper()) or _domain(website)
    if dom:
        return f"https://img.logo.dev/{dom}?token={LOGO_TOKEN}&size=128&format=png"
    return precomputed or None


def _ceo(officers) -> str | None:
    """First officer whose title reads as chief executive — Pivot's rule."""
    for o in officers or []:
        title = (o.get("title") or "").lower()
        if any(k in title for k in ("ceo", "chief executive", "managing director",
                                    "md & ", "& md")):
            return o.get("name")
    return (officers[0].get("name") if officers else None)


def main() -> None:
    import psycopg2
    from dotenv import dotenv_values

    env = dotenv_values(HERE.parents[1] / "pivot" / ".env")
    syms = json.loads((HERE / "symbols.json").read_text())

    fin = psycopg2.connect(env["FINANCIALS_DSN"])
    fc = fin.cursor()
    fc.execute(
        "SELECT nse_symbol, sc_id, company_name, industry_slug, logo_url "
        "FROM mc.companies WHERE nse_symbol = ANY(%s) AND is_active IS NOT FALSE",
        (syms,))
    mc = {r[0]: r[1:] for r in fc.fetchall()}
    sc_ids = [v[0] for v in mc.values()]

    # EPS: consolidated is the group's earnings and the basis a P/E is normally
    # quoted on; standalone is the fallback when no consolidated statement runs
    fc.execute(
        "SELECT sc_id, basis, period_label, value_numeric FROM mc.v_latest_pl "
        "WHERE sc_id = ANY(%s) AND line_item = 'Basic EPS (Rs.)' "
        "AND value_numeric IS NOT NULL", (sc_ids,))
    eps: dict[str, tuple] = {}
    for sc, basis, period, val in fc.fetchall():
        if basis == "consolidated" or sc not in eps:
            eps[sc] = (float(val), basis, period)
    fin.close()

    enr = psycopg2.connect(env["ENRICH_DSN"])
    ec = enr.cursor()
    ec.execute(
        "SELECT sc_id, company_name, long_name, long_business_summary, "
        "market_cap, sector, industry, website, full_time_employees, city, "
        "country, raw_info FROM enrich.company_profile WHERE sc_id = ANY(%s)",
        (sc_ids,))
    en = {r[0]: r[1:] for r in ec.fetchall()}
    enr.close()

    overrides = {k.upper(): v.strip().lower()
                 for k, v in json.loads(
                     (PIVOT_MARKET / "logo_domain_overrides.json").read_text()).items()
                 if not k.startswith("_") and isinstance(v, str)}

    rows, disagree = [], []
    now = int(time.time())
    for sym, (sc, cname, ind_slug, logo) in mc.items():
        e = en.get(sc)
        if e:
            # the join is only trustworthy while both sides name the same
            # company — a disagreement is reported, never silently written
            a = "".join(c for c in (cname or "").lower() if c.isalnum())[:9]
            b = "".join(c for c in (e[0] or "").lower() if c.isalnum())[:9]
            if a and b and a != b:
                disagree.append((sym, cname, e[0]))
                e = None
        ep = eps.get(sc) or (None, None, None)
        website = e[6] if e else None
        # yfinance's own info blob, already stored by the enrichment job — the
        # only place CEO and the EV ratios live. A dict on some drivers, JSON
        # text on others; anything missing stays null rather than defaulted.
        raw = e[10] if e else None
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except Exception:
                raw = None
        raw = raw if isinstance(raw, dict) else {}
        rows.append((
            sym, sc, cname, (e[1] if e else None), ind_slug,
            (e[4] if e else None), (e[5] if e else None),
            (float(e[3]) if e and e[3] is not None else None),
            (e[2] if e else None), website,
            (int(e[7]) if e and e[7] is not None else None),
            (e[8] if e else None), (e[9] if e else None),
            _logo(sym, website, logo, overrides), ep[0], ep[1], ep[2],
            _ceo(raw.get("companyOfficers")),
            raw.get("priceToBook"), raw.get("enterpriseToRevenue"),
            raw.get("enterpriseToEbitda"), now))

    db = sqlite3.connect(HERE / "charto_bars.db")
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("DROP TABLE IF EXISTS company_profile")   # schema evolves here
    db.executescript(DDL)
    db.executemany(
        "INSERT INTO company_profile VALUES (" + ",".join("?" * 22) + ")", rows)
    db.commit()
    n, s, m, p, c, lo = db.execute(
        "SELECT COUNT(*), COUNT(summary), COUNT(market_cap), COUNT(eps), "
        "COUNT(ceo), COUNT(logo_url) FROM company_profile").fetchone()
    db.close()

    missing = sorted(set(syms) - set(mc))
    print(f"company_profile: {n} symbols · {s} summaries · {m} market caps · "
          f"{p} EPS · {c} CEOs · {lo} logos")
    print(f"no Moneycontrol row: {len(missing)} {missing or ''}")
    print(f"name disagreements (profile withheld): {len(disagree)} {disagree or ''}")


if __name__ == "__main__":
    main()
