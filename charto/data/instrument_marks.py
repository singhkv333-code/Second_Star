"""A mark for every instrument the picker lists, derived from what it IS.

sync_instrument_logos.py stores marks for the ~40 instruments that have bars;
the master lists ~7,700, and 3,000 of them (BSE indices, every future, MCX and
NCDEX contracts, G-sec futures, ETFs) reached the search as a bare initial.
None of those need a new brand — each already says what it is:

  a future / continuous   is its underlying: ABB26DECFUT wears ABB's logo,
                          NIFTY's wears NIFTY 50's, GOLDM's the gold badge
  an index                is its exchange plus its theme: a badge in the
                          exchange's tint carrying the sector's glyph
  a commodity             is its metal, energy or crop
  an ETF / fund unit      is a fund, in its exchange's tint
  a BSE listing           is the same company as its NSE listing, matched by
                          NAME (tickers differ across exchanges and collide)

Derived at serve time from the master, so nothing is written to the store and
a new listing gets its mark the day it appears. The badges are static files
under preview/assets/instruments/marks/ (nginx serves preview/ off disk), all
of them written by `python3 instrument_marks.py`; test_instrument_marks.py
checks that every URL this module can return names a file that exists.

An equity with no logo anywhere stays unmarked: a company is a brand, and a
generated badge standing in for one would be a placeholder pretending to be it.
"""
from __future__ import annotations

import re
from pathlib import Path

from sync_instrument_logos import GLYPH as _BASE_GLYPH, badge_svg

ASSETS = Path(__file__).parent.parent / "preview" / "assets" / "instruments" / "marks"
URL = "/assets/instruments/marks"
BASE = "/assets/instruments"

# Glyphs beyond sync_instrument_logos' sector set, on the same 24x24 grid.
GLYPH = {
    **_BASE_GLYPH,
    "bond":   "M6 3h9l3 3v15H6zM15 3v3h3M9 10h6M9 13h6M9 16h4",
    "fund":   "M12 3v9h9A9 9 0 1 1 12 3zM15 3.5A9 9 0 0 1 20.5 9H15z",
    "leaf":   "M5 19C5 10 11 5 20 4c-1 9-6 15-15 15zM5 19l8-8",
    "globe":  "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zM3 12h18M12 3c3 3 3 15 0 18M12 3c-3 3-3 15 0 18",
    "factor": "M4 17l5-5 4 3 7-8M15 7h5v5",
}

# One tint per venue, so a list of marks says where each thing trades. NSE's
# blue and BSE's teal are the ones the hand-made NIFTY 50 / SENSEX badges use.
TONE = {"nse": "#2962ff", "bse": "#0f7b6c", "mcx": "#b45309", "cds": "#475569"}
VENUE_TONE = {"NSE": "nse", "NFO": "nse", "NSEIX": "nse", "BSE": "bse",
              "BFO": "bse", "MCX": "mcx", "NCO": "mcx", "CDS": "cds", "BCD": "cds"}


def _mark(glyph: str, tone: str) -> str:
    return f"{URL}/{glyph}-{tone}.svg"


# ── indices: the theme from the name ─────────────────────────────
# First match wins, so the specific comes before the general ("NIFTY PSU BANK"
# is a bank before it is a PSU; "BHARATBOND" a bond before anything).
# BSE's index codes say little in letters (BSEMOI is momentum, BSEPBI private
# banks), so they are named here rather than guessed at.
_BSE_CODE = {"BSELVI": "factor", "BSEMOI": "factor", "BSEQUI": "factor",
             "BSEDSI": "factor", "BSEEVI": "factor", "BSEPBI": "bank",
             "BSEPSU": "infra", "BSEFMC": "fmcg", "BSE CD": "cart",
             "BSE HC": "pharma", "OILGAS": "oil", "INDSTR": "infra",
             "MFG": "infra", "CARBON": "leaf", "GREENX": "leaf",
             "DOL30": "globe", "DOL100": "globe", "DOL200": "globe"}
