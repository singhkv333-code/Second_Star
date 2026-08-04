"""Give every non-equity instrument a mark, the way companies already have one.

The 500 companies resolve through logo.dev by DOMAIN (sync_company_profile.py),
and the chart reads one `logos` map keyed by symbol. Indices, crypto, MCX
futures and INR pairs had no row in it, so they rendered as a bare ticker next
to 500 branded names. This fills the gap with three different answers, because
the three classes are genuinely different things:

  crypto      a real brand, so a real logo — logo.dev on the PROJECT's domain
  fx          a pair of countries, not a brand — two circular flags, composited
  index /     no brand and no country that distinguishes them (every Indian
  commodity   index shares one flag) — a tinted badge carrying a sector glyph

Quality is enforced, not assumed. Every logo.dev URL is fetched once with
`fallback=404` before it is stored: without that parameter the API answers 200
for ANY domain, serving a generated letter-monogram that looks like a logo and
is not one. A domain that fails the gate is reported and skipped rather than
written, so the table never holds a placeholder pretending to be a brand.

Stored in `instrument_logo`, deliberately NOT in `company_profile`: a row there
would make Bitcoin claim a company profile it does not have, and the /company
endpoint would answer for it. Two tables, two facts — "has a logo" and "has a
company behind it" are not the same statement.

Run:  python3 charto/data/sync_instrument_logos.py
      python3 charto/data/sync_instrument_logos.py --check   (verify only)
"""
from __future__ import annotations

import re
import sqlite3
import sys
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).parent
DB_PATH = HERE / "charto_bars.db"
ASSETS = HERE.parent / "preview" / "assets" / "instruments"
# Served by preview/serve.py from charto/preview, so the chart resolves these
# against its own origin exactly as it resolves ./js/*.
ASSET_URL = "/assets/instruments"

# The publishable key already in company_profile's 478 logo URLs (pk_ = safe
# client-side). retina at 256 is 512px of real pixels — the API tops out at
# 800, and 512 is crisp at every size the chart draws a mark (16-32px).
LOGO_TOKEN = "pk_X3WtLGU0RTuTq-o9GTLEsg"
LOGO_FMT = ("https://img.logo.dev/{domain}"
            "?token=" + LOGO_TOKEN + "&size=256&retina=true&format=png")

# symbol base -> the project's OWN domain. XRP is the trap: ripple.com is
# Ripple Labs, a company that is not the asset; xrpl.org is the ledger.
CRYPTO_DOMAIN = {
    "BTC": "bitcoin.org", "ETH": "ethereum.org", "SOL": "solana.com",
    "XRP": "xrpl.org", "ADA": "cardano.org", "DOGE": "dogecoin.com",
    "AVAX": "avax.network", "LINK": "chain.link", "LTC": "litecoin.org",
    "DOT": "polkadot.network", "BNB": "bnbchain.org",
}

# circle-flags (MIT) — circular by construction, which is the shape a paired
# FX mark needs. `eu` in that repo is a git SYMLINK, so a CDN serves the
# literal string "european_union.svg" instead of any SVG; the real name is used.
FLAG_URL = "https://cdn.jsdelivr.net/gh/HatScripts/circle-flags/flags/{c}.svg"
CCY_FLAG = {"USD": "us", "EUR": "european_union", "GBP": "gb",
            "JPY": "jp", "INR": "in"}
FX_PAIRS = {"USDINR": ("USD", "INR"), "EURINR": ("EUR", "INR"),
            "GBPINR": ("GBP", "INR"), "JPYINR": ("JPY", "INR")}

