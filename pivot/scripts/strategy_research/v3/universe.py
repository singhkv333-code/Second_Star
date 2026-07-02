"""v3 data layer — load the NIFTY-500 universe, fetch+cache daily bars
(chunked yfinance with retry, parquet cache), and expose the returns matrix,
Industry map, and the NIFTY/Brent driver series.

Cache layout (parquet, under v3/_cache/):
  close_<chunk>.parquet / vol_<chunk>.parquet   per ~50-ticker chunk (idempotent)
  close_all.parquet  / vol_all.parquet          merged wide matrices (cols=tickers)
  drivers.parquet                               ^NSEI + BZ=F closes
  coverage.json                                 have / missing / short report

Re-running fetch_bars() SKIPS chunks already cached, so the long-pole download
is resumable. Never silently loses a name — drops are reported.
"""
from __future__ import annotations

import json
import os
import time
import warnings
from typing import Optional

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

import yfinance as yf  # noqa: E402

_HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(_HERE, "_cache")
OUT_DIR = os.path.join(_HERE, "_out")
os.makedirs(CACHE_DIR, exist_ok=True)
os.makedirs(OUT_DIR, exist_ok=True)

NIFTY500_CSV = os.path.join(CACHE_DIR, "nifty500.csv")
NSE_EQUITIES_CSV = os.path.join(CACHE_DIR, "nse_equities.csv")   # full listed set (Kite dump)
NSE_ETFS_CSV = os.path.join(CACHE_DIR, "nse_etfs.csv")           # all NSE ETFs (Kite dump)
CLOSE_ALL = os.path.join(CACHE_DIR, "close_all.parquet")
VOL_ALL = os.path.join(CACHE_DIR, "vol_all.parquet")
DRIVERS = os.path.join(CACHE_DIR, "drivers.parquet")
COVERAGE_JSON = os.path.join(CACHE_DIR, "coverage.json")

NIFTY = "^NSEI"
BRENT = "BZ=F"
GOLD = "GC=F"
USDINR = "INR=X"
_DRIVER_TICKERS = {
    "NIFTY": NIFTY, "BRENT": BRENT, "GOLD": GOLD, "USDINR": USDINR,
}

START = "2010-01-01"
BAD_TICK = 0.5          # |daily simple return| > 0.5 == bad tick (v2 rule)
MIN_OBS = 50            # a ticker needs > MIN_OBS bars to be usable


# ── universe / industry ───────────────────────────────────────────────────────
def load_universe() -> pd.DataFrame:
    """The cached NIFTY-500 constituent table with a yfinance ``ticker`` column
    (Symbol + '.NS'). Columns: Company, Industry, Symbol, Series, ISIN, ticker."""
    df = pd.read_csv(NIFTY500_CSV)
    df = df.rename(columns={
        "Company Name": "Company", "ISIN Code": "ISIN",
    })
    df["Symbol"] = df["Symbol"].astype(str).str.strip()
    df["ticker"] = df["Symbol"] + ".NS"
    return df


def industry_map() -> dict[str, str]:
    """ticker(.NS) -> Industry tag."""
    df = load_universe()
    return dict(zip(df["ticker"], df["Industry"]))


def all_tickers() -> list[str]:
    return load_universe()["ticker"].tolist()


def sector_symbols(industry: str) -> list[str]:
    """All NIFTY-500 tickers whose Industry tag == ``industry`` (exact match)."""
    df = load_universe()
    return df.loc[df["Industry"] == industry, "ticker"].tolist()