_INDEX_RULES = [
    (r"VIX", "vix"),
    (r"BHARATBOND|G-?SEC|GILT|\bBOND|\bSDL\b|LIQUID|\bDEBT|\d+-\d+ ?YR|TBILL|T-BILL|"
     r"\bGS\b", "bond"),
    (r"-NAV\b|\bBEES\b|\bETF\b", "fund"),
    (r"HANGSENG|HANG SENG|S&P|NASDAQ|\bUSD?\b|GLOBAL|TAIWAN|JAPAN|CHINA|\bFPI\b|"
     r"TOURISM", "globe"),
    (r"ESG|CARBON|GREEN|RURAL", "leaf"),
    (r"BULLDEX|GOLDEX|SILVDEX|\bGOLD\b|\bSILVER\b", "gold"),
    (r"CRUDEX|\bOIL\b", "oil"),
    (r"METLDEX|COPRDEX|METAL|\bMETL\b", "metal"),
    (r"COMPDEX|COMMOD", "cube"),
    (r"BANK|BANKEX|\bPSB\b|\bBNKS\b", "bank"),
    (r"FIN|INSUR|CAPITAL MKT|CAPITAL MARKET|FINANCIAL|NBFC", "bank"),
    (r"\bIT\b|TECK|TECH|DIGITAL|INTERNET", "it"),
    (r"PHARMA|HEALTH|HLTH", "pharma"),
    (r"\bAUTO|\bEV\b|MOBILITY|TRANSPORT|LOGIS", "auto"),
    (r"FMCG", "fmcg"),
    (r"CONSUM|DURABLE|CONSR|CDGS", "cart"),
    (r"REALTY|HOUSING", "realty"),
    (r"ENERGY|POWER|\bGAS\b|\bENRG|UTIL", "energy"),
    (r"INFRA|MANUF|\bMFG\b|INDUSTR|CAPITAL GOODS|CAPGOODS|\bCG\b|CPSE|\bPSE\b|DEFENCE|"
     r"RAIL|CHEMICAL", "infra"),
    (r"MEDIA|TELECOM|\bTELCO", "media"),
    (r"MOMENT|MOMNTM|QUAL|QLTY|VALUE|ALPHA|LOW ?VOL|EQUAL|\bEQL\b|DIVIDEND|DIV\d|\bDIV\b|"
     r"GROWTH|FACTOR|\bMQ|MQVLV|TMMQ|HIGHBETA|\bLEV\b|\bINV\b|\bLIQ\b", "factor"),
]
_INDEX_RX = [(re.compile(p), g) for p, g in _INDEX_RULES]


def index_glyph(name: str) -> str:
    n = re.sub(r"^(NSE|BSE) INDEX ", "", name.upper()).strip()
    if n in _BSE_CODE:
        return _BSE_CODE[n]
    for rx, g in _INDEX_RX:
        if rx.search(n):
            return g
    return "index"


# ── commodities: the root contract ────────────────────────────────
# Existing hand-tinted badges first; the rest by what the contract is made of.
_COMMODITY_FILE = {
    "GOLD": "gold", "GOLDM": "goldm", "GOLDGUINEA": "gold", "GOLDPETAL": "gold",
    "GOLDTEN": "gold", "SILVER": "silver", "SILVERM": "silverm",
    "SILVERMIC": "silverm", "COPPER": "copper", "ZINC": "zinc",
    "ZINCMINI": "zinc", "ALUMINIUM": "aluminium", "ALUMINI": "aluminium",
    "CRUDEOIL": "crudeoil", "CRUDEOILM": "crudeoil", "BRCRUDEOIL": "crudeoil",
    "NATURALGAS": "naturalgas", "NATGASMINI": "naturalgas",
}
_METALS = {"LEAD", "LEADMINI", "NICKEL", "NICKELMINI", "STEEL", "STEELREBAR"}
_ENERGY = {"ELECDMBL", "ELECMBL", "ELECTRICITY", "COAL"}


def commodity_mark(root: str) -> str:
    if root in _COMMODITY_FILE:
        return f"{BASE}/{_COMMODITY_FILE[root]}.svg"
    if root in _METALS:
        return _mark("metal", "mcx")
    if root in _ENERGY:
        return _mark("energy", "mcx")
    if root.startswith(("GOLD", "SILVER")):
        return _mark("gold", "mcx")
    if "CRUDE" in root:
        return _mark("oil", "mcx")
    # cotton, cardamom, mentha oil, castor seed, guar, jeera, kapas, sugar …
    # the agricultural contracts are the long tail, and every one is a crop
    return _mark("leaf", "mcx")