# ── glyph badges ──────────────────────────────────────────────────
# Authored here rather than pulled from an icon set: these are drawn on a
# 24x24 grid to sit inside a 40x40 badge, and they have to read at 16px in a
# list row. Stroke-based, currentColor-free (the badge sets its own colours),
# so one definition serves light and dark.
GLYPH = {
    # broad market — a candle trio; the NAME distinguishes 50 from 500, the
    # mark only has to say "this is an index"
    "index": "M5 15h3v5H5zM10.5 6h3v14h-3zM16 11h3v9h-3z",
    "bank":  "M4 10h16M6 10v8M10 10v8M14 10v8M18 10v8M3 20h18M12 3l9 5H3z",
    "it":    "M9 8 5 12l4 4M15 8l4 4-4 4",
    "pharma": "M8.5 4.5a5 5 0 0 1 7 7l-4 4a5 5 0 0 1-7-7zM8 8l7 7",
    "auto":  "M4 15v-3l2-4h12l2 4v3M4 15h16M4 15v2h3v-2M17 15v2h3v-2M8 12h8",
    "fmcg":  "M5 8h14l-1 12H6zM9 8V5h6v3",
    "metal": "M3 15h18l-2 5H5zM6 10h12l1 5H5z",
    "realty": "M4 20V9l8-5 8 5v11M9 20v-6h6v6",
    "energy": "M13 3 5 14h6l-2 7 8-11h-6z",
    "infra": "M4 20V6M20 20V6M4 6h16M8 20v-6h8v6M4 12h16",
    "media": "M4 5h16v12H4zM10 8l5 3-5 3zM8 20h8",
    "cart":  "M3 5h2l2 9h11l2-7H6M9 19a1 1 0 1 0 2 0 1 1 0 1 0-2 0M16 19a1 1 0 1 0 2 0 1 1 0 1 0-2 0",
    "cube":  "M12 3 4 7v10l8 4 8-4V7zM4 7l8 4 8-4M12 11v10",
    "gold":  "M3 16h18l-1.5 4H4.5zM6 11h12l1.2 5H4.8z",
    "oil":   "M12 3c4 5 6 8 6 11a6 6 0 0 1-12 0c0-3 2-6 6-11z",
    "gas":   "M12 3c1 4-3 5-3 9a3 3 0 0 0 6 0c0-1.5-.6-2.4-1-3 2 .8 3 2.6 3 4.5a5 5 0 0 1-10 0C7 8.5 10 6 12 3z",
    "vix":   "M3 12h3l2.5-7 3.5 14 3-9 2 2h4",
}

# symbol -> (glyph, badge tint). Tints are the instrument's own idea of
# itself, not the app's accent, so a list of them reads as a set of markets.
BADGE = {
    "NIFTY 50": ("index", "#2962ff"), "NIFTY 100": ("index", "#2962ff"),
    "NIFTY 500": ("index", "#2962ff"), "NIFTY NEXT 50": ("index", "#3d6fff"),
    "NIFTY MIDCAP 100": ("index", "#5b7cfa"),
    "NIFTY SMLCAP 100": ("index", "#7b8ff8"),
    "SENSEX": ("index", "#0f7b6c"), "BANKEX": ("bank", "#0f7b6c"),
    "NIFTY BANK": ("bank", "#1f6feb"), "NIFTY PSU BANK": ("bank", "#2f81f7"),
    "NIFTY PVT BANK": ("bank", "#4493f8"),
    "NIFTY FIN SERVICE": ("bank", "#1a7f64"),
    "NIFTY IT": ("it", "#0969da"), "NIFTY PHARMA": ("pharma", "#0d9488"),
    "NIFTY AUTO": ("auto", "#475569"), "NIFTY FMCG": ("fmcg", "#b45309"),
    "NIFTY METAL": ("metal", "#71717a"), "NIFTY REALTY": ("realty", "#92400e"),
    "NIFTY ENERGY": ("energy", "#ea580c"), "NIFTY INFRA": ("infra", "#57534e"),
    "NIFTY MEDIA": ("media", "#db2777"),
    "NIFTY CONSUMPTION": ("cart", "#e11d48"),
    "NIFTY COMMODITIES": ("cube", "#a16207"),
    "INDIA VIX": ("vix", "#dc2626"),
    "GOLD": ("gold", "#b8860b"), "GOLDM": ("gold", "#b8860b"),
    "SILVER": ("gold", "#8e8e93"), "SILVERM": ("gold", "#8e8e93"),
    "COPPER": ("metal", "#b45309"), "ZINC": ("metal", "#64748b"),
    "ALUMINIUM": ("metal", "#94a3b8"),
    "CRUDEOIL": ("oil", "#334155"), "NATURALGAS": ("gas", "#0284c7"),
}

DDL = """
CREATE TABLE IF NOT EXISTS instrument_logo (
  symbol   TEXT PRIMARY KEY,
  logo_url TEXT NOT NULL,
  kind     TEXT,
  source   TEXT)
"""


