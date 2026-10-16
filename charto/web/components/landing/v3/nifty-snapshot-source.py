"""Refresh the landing demo's NIFTY 50 OHLC snapshot from Yahoo Finance fallback."""
import datetime as dt
import json
import urllib.request
from pathlib import Path

url = "https://query1.finance.yahoo.com/v8/finance/chart/%5ENSEI?period1=1767225600&period2=1792022400&interval=1d"
request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
data = json.load(urllib.request.urlopen(request, timeout=20))["chart"]["result"][0]
quote = data["indicators"]["quote"][0]
bars = []
for i, t in enumerate(data["timestamp"]):
    vals = [quote[k][i] for k in ("open", "high", "low", "close")]
    if any(v is None for v in vals):
        continue
    bars.append([t, *[round(v, 2) for v in vals], int(quote["volume"][i] or 0)])
assert len(bars) > 100
watch = {}
for symbol in ("NIFTY", "RELIANCE", "HDFCBANK", "TCS", "INFY", "ICICIBANK", "SBIN", "BHARTIARTL", "ITC"):
    if symbol == "NIFTY":
        series = bars
    else:
        ticker = symbol + ".NS"
        item_url = "https://query1.finance.yahoo.com/v8/finance/chart/" + ticker + "?period1=1791504000&period2=1792022400&interval=1d"
        item = json.load(urllib.request.urlopen(urllib.request.Request(item_url, headers={"User-Agent": "Mozilla/5.0"}), timeout=20))["chart"]["result"][0]
        closes = [v for v in item["indicators"]["quote"][0]["close"] if v is not None]
        assert len(closes) >= 2, ticker
        watch[symbol] = {"last": round(closes[-1], 2), "prev": round(closes[-2], 2)}
        continue
    watch[symbol] = {"last": series[-1][4], "prev": series[-2][4]}
day = dt.datetime.fromtimestamp(bars[-1][0], dt.timezone.utc).strftime("%d %b %Y")
out = Path(__file__).with_name("nifty-snapshot.ts")
out.write_text(
    "/** NIFTY 50 daily OHLCV, Yahoo Finance fallback (^NSEI), through " + day + ". */\n"
    + "export const NIFTY_AS_OF = " + json.dumps(day) + ";\n"
    + "export const NIFTY_DAILY: [number, number, number, number, number, number][] = " + json.dumps(bars, separators=(",", ":")) + ";\n"
    + "export const NIFTY_WATCH = " + json.dumps(watch, separators=(",", ":")) + " as const;\n",
    encoding="utf-8",
)
print(f"Wrote {len(bars)} NIFTY bars through {day}")