# ── derivatives: the underlying ───────────────────────────────────
_DERIV = re.compile(r"^(?:[A-Z]+:)?(?P<root>.+?)(?:\d{2}[A-Z]{3}FUT|[12]!)$")
# F&O roots that are an index under another name.
_INDEX_ROOT = {"NIFTY": "NIFTY 50", "BANKNIFTY": "NIFTY BANK",
               "FINNIFTY": "NIFTY FIN SERVICE", "MIDCPNIFTY": "NIFTY MID SELECT",
               "NIFTYNXT50": "NIFTY NEXT 50", "SENSEX": "SENSEX",
               "BANKEX": "BANKEX", "SENSEX50": "SENSEX", "FOCIT": "SENSEX"}
_FX = {"USDINR", "EURINR", "GBPINR", "JPYINR"}


def _norm_name(n: str) -> str:
    n = re.sub(r"[^A-Z0-9 ]", " ", n.upper())
    n = re.sub(r"\b(LIMITED|LTD|THE|CO|COMPANY|CORPORATION|CORP|INDIA|INC)\b", " ", n)
    return " ".join(n.split())


def fill(logos: dict[str, str], names: dict[str, str],
         master: dict[str, dict]) -> dict[str, str]:
    """`logos` plus a derived mark for every master instrument without one.

    `master` is dataserver._master()'s map: sid -> {exchange, kind, name, ...}.
    Never overwrites: a stored logo (a real brand) always outranks a derived one.
    """
    out = dict(logos)
    by_name: dict[str, str] = {}
    for sym, url in logos.items():
        n = names.get(sym)
        if n and ":" not in sym:
            by_name.setdefault(_norm_name(n), url)

    def spot(sym: str) -> str | None:
        """The mark of a spot instrument, derived if it has to be."""
        if out.get(sym):
            return out[sym]
        m = master.get(sym)
        if m and m.get("kind") == "index":
            return _mark(index_glyph(m.get("name") or sym),
                         _tone(m.get("exchange")))
        return None

    for sym, m in master.items():
        if out.get(sym):
            continue
        ex, kind = m.get("exchange") or "", m.get("kind") or ""
        name = (m.get("name") or names.get(sym) or sym)
        tone = _tone(ex)
        url = None
        if kind in ("future", "continuous"):
            g = _DERIV.match(sym)
            root = g.group("root") if g else sym.split(":")[-1]
            if ex in ("MCX", "NCO"):
                url = commodity_mark(root)
            elif ex in ("CDS", "BCD"):
                url = out.get(root) if root in _FX else _mark("bond", "cds")
            else:
                url = (spot(_INDEX_ROOT[root]) if root in _INDEX_ROOT
                       else spot(root)) or None
        elif kind == "index":
            url = _mark(index_glyph(name), tone)
        elif kind == "equity":
            if ":" in sym:          # BSE:XYZ — the same company's NSE mark
                url = out.get(sym.split(":", 1)[1]) or by_name.get(_norm_name(name))
            # "BIRLASLAMC - ABSL10BANK": an AMC's own listing is one of its funds
            if not url and re.search(r"\bETF\b|\bBEES\b|MUTUAL FUND|\bFUND\b|-NAV\b|AMC - ",
                                     name.upper()):
                url = _mark("fund", tone)
        if url:
            out[sym] = url
    return out


def _tone(exchange: str | None) -> str:
    return VENUE_TONE.get((exchange or "NSE").upper(), "nse")


def all_marks() -> list[tuple[str, str]]:
    """Every (glyph, tone) this module can name, i.e. every file it needs."""
    return [(g, t) for g in GLYPH for t in TONE]


def write_assets() -> int:
    ASSETS.mkdir(parents=True, exist_ok=True)
    for g, t in all_marks():
        (ASSETS / f"{g}-{t}.svg").write_text(
            badge_svg(g, TONE[t], f"{g} {t}", glyphs=GLYPH))
    return len(all_marks())


if __name__ == "__main__":
    print(f"{write_assets()} marks written to {ASSETS}")