def _get(url: str, timeout: int = 20) -> tuple[int, bytes]:
    req = urllib.request.Request(url, headers={"User-Agent": "charto/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, b""
    except Exception:
        return 0, b""


def verify_logo(domain: str) -> bool:
    """True only if logo.dev holds a REAL mark for this domain.

    `fallback=404` is the whole point. Without it the API answers 200 for a
    domain that has never existed, returning a generated monogram — so a
    plain status check would happily store a placeholder for every symbol.
    """
    code, _ = _get(LOGO_FMT.format(domain=domain) + "&fallback=404", 15)
    return code == 200


# ── FX: two circular flags, composited ────────────────────────────
def _inner(svg: str, prefix: str) -> str:
    """The flag's drawable content, with its ids namespaced.

    Every circle-flags file declares `<mask id="a">` and references
    `url(#a)`. Two of them in one document is one id defined twice, and the
    second flag then renders through the first flag's mask — which shows up
    as a flag clipped to the wrong circle.
    """
    body = re.sub(r"^<svg[^>]*>|</svg>\s*$", "", svg.strip())
    ids = set(re.findall(r'id="([^"]+)"', body))
    for i in sorted(ids, key=len, reverse=True):
        body = body.replace(f'id="{i}"', f'id="{prefix}{i}"')
        body = body.replace(f"url(#{i})", f"url(#{prefix}{i})")
    return body


def fx_svg(base: str, quote: str, flags: dict[str, str]) -> str:
    """Base flag left, quote flag right, overlapping — the pair convention.

    The left flag is masked with a hole where the right one sits, so the two
    circles stay legible against any background. Painting a background-
    coloured ring instead would be a lie the moment the theme changes.

    Each flag goes in a NESTED <svg> carrying its own viewBox, not in a scaled
    <g>. Every circle-flags file masks itself with a `userSpaceOnUse` circle at
    (256,256) r=256; inside a scaled <g> those coordinates are read in the
    OUTER 40x24 space, so the mask lands off-canvas and the flag renders as a
    fragment. A nested <svg> establishes a fresh user space, which is the one
    the mask was written against.
    """
    a, b = flags[CCY_FLAG[base]], flags[CCY_FLAG[quote]]
    # SQUARE canvas with the pair on a diagonal, not a wide one side by side.
    # Every mark lands in the same 15x15 slot as a company logo, and a 40x24
    # mark letterboxed into that slot renders 15x9 — half the presence of the
    # badges beside it. Diagonal fills a square, which is the shape the list
    # actually has.
    S, D, OFF, GAP = 32.0, 20.0, 12.0, 1.5   # canvas, diameter, offset, notch
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="{S:g}" height="{S:g}" viewBox="0 0 {S:g} {S:g}" '
        f'role="img" aria-label="{base}/{quote}">'
        '<defs><mask id="cut">'
        f'<rect width="{S:g}" height="{S:g}" fill="#fff"/>'
        f'<circle cx="{OFF + D / 2:g}" cy="{OFF + D / 2:g}" '
        f'r="{D / 2 + GAP:g}" fill="#000"/>'
        '</mask></defs>'
        f'<g mask="url(#cut)"><svg x="0" y="0" width="{D:g}" height="{D:g}" '
        f'viewBox="0 0 512 512">{_inner(a, "a_")}</svg></g>'
        f'<svg x="{OFF:g}" y="{OFF:g}" width="{D:g}" height="{D:g}" '
        f'viewBox="0 0 512 512">{_inner(b, "b_")}</svg>'
        "</svg>")


# ── indices / commodities: a tinted badge with a glyph ────────────
def badge_svg(glyph: str, tint: str, label: str) -> str:
    """A filled disc in the instrument's tint, with a stroked glyph on top.

    The disc carries the colour so the mark holds its identity at 16px, where
    a bare stroke glyph turns into grey lint; the glyph is white and stroked
    so it stays readable on every tint without a second palette.
    """
    # width/height as well as viewBox: without an intrinsic size an <img> of
    # this SVG reports naturalWidth 0, and `object-fit: contain` then has no
    # aspect to preserve.
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="40" height="40" '
        f'viewBox="0 0 40 40" role="img" aria-label="{label}">'
        f'<circle cx="20" cy="20" r="20" fill="{tint}"/>'
        '<g transform="translate(8,8)" fill="none" stroke="#fff" '
        'stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round">'
        f'<path d="{GLYPH[glyph]}"/></g></svg>')


