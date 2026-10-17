"""instrument_marks: every listing without a logo gets one from what it is,
and every mark it can name is a file that exists."""
from pathlib import Path

import instrument_marks as im

PREVIEW = Path(__file__).resolve().parents[2] / "chart"

LOGOS = {"ABB": "https://img.logo.dev/new.abb.com?x", "NIFTY 50": "/assets/instruments/nifty-50.svg",
         "USDINR": "/assets/instruments/usdinr.svg", "RELIANCE": "https://reliance/icon.svg"}
NAMES = {"ABB": "ABB India", "RELIANCE": "Reliance Industries Ltd"}
M = lambda ex, kind, name: {"exchange": ex, "kind": kind, "name": name}
MASTER = {
    "NFO:ABB26DECFUT": M("NFO", "future", "ABB Dec 2026 futures"),
    "NFO:ABB1!": M("NFO", "continuous", "ABB continuous futures (front month)"),
    "NFO:NIFTY26OCTFUT": M("NFO", "future", "NIFTY Oct 2026 futures"),
    "NFO:MIDCPNIFTY1!": M("NFO", "continuous", "MIDCPNIFTY continuous futures"),
    "NIFTY MID SELECT": M("NSE", "index", "NIFTY MIDCAP SELECT (MIDCPNIFTY)"),
    "MCX:GOLDM26NOVFUT": M("MCX", "future", "GOLDM Nov 2026 futures"),
    "MCX:CARDAMOM1!": M("MCX", "continuous", "CARDAMOM continuous futures"),
    "NCO:LEAD1!": M("NCO", "continuous", "LEAD continuous futures"),
    "CDS:USDINR26OCTFUT": M("CDS", "future", "USDINR Oct 2026 futures"),
    "CDS:633GS203526DECFUT": M("CDS", "future", "633GS2035 Dec 2026 futures"),
    "BSEMOI": M("BSE", "index", "BSE INDEX BSEMOI"),
    "BSEIPO": M("BSE", "index", "BSE INDEX BSEIPO"),
    "NIFTY PSU BANK": M("NSE", "index", "NIFTY PSU BANK"),
    "BHARATBOND-APR30": M("NSE", "index", "NSE INDEX BHARATBOND-APR30"),
    "BSE:RELIANCE": M("BSE", "equity", "RELIANCE INDUSTRIES LIMITED"),
    "ABSL10BANK": M("NSE", "equity", "BIRLASLAMC - ABSL10BANK ETF"),
    "AASTHA": M("NSE", "equity", "AASTHA SPINTEX"),
    "ABB": M("NSE", "equity", "ABB India"),
}


def test_each_kind_gets_the_mark_of_what_it_is():
    out = im.fill(LOGOS, NAMES, MASTER)
    assert out["NFO:ABB26DECFUT"] == out["NFO:ABB1!"] == LOGOS["ABB"]
    assert out["NFO:NIFTY26OCTFUT"] == LOGOS["NIFTY 50"]
    assert out["NFO:MIDCPNIFTY1!"] == out["NIFTY MID SELECT"] == "/assets/instruments/marks/index-nse.svg"
    assert out["MCX:GOLDM26NOVFUT"] == "/assets/instruments/goldm.svg"
    assert out["MCX:CARDAMOM1!"].endswith("/leaf-mcx.svg")
    assert out["NCO:LEAD1!"].endswith("/metal-mcx.svg")
    assert out["CDS:USDINR26OCTFUT"] == LOGOS["USDINR"]
    assert out["CDS:633GS203526DECFUT"].endswith("/bond-cds.svg")
    assert out["BSEMOI"].endswith("/factor-bse.svg")
    assert out["BSEIPO"].endswith("/index-bse.svg")
    assert out["NIFTY PSU BANK"].endswith("/bank-nse.svg")
    assert out["BHARATBOND-APR30"].endswith("/bond-nse.svg")
    assert out["BSE:RELIANCE"] == LOGOS["RELIANCE"]          # same company, matched by name
    assert out["ABSL10BANK"].endswith("/fund-nse.svg")


def test_a_brand_is_never_invented_or_overwritten():
    out = im.fill(LOGOS, NAMES, MASTER)
    assert "AASTHA" not in out                               # a company with no logo stays bare
    assert out["ABB"] == LOGOS["ABB"]


def test_every_nameable_mark_is_a_real_file():
    out = im.fill(LOGOS, NAMES, MASTER)
    local = {u for u in out.values() if u.startswith("/assets/")}
    local |= {f"{im.URL}/{g}-{t}.svg" for g, t in im.all_marks()}
    local |= {im.commodity_mark(r) for r in ("GOLD", "SILVERMIC", "ZINCMINI", "NATGASMINI",
                                             "BRCRUDEOIL", "ALUMINI", "NICKEL", "ELECDMBL", "KAPAS")}
    missing = [u for u in local if not (PREVIEW / u.lstrip("/")).is_file()]
    assert not missing, missing
