"""ALL-market flows sweep: one file per day carries every company.

Parallel downloads against the nsearchives CDN (static, tolerant), parse in
memory, single-writer SQLite. Era handling:
  delivery : sec_bhavdata_full (2020+) with MTO_*.DAT fallback (2016-2019)
  fut OI   : old-format fo bhavcopy zips before Jul-2024, UDiFF after
Usage: sweep_market.py benchmark | full
"""
import csv, io, json, sqlite3, sys, time, zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
import urllib.request, ssl

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"}
import certifi
CTX = ssl.create_default_context(cafile=certifi.where())

def fetch(url, timeout=25, tries=5):
    """404 = holiday (legit miss). 403/429/5xx = throttled — back off and
    retry; treating those as 'no data' is how a sweep silently loses 95%."""
    import random
    for i in range(tries):
        req = urllib.request.Request(url, headers=UA)
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            time.sleep((2 ** i) + random.random() * 2)
        except Exception:
            time.sleep(1 + random.random())
    return None

# ── per-day workers: return (kind, day, rows) ──
def _nl(t):
    return t.replace("\r\n", "\n").replace("\r", "\n")


def _safe(fn, kind):
    def wrapped(d):
        try:
            return fn(d)
        except Exception as exc:  # noqa: BLE001 — one bad file must not kill 5,000
            print(f"[warn] {kind} {d}: {type(exc).__name__} {exc}", flush=True)
            return (kind, d, [])
    return wrapped


def day_delivery(d):
    ddmmyyyy = d.strftime("%d%m%Y")
    raw = fetch(f"https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_{ddmmyyyy}.csv")
    rows = []
    if raw:
        for r in csv.DictReader(io.StringIO(_nl(raw.decode("utf-8", "replace")))):
            r = {k.strip(): (v.strip() if isinstance(v, str) else v) for k, v in r.items()}
            if r.get("SERIES") not in ("EQ", "BE"):
                continue
            try:
                rows.append((r["SYMBOL"], d.isoformat(), float(r["CLOSE_PRICE"]),
                             int(r["TTL_TRD_QNTY"]), int(r["DELIV_QTY"]),
                             float(r["DELIV_PER"]), int(r["NO_OF_TRADES"])))
            except (KeyError, ValueError):
                continue
        return ("delivery", d, rows)
    # MTO fallback (older era): rec-type 20 lines: 20,serial,SYMBOL,SERIES,traded,deliv,pct
    raw = fetch(f"https://nsearchives.nseindia.com/archives/equities/mto/MTO_{ddmmyyyy}.DAT")
    if raw:
        for line in raw.decode("utf-8", "replace").splitlines():
            p = [x.strip() for x in line.split(",")]
            if len(p) >= 7 and p[0] == "20" and p[3] in ("EQ", "BE"):
                try:
                    rows.append((p[2], d.isoformat(), None,
                                 int(p[4]), int(p[5]), float(p[6]), None))
                except ValueError:
                    continue
    return ("delivery", d, rows)

def day_fo(d):
    rows = []
    if d >= date(2024, 7, 8):
        raw = fetch("https://nsearchives.nseindia.com/content/fo/"
                    f"BhavCopy_NSE_FO_0_0_0_{d.strftime('%Y%m%d')}_F_0000.csv.zip")
        if raw:
            try:
                txt = zipfile.ZipFile(io.BytesIO(raw)).read(
                    zipfile.ZipFile(io.BytesIO(raw)).namelist()[0]).decode("utf-8", "replace")
            except Exception:
                return ("fut_oi", d, rows)
            for r in csv.DictReader(io.StringIO(_nl(txt))):
                if r.get("FinInstrmTp") != "STF":
                    continue
                try:
                    rows.append((r["TckrSymb"], d.isoformat(), r["XpryDt"],
                                 int(float(r["OpnIntrst"])),
                                 int(float(r["ChngInOpnIntrst"])),
                                 float(r["ClsPric"])))
                except (KeyError, ValueError):
                    continue
    else:
        mon = d.strftime("%b").upper()
        raw = fetch(f"https://nsearchives.nseindia.com/content/historical/DERIVATIVES/"
                    f"{d.year}/{mon}/fo{d.strftime('%d')}{mon}{d.year}bhav.csv.zip")
        if raw:
            try:
                txt = zipfile.ZipFile(io.BytesIO(raw)).read(
                    zipfile.ZipFile(io.BytesIO(raw)).namelist()[0]).decode("utf-8", "replace")
            except Exception:
                return ("fut_oi", d, rows)
            for r in csv.DictReader(io.StringIO(_nl(txt))):
                if r.get("INSTRUMENT") != "FUTSTK":
                    continue
                try:
                    rows.append((r["SYMBOL"], d.isoformat(), r["EXPIRY_DT"],
                                 int(float(r["OPEN_INT"])), int(float(r["CHG_IN_OI"])),
                                 float(r["CLOSE"])))
                except (KeyError, ValueError):
                    continue
    return ("fut_oi", d, rows)