def main(check_only: bool = False) -> int:
    con = sqlite3.connect(DB_PATH)
    con.execute(DDL)
    have = {r[0] for r in con.execute("SELECT DISTINCT symbol FROM bars_1d")}
    rows: list[tuple] = []
    skipped: list[str] = []

    # crypto — one verified logo.dev URL per BASE, shared by its listings
    print("crypto (logo.dev, fallback=404 gate):")
    seen: dict[str, str] = {}
    for sym in sorted(have):
        base = (sym[:-4] if sym.endswith("USDT")
                else sym[:-4] if sym.endswith("-USD") else None)
        if base is None or base not in CRYPTO_DOMAIN:
            continue
        dom = CRYPTO_DOMAIN[base]
        if dom not in seen:
            seen[dom] = "ok" if verify_logo(dom) else "no-real-logo"
            print(f"  {base:<6} {dom:<20} {seen[dom]}")
        if seen[dom] != "ok":
            skipped.append(sym)
            continue
        rows.append((sym, LOGO_FMT.format(domain=dom), "crypto", dom))

    # fx — composited circular flags, written as local assets
    ASSETS.mkdir(parents=True, exist_ok=True)
    flags: dict[str, str] = {}
    for c in sorted(set(CCY_FLAG.values())):
        code, body = _get(FLAG_URL.format(c=c))
        if code != 200 or b"<svg" not in body:
            print(f"  flag {c}: HTTP {code} — cannot build pairs using it")
            continue
        flags[c] = body.decode()
    print(f"\nfx (circle-flags, {len(flags)} flags fetched):")
    for sym, (b, q) in FX_PAIRS.items():
        if sym not in have:
            continue
        if CCY_FLAG[b] not in flags or CCY_FLAG[q] not in flags:
            skipped.append(sym)
            continue
        p = ASSETS / f"{sym.lower()}.svg"
        if not check_only:
            p.write_text(fx_svg(b, q, flags))
        rows.append((sym, f"{ASSET_URL}/{p.name}", "fx", "circle-flags"))
        print(f"  {sym:<8} {b}/{q}  -> {p.name}")

    # indices, commodities, volatility — generated badges
    print("\nbadges (generated):")
    n_badge = 0
    for sym, (glyph, tint) in sorted(BADGE.items()):
        if sym not in have:
            continue
        p = ASSETS / f"{_slug(sym)}.svg"
        if not check_only:
            p.write_text(badge_svg(glyph, tint, sym))
        kind = ("commodity" if sym in
                {"GOLD", "GOLDM", "SILVER", "SILVERM", "COPPER", "ZINC",
                 "ALUMINIUM", "CRUDEOIL", "NATURALGAS"}
                else "volatility" if sym == "INDIA VIX" else "index")
        rows.append((sym, f"{ASSET_URL}/{p.name}", kind, "generated"))
        n_badge += 1
    print(f"  {n_badge} badges written to {ASSETS}")

    if check_only:
        print(f"\n--check: {len(rows)} would be stored, {len(skipped)} skipped")
        return 0
    con.executemany(
        "INSERT OR REPLACE INTO instrument_logo VALUES (?,?,?,?)", rows)
    con.commit()
    by_kind = dict(con.execute(
        "SELECT kind, COUNT(*) FROM instrument_logo GROUP BY kind"))
    missing = [r[0] for r in con.execute(
        "SELECT b.symbol FROM (SELECT DISTINCT symbol FROM bars_1d) b "
        "LEFT JOIN instrument_logo i ON b.symbol=i.symbol "
        "LEFT JOIN company_profile p ON b.symbol=p.symbol "
        "WHERE i.symbol IS NULL AND (p.logo_url IS NULL OR p.logo_url='') "
        "ORDER BY b.symbol")]
    con.close()
    print(f"\ninstrument_logo: {len(rows)} rows — {by_kind}")
    if skipped:
        print(f"skipped (no verified logo): {', '.join(skipped)}")
    print(f"still without any mark: {len(missing)}"
          + (f" -> {', '.join(missing[:12])}" if missing else ""))
    return 0


def _slug(sym: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", sym.lower()).strip("-")


if __name__ == "__main__":
    raise SystemExit(main("--check" in sys.argv))
