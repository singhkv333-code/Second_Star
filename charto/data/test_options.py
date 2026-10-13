"""The option chain (options.py): a synthetic NSE F&O file through the real
parser, so the units, the reads and the live overlay are checked without the
network or a Kite session."""
import csv
import io
import zipfile
from datetime import date, datetime, timedelta

import pytest

import options

COLS = ["TradDt", "FinInstrmTp", "TckrSymb", "XpryDt", "StrkPric", "OptnTp", "FinInstrmNm",
        "ClsPric", "PrvsClsgPric", "UndrlygPric", "SttlmPric", "OpnIntrst", "ChngInOpnIntrst",
        "TtlTradgVol", "NewBrdLotQty"]
LOT = 50


def _zip(trade: str, exp: str, with_future: bool = True) -> bytes:
    rows = []
    yy = exp[2:4]
    if with_future:
        rows.append({"TradDt": trade, "FinInstrmTp": "STF", "TckrSymb": "ACME", "XpryDt": exp,
                     "StrkPric": "", "OptnTp": "", "FinInstrmNm": f"ACME{yy}OCTFUT", "ClsPric": "1003",
                     "PrvsClsgPric": "990", "UndrlygPric": "1000", "SttlmPric": "1003",
                     "OpnIntrst": "500000", "ChngInOpnIntrst": "0", "TtlTradgVol": "100",
                     "NewBrdLotQty": str(LOT)})
    # call OI piles up above the money, put OI below it: max pain lands at 1000
    for k, (ce_oi, pe_oi, ce_px, pe_px) in {
        960: (10, 60, 48.0, 6.0), 980: (20, 80, 31.0, 10.0), 1000: (50, 50, 18.0, 16.0),
        1020: (90, 20, 9.0, 27.0), 1040: (70, 10, 4.0, 42.0)}.items():
        for side, oi, p in (("CE", ce_oi, ce_px), ("PE", pe_oi, pe_px)):
            rows.append({"TradDt": trade, "FinInstrmTp": "STO", "TckrSymb": "ACME", "XpryDt": exp,
                         "StrkPric": f"{k}.00", "OptnTp": side, "FinInstrmNm": f"ACME{yy}OCT{k}{side}",
                         "ClsPric": str(p), "PrvsClsgPric": str(p * 0.9), "UndrlygPric": "1000",
                         "SttlmPric": str(p), "OpnIntrst": str(oi * LOT * 100),
                         "ChngInOpnIntrst": str(oi * LOT * 10), "TtlTradgVol": "250",
                         "NewBrdLotQty": str(LOT)})
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=COLS)
    w.writeheader()
    w.writerows(rows)
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        z.writestr("BhavCopy_NSE_FO_0_0_0_x_F_0000.csv", buf.getvalue())
    return out.getvalue()


@pytest.fixture
def market(monkeypatch):
    today = datetime.now(options.IST).date()
    exp = (today + timedelta(days=10)).isoformat()
    blob = _zip(today.isoformat(), exp)
    monkeypatch.setattr(options, "_download", lambda d: blob)
    monkeypatch.setattr(options, "_days", {})
    monkeypatch.setattr(options, "_miss", {})
    monkeypatch.setattr(options, "_latest", (0.0, None))
    monkeypatch.setattr(options, "_kite_quote", lambda keys: {})
    return exp


def test_eod_chain_reads_in_contracts(market):
    c = options.chain("ACME", strikes=10)
    assert c["available"] and c["source"] == "nse_eod" and c["as_of_label"].startswith("Close")
    r = {x["strike"]: x for x in c["rows"]}
    assert r[1000]["ce"]["oi"] == 5000 and r[1000]["ce"]["oi_chg"] == 500   # shares / lot
    assert r[1000]["ce"]["volume"] == 250                                   # already contracts
    assert c["lot_size"] == LOT and c["spot"] == 1000
    assert c["forward_basis"] == "future" and c["forward"] == 1003
    assert c["atm_strike"] == 1000 and c["step"] == 20
    assert c["max_pain"] == 1000
    assert c["pcr_oi"] == round((60 + 80 + 50 + 20 + 10) / (10 + 20 + 50 + 90 + 70), 2)
    assert c["walls"]["call"][0]["strike"] == 1020 and c["walls"]["put"][0]["strike"] == 980
    assert c["expected_move"]["abs"] == 34.0
    assert 0 < c["atm_iv"] < 100 and r[1000]["ce"]["delta"] > 0 > r[1000]["pe"]["delta"]


def test_slice_digest_and_expiry_words(market):
    c = options.chain("ACME", strikes=1)        # at least 3 a side, centred on the money
    assert [x["strike"] for x in c["rows"]] == [960, 980, 1000, 1020, 1040]
    assert c["strikes_total"] == 5
    d = options.digest(options.chain("ACME"))
    assert "rows" not in d and d["near_money"][0]["strike"] == 960
    assert options.chain("ACME", expiry=market)["expiry"] == market
    assert options.chain("ACME", expiry="not a date")["available"] is False


def test_live_overlay_requotes_and_keeps_oi_change_honest(market, monkeypatch):
    lot = LOT

    def kite(keys):
        out = {"NSE:ACME": {"last_price": 1010.0}}
        for k in keys:
            if k.endswith("1000CE"):
                out[k] = {"last_price": 22.0, "ohlc": {"close": 18.0}, "oi": 6000 * lot,
                          "volume": 40 * lot, "depth": {"buy": [{"price": 21.9}], "sell": [{"price": 22.1}]}}
        return out
    monkeypatch.setattr(options, "_kite_quote", kite)
    c = options.chain("ACME")
    a = next(x for x in c["rows"] if x["strike"] == 1000)
    assert c["source"] == "kite" and c["as_of_label"].startswith("Live")
    assert c["spot"] == 1010.0
    assert a["ce"]["ltp"] == 22.0 and a["ce"]["oi"] == 6000 and a["ce"]["volume"] == 40
    # today's file is the only file: the baseline is the file before it, which
    # does not exist, so the change falls back to the file's own figure
    assert a["ce"]["oi_chg"] in (500, 1000)


def test_honest_refusals(market):
    assert options.chain("NOTLISTED")["available"] is False
    assert "BSE" in options.chain("SENSEX")["reason"]
    assert options.underlying_of("NIFTY 50") == "NIFTY"
    assert options.underlying_of("NIFTY BANK") == "BANKNIFTY"
    assert options.chart_symbol("BANKNIFTY") == "NIFTY BANK"


def test_tool_is_deferred_and_draws_the_card(market):
    import dataserver as server
    assert "get_option_chain" in server.TOOL_GROUPS["options"][1]
    server._card_take()
    out = server.tool_get_option_chain("ACME")
    cards = server._card_take()
    assert cards and cards[0]["kind"] == "option_chain" and cards[0]["available"]
    assert "END-OF-DAY" in out["_note"] and "near_money" in out
    miss = server.tool_get_option_chain("NOTLISTED")
    assert miss["available"] is False and not server._card_take()
