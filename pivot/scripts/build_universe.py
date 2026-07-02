"""Build the EXTENDED tradeable universe from the Kite NSE cash dump.

Outputs (all derived from real exchange data — nothing hand-invented):
  scripts/strategy_research/v3/_cache/nse_equities.csv   all plain-series NSE
                                                         equities (~2,700)
  scripts/strategy_research/v3/_cache/nse_etfs.csv       all NSE ETFs (~240)
  backend/view_markets/etf_catalog.json                  curated per-category
                                                         ETF picks, each one
                                                         VERIFIED live: exists
                                                         in the exchange dump,
                                                         has a recent yfinance
                                                         close, and the most
                                                         liquid candidate in
                                                         its category wins
                                                         (20-day median traded
                                                         value, ₹ cr).

Why: the research/expression universe was a manually-dropped NIFTY-500 CSV and
the codebase knew ~5 ETF tickers. The Kite dump has every listed stock + ETF;
this script snapshots it into files the offline pipeline can use, and builds
the ETF substitution catalog that the affordability engine (min ₹ entries)
depends on.

Run:  python -m scripts.build_universe            (from the pivot/ dir)
      python -m scripts.build_universe --fetch-bars   (also cache ETF price bars)

The Kite dump is cached to _cache/nse_cash_dump.json; pass --refresh-dump to
re-pull it (needs a live Kite session).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date
from typing import Any, Optional

import pandas as pd

from scripts.strategy_research.v3 import universe as v3u

_CACHE = v3u.CACHE_DIR
DUMP_JSON = os.path.join(_CACHE, "nse_cash_dump.json")
CATALOG_JSON = os.path.join(
    os.path.dirname(__file__), "..", "backend", "view_markets", "etf_catalog.json"
)

# Candidate tradingsymbols per category, preference-ordered by brand/AUM
# knowledge. Every candidate is VERIFIED against the exchange dump + yfinance
# before it can ship; among the verified, the highest 20-day median traded
# value wins — so a wrong guess here can never reach the catalog.
_ETF_CANDIDATES: dict[str, dict[str, Any]] = {
    "nifty50":       {"candidates": ["NIFTYBEES", "SETFNIF50", "HDFCNIFTY", "UTINIFTETF", "ICICINIFTY"],
                      "tracks": "Nifty 50", "matches": ["index", "largecap", "nifty"]},
    "next50":        {"candidates": ["JUNIORBEES", "SETFNN50", "NEXT50IETF"],
                      "tracks": "Nifty Next 50", "matches": ["index", "largecap"]},
    "midcap":        {"candidates": ["MID150BEES", "HDFCMID150", "MIDCAPIETF", "MIDCAPETF"],
                      "tracks": "Nifty Midcap 150", "matches": ["midcap"]},
    "smallcap":      {"candidates": ["HDFCSML250", "MOSMALL250", "GROWWSC250"],
                      "tracks": "Nifty Smallcap 250", "matches": ["smallcap"]},
    "bank":          {"candidates": ["BANKBEES", "BANKIETF", "SETFNIFBK", "BANKETF"],
                      "tracks": "Nifty Bank", "matches": ["Banks", "Financial Services", "bank"]},
    "psu_bank":      {"candidates": ["PSUBNKBEES", "PSUBANK", "PSUBNKIETF"],
                      "tracks": "Nifty PSU Bank", "matches": ["PSU Banks", "psu bank"]},
    "it":            {"candidates": ["ITBEES", "ITIETF", "ITETF", "SBIETFIT", "AXISTECETF"],
                      "tracks": "Nifty IT", "matches": ["Information Technology", "IT", "software"]},
    "pharma":        {"candidates": ["PHARMABEES"],
                      "tracks": "Nifty Pharma", "matches": ["Pharmaceuticals", "pharma"]},
    "healthcare":    {"candidates": ["HEALTHIETF", "MOHEALTH", "HEALTHY", "HEALTHADD"],
                      "tracks": "Nifty Healthcare", "matches": ["Healthcare", "hospitals"]},
    "auto":          {"candidates": ["AUTOBEES", "AUTOIETF"],
                      "tracks": "Nifty Auto", "matches": ["Automobiles", "auto"]},
    "fmcg":          {"candidates": ["FMCGIETF"],
                      "tracks": "Nifty FMCG", "matches": ["FMCG", "consumer staples"]},
    "consumption":   {"candidates": ["CONSUMBEES", "CONSUMIETF", "AXISCETF"],
                      "tracks": "Nifty India Consumption", "matches": ["Consumer", "consumption", "rural"]},
    "metal":         {"candidates": ["METALIETF", "GROWWMETAL", "METAL"],
                      "tracks": "Nifty Metal", "matches": ["Metals & Mining", "metal"]},
    "energy":        {"candidates": ["MOENERGY", "ENERGY"],
                      "tracks": "Nifty Energy", "matches": ["Energy", "power", "utilities"]},
    "oil_gas":       {"candidates": ["OILIETF"],
                      "tracks": "Nifty Oil & Gas", "matches": ["Oil Gas & Consumable Fuels", "oil"]},
    "cpse":          {"candidates": ["CPSEETF"],
                      "tracks": "Nifty CPSE", "matches": ["PSU", "public sector"]},
    "infra":         {"candidates": ["INFRABEES", "INFRAIETF", "MOINFRA"],
                      "tracks": "Nifty Infrastructure", "matches": ["Infrastructure", "Construction", "capital goods"]},
    "realty":        {"candidates": ["MOREALTY", "GROWWRLTY"],
                      "tracks": "Nifty Realty", "matches": ["Realty", "real estate"]},
    "defence":       {"candidates": ["MODEFENCE", "GROWWDEFNC"],
                      "tracks": "Nifty India Defence", "matches": ["Defence", "Aerospace & Defense", "defense"]},
    "manufacturing": {"candidates": ["MAKEINDIA", "MANUFGBEES", "MOMGF"],
                      "tracks": "Nifty India Manufacturing", "matches": ["Manufacturing", "Industrial Manufacturing", "make in india"]},
    "ev":            {"candidates": ["EVINDIA", "EVIETF", "GROWWEV"],
                      "tracks": "Nifty EV & New Age Automotive", "matches": ["EV", "electric vehicle", "new age automotive"]},
    "internet":      {"candidates": ["GROWWNET", "INTERNET"],
                      "tracks": "Nifty India Internet", "matches": ["internet", "digital", "platforms", "fintech"]},
    "capital_markets": {"candidates": ["MOCAPITAL", "GROWWCAPM"],
                      "tracks": "Nifty Capital Markets", "matches": ["capital markets", "exchanges", "brokers"]},
    "railways":      {"candidates": ["GROWWRAIL"],
                      "tracks": "Nifty India Railways PSU", "matches": ["railways"]},
    "power":         {"candidates": ["GROWWPOWER"],
                      "tracks": "Nifty Power", "matches": ["Power", "utilities"]},
    "tourism":       {"candidates": ["MOTOUR"],
                      "tracks": "Nifty India Tourism", "matches": ["tourism", "hotels", "aviation"]},
    "gold":          {"candidates": ["GOLDBEES", "GOLDCASE", "AXISGOLD", "GOLDIETF"],
                      "tracks": "Domestic gold price", "matches": ["gold", "bullion"]},
    "silver":        {"candidates": ["SILVERBEES", "SILVERIETF", "SILVERADD"],
                      "tracks": "Domestic silver price", "matches": ["silver"]},
    "nasdaq100":     {"candidates": ["MON100"],
                      "tracks": "Nasdaq 100 (INR)", "matches": ["us tech", "nasdaq", "global tech", "AI"]},
    "faang":         {"candidates": ["MAFANG"],
                      "tracks": "NYSE FANG+ (INR)", "matches": ["us tech", "faang", "AI"]},
    "sp500":         {"candidates": ["MASPTOP50"],
                      "tracks": "S&P 500 Top 50 (INR)", "matches": ["us equities", "s&p"]},
    "momentum":      {"candidates": ["MOMOMENTUM", "MOM30IETF", "MOMENTUM30", "HDFCMOMENT"],
                      "tracks": "Nifty 200 Momentum 30", "matches": ["momentum factor"]},
    "quality":       {"candidates": ["MOQUALITY", "QUAL30IETF", "SBIETFQLTY", "HDFCQUAL"],
                      "tracks": "Nifty 200 Quality 30", "matches": ["quality factor"]},
    "value":         {"candidates": ["MOVALUE", "NV20IETF", "NV20"],
                      "tracks": "Nifty 500 Value 50 / NV20", "matches": ["value factor"]},
    "low_vol":       {"candidates": ["LOWVOLIETF", "MOLOWVOL", "HDFCLOWVOL", "LOWVOL"],
                      "tracks": "Nifty 100 Low Volatility 30", "matches": ["low volatility", "defensive"]},
    "alpha":         {"candidates": ["ALPHAETF", "ALPHA", "MOALPHA50"],
                      "tracks": "Nifty Alpha 50", "matches": ["alpha factor"]},
    "liquid":        {"candidates": ["LIQUIDBEES", "LIQUIDCASE", "LIQUIDETF", "LIQUIDADD"],
                      "tracks": "Overnight rate (cash parking)", "matches": ["cash", "liquid", "idle"]},
    "gilt":          {"candidates": ["LTGILTBEES"],
                      "tracks": "8-13y G-sec", "matches": ["gilt", "bonds", "duration", "rates"]},
}


def _load_dump(refresh: bool) -> list[dict]:
    if refresh or not os.path.exists(DUMP_JSON):
        from backend.kite.auth import get_kite_instance
        kite = get_kite_instance()
        ins = kite.instruments("NSE")
        with open(DUMP_JSON, "w") as f:
            json.dump(ins, f, default=str)
        print(f"Pulled fresh Kite NSE dump: {len(ins)} rows")
        return ins
    with open(DUMP_JSON) as f:
        return json.load(f)


def _is_etf(row: dict) -> bool:
    nm = (row.get("name") or "").upper()
    ts = row["tradingsymbol"]
    if "INAV" in ts:                      # indicative-NAV quote lines, not funds
        return False
    return "ETF" in nm or ts.endswith("BEES") or "BEES" in nm


def _split_dump(ins: list[dict]) -> tuple[pd.DataFrame, pd.DataFrame]:
    eq = [i for i in ins if i.get("segment") == "NSE"
          and i.get("instrument_type") == "EQ"
          and "-" not in i["tradingsymbol"]          # drop BE/SM/ST… series lines
          and "INAV" not in i["tradingsymbol"]]
    etfs = [i for i in eq if _is_etf(i)]
    etf_syms = {i["tradingsymbol"] for i in etfs}
    stocks = [i for i in eq if i["tradingsymbol"] not in etf_syms]
    eq_df = pd.DataFrame(
        [{"Symbol": i["tradingsymbol"], "Name": i.get("name") or ""} for i in stocks]
    ).sort_values("Symbol")
    etf_df = pd.DataFrame(
        [{"Symbol": i["tradingsymbol"], "Name": i.get("name") or ""} for i in etfs]
    ).sort_values("Symbol")
    return eq_df, etf_df


def _verify_candidates(all_syms: set[str]) -> dict[str, Any]:
    """For every category, verify candidates live (recent yfinance close +
    20-day median traded value) and pick the most liquid verified one."""
    import yfinance as yf

    wanted: list[str] = []
    for spec in _ETF_CANDIDATES.values():
        wanted.extend(s for s in spec["candidates"] if s in all_syms)
    wanted = list(dict.fromkeys(wanted))
    print(f"Verifying {len(wanted)} candidate ETFs via yfinance ...")
    raw = yf.download([f"{s}.NS" for s in wanted], period="3mo",
                      auto_adjust=True, progress=False, group_by="column")
    close = raw["Close"] if "Close" in raw.columns.get_level_values(0) else pd.DataFrame()
    vol = raw["Volume"] if "Volume" in raw.columns.get_level_values(0) else pd.DataFrame()

    stats: dict[str, dict] = {}
    for s in wanted:
        t = f"{s}.NS"
        if t not in close.columns:
            continue
        c = close[t].dropna()
        if c.empty or (pd.Timestamp.now() - c.index[-1]).days > 10:
            continue
        v = vol[t].reindex(c.index).fillna(0.0) if t in vol.columns else None
        adv_cr = float((c * v).tail(20).median() / 1e7) if v is not None else 0.0
        stats[s] = {"last_price": round(float(c.iloc[-1]), 2),
                    "as_of": str(c.index[-1].date()),
                    "adv_cr": round(adv_cr, 2)}

    catalog: dict[str, Any] = {}
    for cat, spec in _ETF_CANDIDATES.items():
        verified = [s for s in spec["candidates"] if s in stats]
        if not verified:
            print(f"  !! {cat}: no candidate verified — category omitted")
            continue
        best = max(verified, key=lambda s: stats[s]["adv_cr"])
        catalog[cat] = {
            "symbol": best,
            "tracks": spec["tracks"],
            "matches": spec["matches"],
            **stats[best],
            "alternates": [
                {"symbol": s, **stats[s]} for s in verified if s != best
            ],
        }
        print(f"  {cat:16} -> {best:12} ₹{stats[best]['last_price']:>9,.2f}  "
              f"ADV ₹{stats[best]['adv_cr']:.1f}cr")
    return catalog


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh-dump", action="store_true")
    ap.add_argument("--fetch-bars", action="store_true",
                    help="also cache daily bars for the catalog ETFs into the v3 matrix")
    ap.add_argument("--fetch-ext-equities", action="store_true",
                    help="also cache daily bars for ALL extended NSE equities (slow)")
    args = ap.parse_args()

    ins = _load_dump(args.refresh_dump)
    eq_df, etf_df = _split_dump(ins)
    eq_df.to_csv(v3u.NSE_EQUITIES_CSV, index=False)
    etf_df.to_csv(v3u.NSE_ETFS_CSV, index=False)
    print(f"Wrote {v3u.NSE_EQUITIES_CSV}  ({len(eq_df)} equities)")
    print(f"Wrote {v3u.NSE_ETFS_CSV}  ({len(etf_df)} ETFs)")

    catalog = _verify_candidates(set(etf_df["Symbol"]))
    payload = {
        "generated_on": str(date.today()),
        "source": "Kite NSE cash dump + yfinance 3mo verification",
        "categories": catalog,
    }
    with open(os.path.abspath(CATALOG_JSON), "w") as f:
        json.dump(payload, f, indent=1)
    print(f"Wrote {os.path.abspath(CATALOG_JSON)}  ({len(catalog)} categories)")

    if args.fetch_bars:
        syms = [f"{c['symbol']}.NS" for c in catalog.values()]
        for c in catalog.values():
            syms.extend(f"{a['symbol']}.NS" for a in c.get("alternates", []))
        v3u.fetch_extra(sorted(set(syms)), namespace="etf")
        v3u.fetch_drivers()
    if args.fetch_ext_equities:
        base = set(v3u.all_tickers())
        ext = [t for t in (eq_df["Symbol"] + ".NS") if t not in base]
        v3u.fetch_extra(ext, namespace="ext")


if __name__ == "__main__":
    sys.exit(main())
