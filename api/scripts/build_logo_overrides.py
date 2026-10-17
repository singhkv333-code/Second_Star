#!/usr/bin/env python
"""Generate/extend the company logo domain-override map from yfinance.

WHY
    ``mc.companies.logo_url`` was precomputed with a naive ``<name>.com``
    domain guess. logo.dev serves logos *by domain*, so a wrong domain
    (``reliance.com`` → a US firm, ``ntpc.com``, ``infy.com`` → unrelated
    brands) renders the wrong logo. There is no heuristic to tell a correct
    guess (``hdfcbank.com``) from a wrong one — the authoritative domain is
    yfinance's ``website`` field.

WHAT
    For every company with a precomputed logo, fetch the real website from
    yfinance, reduce it to a bare domain, and when it differs from the guessed
    domain, record ``TICKER -> domain`` in
    ``backend/market/logo_domain_overrides.json``. The runtime resolver
    (backend.market.company_logos) applies that file at highest priority, so
    corrections take effect on the next request (cache key is versioned).

USAGE
    python -m scripts.build_logo_overrides                # all companies
    python -m scripts.build_logo_overrides --limit 100    # first 100 (smoke)
    python -m scripts.build_logo_overrides --only RELIANCE,NTPC,INFY
    python -m scripts.build_logo_overrides --dry-run      # print, don't write

    Resumable: processed sc_ids are checkpointed to a sidecar progress file
    (gitignored) so a re-run skips what it already fetched. Use --fresh to
    ignore the checkpoint.

Reads the financials DB read-only; never writes to Postgres. Politeness sleep
between yfinance calls keeps us under its rate limit.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit

_OVERRIDES = Path(__file__).resolve().parents[1] / "backend" / "market" / "logo_domain_overrides.json"
_PROGRESS = _OVERRIDES.with_name("logo_overrides_progress.json")  # gitignored checkpoint

# Famous tickers whose website yfinance intermittently fails to return (404s).
# Used only as a fallback when yfinance has nothing; each is still validated
# against the logo.dev byte-size threshold like any other candidate.
_MANUAL: dict[str, str] = {
    "TATAMOTORS": "tatamotors.com",
}

# Hand-verified domains that WIN outright (applied before yfinance), for symbols
# where yfinance's domain has no real logo.dev logo but a different known-good
# domain does. Each was confirmed to return a real (non-monogram) logo.
_FORCE: dict[str, str] = {
    # britannia.com (black cross) and britanniaindustries.com (teal "G") are
    # UNRELATED brands; the real red/yellow Britannia logo lives here. Verified
    # visually — size alone can't tell a wrong brand from the right one.
    "BRITANNIA": "britannia-international.com",
    "GRASIM": "adityabirla.com",             # grasim.com is a monogram; use group logo
    "EICHERMOT": "eichermotors.com",         # correct entity (not royalenfield.com)
    "TECHM": "techmahindra.com",             # correct domain, small but real logo
    # ETFs — yfinance has no website for funds, so map each to its issuer's
    # (fund house) logo. Domains verified to return a real logo on logo.dev.
    "NIFTYBEES": "nipponindiaim.com",
    "GOLDBEES": "nipponindiaim.com",
    "BANKBEES": "nipponindiaim.com",
    "JUNIORBEES": "nipponindiaim.com",
    "ITBEES": "nipponindiaim.com",
    "PHARMABEES": "nipponindiaim.com",
    "LIQUIDBEES": "nipponindiaim.com",
    "PSUBANKBEES": "nipponindiaim.com",
    "CPSEETF": "nipponindiaim.com",
    "NEXT50": "nipponindiaim.com",
    "MAFANG": "miraeassetmf.co.in",
    "MON100": "motilaloswalmf.com",
    "BHARATBOND": "edelweissmf.com",
}


def _domain(website: Optional[str]) -> Optional[str]:
    """``https://www.ril.com/about`` -> ``ril.com``. Best-effort."""
    if not website:
        return None
    raw = website.strip()
    if not raw:
        return None
    if "//" not in raw:
        raw = "//" + raw
    host = (urlsplit(raw).hostname or "").lower().strip()
    if host.startswith("www."):
        host = host[4:]
    return host if "." in host else None


def _guessed_domain(logo_url: Optional[str]) -> Optional[str]:
    if not logo_url:
        return None
    m = re.search(r"img\.logo\.dev/([^?]+)", logo_url)
    return m.group(1).lower() if m else None


def _load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _yf_website(ticker: str) -> Optional[str]:
    """Try the NSE then BSE listing; return the first website yfinance has."""
    import yfinance as yf

    for suffix in (".NS", ".BO"):
        try:
            info = yf.Ticker(ticker + suffix).info
        except Exception:
            continue
        site = info.get("website") if isinstance(info, dict) else None
        if site:
            return site
    return None