# ── extended universe (all NSE listed + ETFs, from the Kite cash dump) ────────
def load_equities_ext() -> pd.DataFrame:
    """NIFTY-500 ∪ the full plain-series NSE listed set (``nse_equities.csv``,
    written by ``scripts/build_universe.py`` from the Kite cash dump). Extras
    carry ``Industry=""`` (the exchange dump has no sector tag) and
    ``in_nifty500=False``. Falls back to the NIFTY-500 alone if the extended
    file has not been built yet."""
    base = load_universe()
    base["in_nifty500"] = True
    if not os.path.exists(NSE_EQUITIES_CSV):
        return base
    ext = pd.read_csv(NSE_EQUITIES_CSV)
    ext["Symbol"] = ext["Symbol"].astype(str).str.strip()
    ext["ticker"] = ext["Symbol"] + ".NS"
    ext = ext[~ext["Symbol"].isin(set(base["Symbol"]))]
    ext["Industry"] = ""
    ext["in_nifty500"] = False
    cols = ["Company", "Industry", "Symbol", "ticker", "in_nifty500"]
    ext = ext.rename(columns={"Name": "Company"})
    return pd.concat([base[cols], ext[cols]], ignore_index=True)


def etf_universe() -> pd.DataFrame:
    """All NSE-listed ETFs (``nse_etfs.csv`` from the Kite cash dump), with a
    yfinance ``ticker`` column. Empty frame if not built yet."""
    if not os.path.exists(NSE_ETFS_CSV):
        return pd.DataFrame(columns=["Symbol", "Name", "ticker"])
    df = pd.read_csv(NSE_ETFS_CSV)
    df["Symbol"] = df["Symbol"].astype(str).str.strip()
    df["ticker"] = df["Symbol"] + ".NS"
    return df


# ── fetch + cache (chunked, retry, idempotent) ────────────────────────────────
def _flatten(raw: pd.DataFrame, field: str) -> pd.DataFrame:
    """Pull a (field) wide frame (cols=tickers) out of a yf.download MultiIndex."""
    if raw is None or len(raw) == 0:
        return pd.DataFrame()
    if isinstance(raw.columns, pd.MultiIndex):
        lv0 = raw.columns.get_level_values(0)
        if field in set(lv0):                       # layout (field, ticker)
            sub = raw[field]
            return sub if isinstance(sub, pd.DataFrame) else sub.to_frame()
        # layout (ticker, field)
        out = {}
        for tkr in raw.columns.get_level_values(0).unique():
            if (tkr, field) in raw.columns:
                out[tkr] = raw[(tkr, field)]
        return pd.DataFrame(out)
    # single-ticker frame
    if field in raw.columns:
        return raw[[field]]
    return pd.DataFrame()


