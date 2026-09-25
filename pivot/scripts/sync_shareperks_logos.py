"""Refresh backend/market/shareperks_logos.json: ticker -> ISIN, for every
Indian listing SharePerks has a REAL logo for.

SharePerks serves `https://company-logo.shareperks.in/logo/{isin}/icon.svg`
but answers 200 with a placeholder for an ISIN it doesn't know, so a URL is
only ever built for an ISIN on this list. The list is the project's own index
(3,156 logos); SharePerks has no listing endpoint.

The logos are TradingView's marks re-served without a stated licence. Chosen
2026-09-24 as a trial on looks; see project memory before relying on it.

Run:  pivot/.venv/bin/python scripts/sync_shareperks_logos.py
"""
from __future__ import annotations

import json
import ssl
import urllib.request
from pathlib import Path

INDEX = ("https://raw.githubusercontent.com/dharunashokkumar/"
         "indian-listed-company-logos/main/data/logos.json")
OUT = Path(__file__).resolve().parents[1] / "backend" / "market" / "shareperks_logos.json"


def main() -> None:
    import certifi  # the framework Python ships without a CA bundle
    ctx = ssl.create_default_context(cafile=certifi.where())
    with urllib.request.urlopen(INDEX, timeout=60, context=ctx) as r:
        d = json.load(r)
    tickers: dict[str, str] = {}
    # NSE first: a ticker listed on both exchanges means the NSE company.
    for exchange in ("NSE", "BSE"):
        for x in d["logos"]:
            if x.get("exchange") == exchange and x.get("isin"):
                tickers.setdefault(x["ticker"].strip().upper(), x["isin"].strip().upper())
    OUT.write_text(json.dumps({
        "_source": INDEX,
        "_generated_at": d.get("generatedAt"),
        "tickers": dict(sorted(tickers.items())),
    }, separators=(",", ":")) + "\n")
    print(f"{len(tickers)} tickers -> {OUT}")


if __name__ == "__main__":
    main()
