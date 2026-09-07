"""Classify the non-equity instruments so peers and screens work on them.

`classification(symbol, name, industry)` is what get_peers groups on and what
screen_universe filters by. It ships with the 500 Moneycontrol-classified NSE
companies; the indices, India VIX, MCX futures, INR pairs and crypto added by
backfill_macro.py / backfill_crypto.py have bars but no row, so get_peers
answers "no industry classification" and they are invisible to a screen.

The industry values follow the existing slug style (lowercase, no spaces) and
are chosen so a peer group is something you would actually compare: broad
indices against each other, sector indices against each other, precious metals
apart from base metals and from energy, and every crypto in one bucket.

Idempotent — INSERT OR REPLACE, safe to re-run after adding instruments.

Run:  python charto/data/classify_macro.py
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "charto_bars.db"

# symbol -> (display name, industry slug)
ROWS: dict[str, tuple[str, str]] = {}

_BROAD = {
    "NIFTY 50": "Nifty 50", "NIFTY 100": "Nifty 100", "NIFTY 500": "Nifty 500",
    "NIFTY NEXT 50": "Nifty Next 50", "NIFTY MIDCAP 100": "Nifty Midcap 100",
    "NIFTY SMLCAP 100": "Nifty Smallcap 100", "SENSEX": "BSE Sensex",
}
_SECTOR = {
    "NIFTY BANK": "Nifty Bank", "NIFTY IT": "Nifty IT",
    "NIFTY AUTO": "Nifty Auto", "NIFTY PHARMA": "Nifty Pharma",
    "NIFTY FMCG": "Nifty FMCG", "NIFTY METAL": "Nifty Metal",
    "NIFTY REALTY": "Nifty Realty", "NIFTY ENERGY": "Nifty Energy",
    "NIFTY INFRA": "Nifty Infrastructure", "NIFTY PSU BANK": "Nifty PSU Bank",
    "NIFTY PVT BANK": "Nifty Private Bank",
    "NIFTY FIN SERVICE": "Nifty Financial Services",
    "NIFTY CONSUMPTION": "Nifty Consumption", "NIFTY MEDIA": "Nifty Media",
    "NIFTY COMMODITIES": "Nifty Commodities", "BANKEX": "BSE Bankex",
}
_PRECIOUS = {"GOLD": "Gold", "GOLDM": "Gold Mini",
             "SILVER": "Silver", "SILVERM": "Silver Mini"}
_BASE = {"COPPER": "Copper", "ZINC": "Zinc", "ALUMINIUM": "Aluminium",
         "LEAD": "Lead", "NICKEL": "Nickel"}
_ENERGY = {"CRUDEOIL": "Crude Oil", "NATURALGAS": "Natural Gas"}
_SOFT = {"COTTON": "Cotton", "MENTHAOIL": "Mentha Oil"}
_FX = {"USDINR": "USD/INR", "EURINR": "EUR/INR",
       "GBPINR": "GBP/INR", "JPYINR": "JPY/INR"}
# The venue is part of the name because BTC-USD and BTCUSDT are the same asset
# priced on different books — a comparison between them is a basis question,
# and the reply should be able to say which is which.
_CRYPTO_BASE = {
    "BTC": "Bitcoin", "ETH": "Ethereum", "SOL": "Solana", "XRP": "XRP",
    "ADA": "Cardano", "DOGE": "Dogecoin", "AVAX": "Avalanche",
    "LINK": "Chainlink", "LTC": "Litecoin", "DOT": "Polkadot", "BNB": "BNB",
}

for src, ind in ((_BROAD, "indexbroad"), (_SECTOR, "indexsector"),
                 (_PRECIOUS, "commoditypreciousmetals"),
                 (_BASE, "commoditybasemetals"), (_ENERGY, "commodityenergy"),
                 (_SOFT, "commoditysoft"), (_FX, "currency")):
    for sym, name in src.items():
        ROWS[sym] = (name, ind)
ROWS["INDIA VIX"] = ("India VIX", "volatility")
for base, name in _CRYPTO_BASE.items():
    ROWS[f"{base}USDT"] = (f"{name} / USDT (Bybit)", "cryptocurrency")
    ROWS[f"{base}-USD"] = (f"{name} / USD (Coinbase)", "cryptocurrency")


def main() -> None:
    con = sqlite3.connect(DB_PATH, timeout=60)
    con.execute("PRAGMA busy_timeout=30000")
    con.execute("CREATE TABLE IF NOT EXISTS classification ("
                " symbol TEXT PRIMARY KEY, name TEXT, industry TEXT)")
    have = {r[0] for r in con.execute("SELECT DISTINCT symbol FROM bars")}
    # only classify what is actually in the store — a row for a symbol with no
    # bars would show up as a permanently "cold" peer that never hydrates
    rows = [(s, n, i) for s, (n, i) in ROWS.items() if s in have]
    skipped = sorted(set(ROWS) - have)
    con.executemany(
        "INSERT OR REPLACE INTO classification VALUES (?,?,?)", rows)
    con.commit()

    print(f"classified {len(rows)} instruments"
          + (f" ({len(skipped)} skipped, no bars: {', '.join(skipped[:6])}"
             + ("…" if len(skipped) > 6 else "") + ")" if skipped else ""))
    for ind, n in con.execute(
            "SELECT industry, COUNT(*) FROM classification WHERE industry IN "
            "(SELECT DISTINCT industry FROM classification WHERE symbol IN "
            f"({','.join('?' * len(rows))})) GROUP BY industry ORDER BY 2 DESC",
            [r[0] for r in rows]):
        print(f"  {ind:26s} {n}")
    missing = [r[0] for r in con.execute(
        "SELECT DISTINCT b.symbol FROM bars b LEFT JOIN classification c "
        "ON b.symbol=c.symbol WHERE c.symbol IS NULL ORDER BY b.symbol")]
    print(f"still unclassified with bars: {len(missing)}"
          + (f" -> {', '.join(missing)}" if missing else ""))
    con.close()


if __name__ == "__main__":
    main()