def _download_chunk(tickers: list[str], start: str, end: Optional[str],
                    retries: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            raw = yf.download(tickers, start=start, end=end, auto_adjust=True,
                              progress=False, group_by="column", threads=True)
            close = _flatten(raw, "Close")
            vol = _flatten(raw, "Volume")
            if close.shape[1] > 0:
                close.index = pd.to_datetime(close.index).tz_localize(None)
                if vol.shape[1] > 0:
                    vol.index = pd.to_datetime(vol.index).tz_localize(None)
                return close, vol
        except Exception as exc:  # noqa: BLE001
            last_err = exc
        time.sleep(1.5 * attempt)
    print(f"    [chunk failed after {retries} tries] {last_err}")
    return pd.DataFrame(), pd.DataFrame()


def fetch_bars(symbols: Optional[list[str]] = None, *, start: str = START,
               end: Optional[str] = None, chunk: int = 50, retries: int = 3,
               force: bool = False) -> dict:
    """Download daily Close+Volume for ``symbols`` (default = full universe) in
    ~``chunk``-ticker batches with retry, cache each batch to parquet (skipped on
    re-run), then merge into close_all/vol_all + drivers. Returns the coverage
    report (also written to coverage.json). Idempotent + resumable."""
    if symbols is None:
        symbols = all_tickers()
    symbols = list(dict.fromkeys(symbols))
    n_req = len(symbols)
    chunks = [symbols[i:i + chunk] for i in range(0, n_req, chunk)]

    close_parts: list[pd.DataFrame] = []
    vol_parts: list[pd.DataFrame] = []
    for ci, batch in enumerate(chunks):
        cpath = os.path.join(CACHE_DIR, f"close_{ci:02d}.parquet")
        vpath = os.path.join(CACHE_DIR, f"vol_{ci:02d}.parquet")
        if (not force) and os.path.exists(cpath):
            close_parts.append(pd.read_parquet(cpath))
            if os.path.exists(vpath):
                vol_parts.append(pd.read_parquet(vpath))
            print(f"  [chunk {ci+1}/{len(chunks)}] cached ({len(batch)} tkrs)")
            continue
        print(f"  [chunk {ci+1}/{len(chunks)}] downloading {len(batch)} tickers...")
        close, vol = _download_chunk(batch, start, end, retries)
        if close.shape[1] > 0:
            close.to_parquet(cpath)
            close_parts.append(close)
            if vol.shape[1] > 0:
                vol.to_parquet(vpath)
                vol_parts.append(vol)

    # drivers (NIFTY + Brent) — always refreshed if missing
    if force or not os.path.exists(DRIVERS):
        print("  [drivers] downloading ^NSEI + BZ=F ...")
        dcl, _ = _download_chunk([NIFTY, BRENT], start, end, retries)
        if dcl.shape[1] > 0:
            dcl.to_parquet(DRIVERS)

    # merge
    close_all = pd.concat(close_parts, axis=1) if close_parts else pd.DataFrame()
    close_all = close_all.loc[:, ~close_all.columns.duplicated()].sort_index()
    vol_all = pd.concat(vol_parts, axis=1) if vol_parts else pd.DataFrame()
    if vol_all.shape[1]:
        vol_all = vol_all.loc[:, ~vol_all.columns.duplicated()].sort_index()
    close_all.to_parquet(CLOSE_ALL)
    if vol_all.shape[1]:
        vol_all.to_parquet(VOL_ALL)

    # coverage
    have = [c for c in symbols if c in close_all.columns
            and close_all[c].notna().sum() > MIN_OBS]
    short = [c for c in symbols if c in close_all.columns
             and 0 < close_all[c].notna().sum() <= MIN_OBS]
    missing = [c for c in symbols if c not in close_all.columns]
    rep = {
        "requested": n_req,
        "fetched": len(have),
        "short": short,
        "missing": missing,
        "n_short": len(short),
        "n_missing": len(missing),
        "date_min": str(close_all.index.min().date()) if len(close_all) else None,
        "date_max": str(close_all.index.max().date()) if len(close_all) else None,
        "rows": int(len(close_all)),
        "close_cols": int(close_all.shape[1]),
    }
    with open(COVERAGE_JSON, "w") as f:
        json.dump(rep, f, indent=2)
    return rep


def fetch_extra(symbols: list[str], *, namespace: str, start: str = START,
                end: Optional[str] = None, chunk: int = 50, retries: int = 3,
                force: bool = False) -> dict:
    """Fetch bars for symbols OUTSIDE the base universe (ETFs, extended NSE
    names) into their own chunk namespace (``close_<ns>_NN.parquet``) and MERGE
    the new columns into CLOSE_ALL/VOL_ALL **without dropping** the existing
    matrix columns (``fetch_bars`` rebuilds the matrix from its own chunks only,
    so calling it with a different symbol list would clobber the cache)."""
    symbols = list(dict.fromkeys(symbols))
    chunks = [symbols[i:i + chunk] for i in range(0, len(symbols), chunk)]
    close_parts: list[pd.DataFrame] = []
    vol_parts: list[pd.DataFrame] = []
    for ci, batch in enumerate(chunks):
        cpath = os.path.join(CACHE_DIR, f"close_{namespace}_{ci:02d}.parquet")
        vpath = os.path.join(CACHE_DIR, f"vol_{namespace}_{ci:02d}.parquet")
        if (not force) and os.path.exists(cpath):
            close_parts.append(pd.read_parquet(cpath))
            if os.path.exists(vpath):
                vol_parts.append(pd.read_parquet(vpath))
            print(f"  [{namespace} {ci+1}/{len(chunks)}] cached ({len(batch)} tkrs)")
            continue
        print(f"  [{namespace} {ci+1}/{len(chunks)}] downloading {len(batch)} tickers...")
        close, vol = _download_chunk(batch, start, end, retries)
        if close.shape[1] > 0:
            close.to_parquet(cpath)
            close_parts.append(close)
            if vol.shape[1] > 0:
                vol.to_parquet(vpath)
                vol_parts.append(vol)

    def _merge(existing_path: str, parts: list[pd.DataFrame]) -> pd.DataFrame:
        new = pd.concat(parts, axis=1) if parts else pd.DataFrame()
        if new.shape[1]:
            new = new.loc[:, ~new.columns.duplicated()]
        if os.path.exists(existing_path):
            base = pd.read_parquet(existing_path)
            keep_new = [c for c in new.columns if c not in base.columns]
            merged = pd.concat([base, new[keep_new]], axis=1).sort_index()
        else:
            merged = new.sort_index()
        merged.to_parquet(existing_path)
        return merged

    close_all = _merge(CLOSE_ALL, close_parts)
    if vol_parts:
        _merge(VOL_ALL, vol_parts)
    have = [c for c in symbols if c in close_all.columns
            and close_all[c].notna().sum() > MIN_OBS]
    rep = {"namespace": namespace, "requested": len(symbols), "fetched": len(have),
           "missing": [c for c in symbols if c not in close_all.columns]}
    print(f"  [{namespace}] merged: {rep['fetched']}/{rep['requested']} usable")
    return rep


def fetch_drivers(*, force: bool = False, start: str = START,
                  end: Optional[str] = None, retries: int = 3) -> list[str]:
    """Ensure every ``_DRIVER_TICKERS`` column exists in drivers.parquet,
    merging new drivers (gold, USDINR, …) without dropping the existing ones."""
    want = list(_DRIVER_TICKERS.values())
    have = pd.read_parquet(DRIVERS) if os.path.exists(DRIVERS) else pd.DataFrame()
    need = want if force else [t for t in want if t not in have.columns]
    if need:
        print(f"  [drivers] downloading {need} ...")
        dcl, _ = _download_chunk(need, start, end, retries)
        if dcl.shape[1] > 0:
            keep = [c for c in dcl.columns if force or c not in have.columns]
            have = pd.concat([have.drop(columns=[c for c in keep if c in have.columns]),
                              dcl[keep]], axis=1).sort_index()
            have.to_parquet(DRIVERS)
    return [c for c in want if c in have.columns]


# ── matrices / series exposed to the rest of the engine ───────────────────────
def close_matrix() -> pd.DataFrame:
    return pd.read_parquet(CLOSE_ALL).sort_index()


def volume_matrix() -> pd.DataFrame:
    if os.path.exists(VOL_ALL):
        return pd.read_parquet(VOL_ALL).sort_index()
    return pd.DataFrame()


def returns_matrix() -> pd.DataFrame:
    """Daily simple returns for every usable constituent, |r|>0.5 bad-tick masked
    (the v2 clean_returns rule). Columns = tickers with > MIN_OBS bars."""
    px = close_matrix()
    usable = [c for c in px.columns if px[c].notna().sum() > MIN_OBS]
    r = px[usable].pct_change()
    return r.mask(r.abs() > BAD_TICK)


def series(name: str) -> pd.Series:
    """Daily simple return series for a driver: name in {NIFTY, BRENT} (or a
    raw yfinance ticker present in drivers.parquet)."""
    d = pd.read_parquet(DRIVERS).sort_index()
    tkr = _DRIVER_TICKERS.get(name.upper(), name)
    s = d[tkr].dropna()
    r = s.pct_change()
    return r.mask(r.abs() > BAD_TICK)


def driver_close(name: str) -> pd.Series:
    d = pd.read_parquet(DRIVERS).sort_index()
    tkr = _DRIVER_TICKERS.get(name.upper(), name)
    return d[tkr].dropna()


def coverage_report() -> dict:
    if os.path.exists(COVERAGE_JSON):
        with open(COVERAGE_JSON) as f:
            return json.load(f)
    return {}


if __name__ == "__main__":
    print("Universe rows:", len(load_universe()))
    rep = fetch_bars()
    print(json.dumps(rep, indent=2))
