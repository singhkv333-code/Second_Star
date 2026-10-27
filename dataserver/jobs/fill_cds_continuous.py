#!/usr/bin/env python3
"""Fill the INR-pair continuous daily series where Kite's stops (June 2023).

Kite's `continuous=1` daily for the CDS segment ends 2023-06-06, and Kite
does not list expired contracts, so between then and the first front-month
contract the backfill holds, the USDINR/EURINR/GBPINR/JPYINR daily chart
would have a three-year hole. NSE's currency-derivatives bhavcopy carries
every contract's daily bar, expired ones included, so the front month on
each day is read straight off it:

  front = the contract carrying the most open interest among those expiring
          on or after the day (an OI roll). The weeklies hold a sliver of the
          OI, and a monthly's OI migrates to the next month around expiry;
          "last expiry of the month" is NOT a monthly test — October 2026's
          weekly (30th) expires after its monthly (28th).

Three file formats: CD_BhavcopyDDMMYY.zip holds a dBase file before 2016 and
a CSV after, until NSE's July-2024 UDiFF switch to
BhavCopy_NSE_CD_0_0_0_YYYYMMDD_F_0000.csv.zip. A 404 is a holiday.
Volume is contracts in the bhavcopy, the same unit as Kite's (measured within
0.03% on overlapping days), so the scale defaults to 1; --calibrate measures
it against Kite volumes if that ever changes.

Writes CSV rows (symbol, ts, o, h, l, c, v, expiry) for CONT:<pair>; the first
seven columns replace fut_cds's kite_1d continuous rows (2011 onward).

  python3 fill_cds_continuous.py --from 2023-06-07 --to 2026-09-22 --out fill.csv
"""
from __future__ import annotations

import argparse, csv, io, json, struct, sys, time, urllib.error, urllib.request, zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))
PAIRS = ("USDINR", "EURINR", "GBPINR", "JPYINR")
BASE = "https://nsearchives.nseindia.com/archives/cd/bhav/"
UA = {"User-Agent": "Mozilla/5.0"}


def _dbf_rows(raw: bytes):
    """Rows of a dBase III file — NSE's pre-2016 bhavcopy format, same
    columns as the later CSV."""
    n, hl, rl = struct.unpack("<IHH", raw[4:12])
    fields, p = [], 32
    while raw[p] != 0x0D:
        fields.append((raw[p:p + 11].split(b"\0")[0].decode(), raw[p + 16]))
        p += 32
    for i in range(n):
        rec = raw[hl + i * rl: hl + (i + 1) * rl]
        if not rec or rec[:1] == b"*":
            continue
        q, row = 1, {}
        for name, ln in fields:
            row[name] = rec[q:q + ln].decode("latin-1").strip()
            q += ln
        yield row


def fetch(day: date) -> list[dict] | None:
    names = [f"BhavCopy_NSE_CD_0_0_0_{day:%Y%m%d}_F_0000.csv.zip", f"CD_Bhavcopy{day:%d%m%y}.zip"]
    for n in names:
        for attempt in range(3):
            try:
                raw = urllib.request.urlopen(urllib.request.Request(BASE + n, headers=UA), timeout=30).read()
                break
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    raw = None
                    break
                time.sleep(2 + attempt * 3)
            except OSError:
                time.sleep(2 + attempt * 3)
        else:
            raw = None
        if not raw:
            continue
        z = zipfile.ZipFile(io.BytesIO(raw))
        out = []
        for member in z.namelist():
            if "_OP" in member:
                continue
            rows = (_dbf_rows(z.read(member)) if member.lower().endswith(".dbf") else
                    csv.DictReader(io.TextIOWrapper(z.open(member), encoding="utf-8", errors="replace")))
            for r in rows:
                if "TckrSymb" in r:                                      # UDiFF
                    if r.get("FinInstrmTp", "").upper() not in ("STF", "IDF", "FUTCUR") and r.get("OptnTp"):
                        continue
                    if r.get("OptnTp") or r["TckrSymb"] not in PAIRS:
                        continue
                    exp = r.get("XpryDt") or r.get("FininstrmActlXpryDt")
                    out.append({"pair": r["TckrSymb"], "exp": exp[:10],
                                "o": float(r["OpnPric"] or 0), "h": float(r["HghPric"] or 0),
                                "l": float(r["LwPric"] or 0), "c": float(r["ClsPric"] or 0),
                                "v": float(r["TtlTradgVol"] or 0),
                                "oi": float(r.get("OpnIntrst") or 0)})
                else:                                                     # pre-2024
                    k = r.get("CONTRACT_D", "")
                    if not k.startswith("FUTCUR"):
                        continue
                    body = k[6:]
                    pair, exp = body[:6], body[6:]
                    if pair not in PAIRS:
                        continue
                    out.append({"pair": pair, "exp": datetime.strptime(exp, "%d-%b-%Y").strftime("%Y-%m-%d"),
                                "o": float(r["OPEN_PRICE"] or 0), "h": float(r["HIGH_PRICE"] or 0),
                                "l": float(r["LOW_PRICE"] or 0), "c": float(r["CLOSE_PRIC"] or 0),
                                "v": float(r["TRADED_QUA"] or 0),
                                "oi": float(r.get("OI_NO_CON") or 0)})
        return out
    return None


def front_rows(day: date, rows: list[dict]) -> dict[str, dict]:
    out = {}
    for pair in PAIRS:
        rs = [r for r in rows if r["pair"] == pair and r["exp"] >= day.isoformat()]
        if not rs:
            continue
        r = max(rs, key=lambda x: (x["oi"], -int(x["exp"].replace("-", ""))))
        if r["o"] > 0 and r["h"] > 0 and r["l"] > 0:
            out[pair] = r
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="d0", required=True)
    ap.add_argument("--to", dest="d1", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--calibrate", help="JSON {pair: [[YYYY-MM-DD, kite_volume], ...]}")
    a = ap.parse_args(argv)
    d0, d1 = date.fromisoformat(a.d0), date.fromisoformat(a.d1)
    days = [d0 + timedelta(i) for i in range((d1 - d0).days + 1)]
    days = [d for d in days if d.weekday() < 5]
    with ThreadPoolExecutor(6) as ex:
        got = dict(zip(days, ex.map(fetch, days)))
    scale = {p: 1.0 for p in PAIRS}
    if a.calibrate:
        cal = json.load(open(a.calibrate))
        for p, pts in cal.items():
            ratios = []
            cdays = [date.fromisoformat(d) for d, _ in pts]
            with ThreadPoolExecutor(6) as ex:
                cg = dict(zip(cdays, ex.map(fetch, cdays)))
            for (d, kv) in pts:
                rows = cg.get(date.fromisoformat(d))
                fr = front_rows(date.fromisoformat(d), rows or []).get(p)
                if fr and fr["v"] and kv:
                    ratios.append(kv / fr["v"])
            if ratios:
                ratios.sort()
                scale[p] = ratios[len(ratios) // 2]
    n_days = n_rows = 0
    with open(a.out, "w", newline="") as f:
        w = csv.writer(f)
        for d in days:
            rows = got[d]
            if rows is None:
                continue
            n_days += 1
            ts = int(datetime(d.year, d.month, d.day, tzinfo=IST).timestamp())
            for p, r in front_rows(d, rows).items():
                w.writerow([f"CONT:{p}", ts, r["o"], r["h"], r["l"], r["c"], int(round(r["v"] * scale[p])),
                            r["exp"]])
                n_rows += 1
    print(json.dumps({"business_days": len(days), "files": n_days, "rows": n_rows,
                      "volume_scale": scale}))


if __name__ == "__main__":
    sys.exit(main())