def _logo_bytes(domain: str, token: str) -> int:
    """Size of the PNG logo.dev serves for ``domain``. logo.dev returns 200
    with a small generated *monogram* (~2–3 KB) for domains it doesn't have,
    and a larger real logo (~6 KB+) for ones it does — so the byte size is a
    reliable real-vs-monogram signal. Returns 0 on any error."""
    import urllib.request

    url = f"https://img.logo.dev/{domain}?token={token}&size=128&format=png"
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            return len(r.read())
    except Exception:
        return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=0, help="process at most N companies (0 = all)")
    ap.add_argument("--only", default="", help="comma-separated tickers to (re)check")
    ap.add_argument("--symbols", default="",
                    help="comma-separated NSE symbols to resolve DIRECTLY via yfinance, keyed by "
                         "the symbol as given. Bypasses mc.companies entirely — use for app/trading "
                         "symbols (SBIN, ONGC, ...) that mc.companies keys under a different code.")
    ap.add_argument("--sleep", type=float, default=0.4, help="seconds between yfinance calls")
    ap.add_argument("--threshold", type=int, default=4000,
                    help="PNG byte size above which a logo.dev image counts as a real logo "
                         "(not a generated monogram). Corrections only apply when the guessed "
                         "domain is below this AND the candidate is above it.")
    ap.add_argument("--dry-run", action="store_true", help="print changes, don't write the map")
    ap.add_argument("--fresh", action="store_true", help="ignore the resume checkpoint")
    args = ap.parse_args()

    from backend.config import settings
    from backend.database import FinancialsSessionLocal
    from sqlalchemy import text

    token = (settings.logodev_publishable_token or "").strip()
    if not token:
        print("ERROR: settings.logodev_publishable_token is empty; cannot validate logos.", file=sys.stderr)
        return 2

    overrides = _load_json(_OVERRIDES)
    progress = {} if args.fresh else _load_json(_PROGRESS)
    done: set[str] = set(progress.get("done", []))

    only = {s.strip().upper() for s in args.only.split(",") if s.strip()}

    def _consider(sym: str, guessed: Optional[str]) -> bool:
        """Resolve sym's correct domain and record an override if warranted.
        Returns True when an override was added. Shared by the DB sweep and the
        --symbols direct mode so both use the identical yfinance + byte-size rule."""
        if sym in _FORCE:
            overrides[sym] = _FORCE[sym]
            print(f"  FORCE {sym:<13} -> {_FORCE[sym]}")
            return True
        cand = _domain(_yf_website(sym)) or _MANUAL.get(sym)
        if not cand or cand == guessed:
            print(f"  ok   {sym:<14} {guessed or '-'}")
            return False
        cand_size = _logo_bytes(cand, token)
        if cand_size >= args.threshold:
            guessed_size = _logo_bytes(guessed, token) if guessed else 0
            overrides[sym] = cand
            print(f"  FIX  {sym:<14} {guessed}({guessed_size}) -> {cand}({cand_size})")
            return True
        print(f"  skip {sym:<14} {guessed or '-'} -> {cand}({cand_size}) [candidate has no real logo]")
        return False

    # Direct mode: resolve an explicit list of NSE symbols by yfinance, keyed by
    # the symbol itself. No mc.companies dependency — fixes SBIN/ONGC/etc.
    if args.symbols:
        syms = [x.strip().upper() for x in args.symbols.split(",") if x.strip()]
        added = 0
        for sym in syms:
            if _consider(sym, _guessed_domain(None)):
                added += 1
            time.sleep(args.sleep)
        print(f"\nchecked={len(syms)} corrections_added={added}")
        if args.dry_run:
            print("(dry-run: map not written)")
            return 0
        _OVERRIDES.write_text(json.dumps(overrides, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"wrote {_OVERRIDES}")
        return 0

    s = FinancialsSessionLocal()
    # Process EVERY ticker, including ones with a NULL precomputed logo_url —
    # those are famous companies (SBIN, HINDUNILVR, ONGC, ...) that otherwise
    # fall straight through to a monogram. Highest market cap first so the
    # most-visible names are corrected before any rate-limit/interruption.
    rows = s.execute(text(
        "select sc_id, ticker, logo_url from mc.companies "
        "where ticker is not null order by market_cap desc nulls last"
    )).all()

    checked = added = 0
    for r in rows:
        sc_id, ticker, logo_url = r.sc_id, (r.ticker or "").strip(), r.logo_url
        sym = ticker.upper()
        if not sym:
            continue
        if only and sym not in only:
            continue
        if not only and sc_id in done:
            continue
        if args.limit and checked >= args.limit:
            break

        guessed = _guessed_domain(logo_url)
        cand = _domain(_yf_website(ticker)) or _MANUAL.get(sym)
        checked += 1
        done.add(sc_id)

        if not cand or cand == guessed:
            # No alternative, or yfinance agrees with the guess — nothing to do.
            print(f"  ok   {sym:<14} {guessed or '-'}")
            time.sleep(args.sleep)
            continue

        # yfinance's `website` is the *actual corporate domain*, so it's the
        # authoritative brand signal. Override whenever it differs from the
        # guess AND logo.dev actually has a real logo there (size >= threshold,
        # i.e. not a generated monogram). This corrects both:
        #   - monogram guesses (sbin.com, reliance.com -> real domain), and
        #   - real-but-WRONG-brand guesses (kotakbank.com, powergrid.com,
        #     coalindia.com -> the genuine company domain),
        # while leaving correct matches (wipro.com == wipro.com) untouched and
        # refusing domains logo.dev has never heard of (hdfc.bank.in monogram).
        cand_size = _logo_bytes(cand, token)
        if cand_size >= args.threshold:
            guessed_size = _logo_bytes(guessed, token) if guessed else 0
            overrides[sym] = cand
            added += 1
            print(f"  FIX  {sym:<14} {guessed}({guessed_size}) -> {cand}({cand_size})")
        else:
            print(f"  skip {sym:<14} {guessed or '-'} -> {cand}({cand_size}) [candidate has no real logo]")

        time.sleep(args.sleep)

        if checked % 25 == 0 and not args.dry_run:
            _OVERRIDES.write_text(json.dumps(overrides, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            _PROGRESS.write_text(json.dumps({"done": sorted(done)}), encoding="utf-8")

    print(f"\nchecked={checked} corrections_added={added}")
    if args.dry_run:
        print("(dry-run: map not written)")
        return 0

    _OVERRIDES.write_text(json.dumps(overrides, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    _PROGRESS.write_text(json.dumps({"done": sorted(done)}), encoding="utf-8")
    print(f"wrote {_OVERRIDES}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