def run(days, workers, db_path, label):
    db = sqlite3.connect(db_path)
    db.executescript("""
    PRAGMA journal_mode=WAL;
    CREATE TABLE IF NOT EXISTS delivery (symbol TEXT, d TEXT, close REAL,
      qty INTEGER, deliv_qty INTEGER, deliv_per REAL, trades INTEGER,
      PRIMARY KEY (symbol, d));
    CREATE TABLE IF NOT EXISTS fut_oi (symbol TEXT, d TEXT, expiry TEXT,
      oi INTEGER, oi_chg INTEGER, close REAL, PRIMARY KEY (symbol, d, expiry));
    """)
    have_d = {r[0] for r in db.execute("SELECT DISTINCT d FROM delivery")}
    have_f = {r[0] for r in db.execute("SELECT DISTINCT d FROM fut_oi")}
    jobs = ([(_safe(day_delivery, "delivery"), d) for d in days if d.isoformat() not in have_d]
            + [(_safe(day_fo, "fut_oi"), d) for d in days if d.isoformat() not in have_f])
    print(f"[{label}] resume: {len(days)*2 - len(jobs)} day-feeds already stored, "
          f"{len(jobs)} to fetch", flush=True)
    t0 = time.time(); done = 0; got = {"delivery": 0, "fut_oi": 0}
    hit_days = {"delivery": 0, "fut_oi": 0}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(fn, d) for fn, d in jobs]
        for f in as_completed(futs):
            kind, d, rows = f.result()
            if rows:
                hit_days[kind] += 1
                if kind == "delivery":
                    db.executemany("INSERT OR REPLACE INTO delivery VALUES (?,?,?,?,?,?,?)", rows)
                else:
                    db.executemany("INSERT OR REPLACE INTO fut_oi VALUES (?,?,?,?,?,?)", rows)
                got[kind] += len(rows)
            done += 1
            if done % 200 == 0:
                db.commit()
                el = time.time() - t0
                print(f"[{label}] {done}/{len(jobs)} files · {el:.0f}s · "
                      f"{done/el:.1f} files/s · rows {got}", flush=True)
    db.commit()
    el = time.time() - t0
    print(f"[{label}] DONE {len(jobs)} fetches in {el:.1f}s "
          f"({len(jobs)/el:.1f} files/s) · rows {got} · trading-days hit {hit_days}", flush=True)
    return el, got

if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "benchmark"
    if mode == "benchmark":
        # 30 weekdays spread across the eras that exercise every parser path
        days = ([date(2017, 3, 1) + timedelta(days=i) for i in range(10)]
                + [date(2022, 2, 1) + timedelta(days=i) for i in range(10)]
                + [date(2026, 7, 13) + timedelta(days=i) for i in range(10)])
        days = [d for d in days if d.weekday() < 5]
        run(days, 6, "/private/tmp/claude-501/-Users-karanveersingh-Downloads-Second-Star/f58ae040-8e2b-4c55-8857-096da362a10c/scratchpad/flows_bench.db", "bench")
    else:
        d0, d1 = date(2016, 1, 1), date(2026, 7, 27)
        days = [d0 + timedelta(days=i) for i in range((d1 - d0).days + 1)]
        days = [d for d in days if d.weekday() < 5]
        run(days, 6, "/private/tmp/claude-501/-Users-karanveersingh-Downloads-Second-Star/f58ae040-8e2b-4c55-8857-096da362a10c/scratchpad/flows_all.db", "full")
