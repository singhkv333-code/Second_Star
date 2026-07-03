"""Monsoon event study, PART 2 — WHERE in the calendar is the genuine signal,
and the DEFICIENT-year asymmetry. Drives the staging of the 3 strategies.

Part 1 (monsoon_event_study.py) showed that over the FULL Jun-Sep season the
cross-section is dominated by market beta and the largest normal-vs-deficient
spreads sit in URBAN controls (Bajaj Finance / Asian Paints) -> the naive
"buy rural FMCG in a good monsoon" read is largely SPURIOUS (risk-on, not rural).

This script locates the GENUINE, physically-connected signal by:
  A) Re-running the market-model CAR + monsoon-beta (season CAR ~ centred LPA%)
     over SEVERAL calendar windows:
        forecast  [Apr15..Jun15]  (around IMD's 1st Long Range Forecast)
        onset     [May15..Jul31]  (monsoon onset + Jun-Jul sowing progress)
        sowing    [Jun01..Aug31]  (kharif sowing -> input demand realised)
        season    [Jun01..Sep30]  (full SW monsoon)
        drift     [Oct01..Dec31]  (post-season harvest/rural-spend drift)
     The window where monsoon-beta is largest + most significant tells us WHEN
     to be positioned (pre-position vs confirmation vs harvest-drift).
  B) The DEFICIENT-year asymmetry: average CAR of the agri-input vs rural-FMCG
     vs urban-control groups in DEFICIENT (<96) years -> sizes the downside the
     defined-risk hedge must cover.

yfinance only. Real numbers printed. No fabrication.
"""
from __future__ import annotations

import json
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

warnings.filterwarnings("ignore")
OUT = Path(__file__).resolve().parent / "monsoon_windows_out.json"
NIFTY = "^NSEI"

LPA = {2009: 78, 2010: 102, 2011: 102, 2012: 93, 2013: 106, 2014: 88, 2015: 86,
       2016: 97, 2017: 95, 2018: 91, 2019: 110, 2020: 109, 2021: 99, 2022: 106,
       2023: 94, 2024: 108}
NORMAL_CUT = 96

# (mon_day_start, mon_day_end) inclusive
WINDOWS = {
    "forecast": (("04", "15"), ("06", "15")),
    "onset":    (("05", "15"), ("07", "31")),
    "sowing":   (("06", "01"), ("08", "31")),
    "season":   (("06", "01"), ("09", "30")),
    "drift":    (("10", "01"), ("12", "31")),
}

GROUPS = {
    "agri_input": ["COROMANDEL.NS", "CHAMBLFERT.NS", "RALLIS.NS", "UPL.NS", "PIIND.NS"],
    "tractor_2w": ["M&M.NS", "ESCORTS.NS", "HEROMOTOCO.NS", "TVSMOTOR.NS", "BAJAJ-AUTO.NS"],
    "rural_fmcg": ["HINDUNILVR.NS", "DABUR.NS", "MARICO.NS", "ITC.NS", "EMAMILTD.NS",
                   "GODREJCP.NS", "TATACONSUM.NS"],
    "urban_ctrl": ["ASIANPAINT.NS", "TITAN.NS", "BAJFINANCE.NS"],
}
ALL = sorted({t for g in GROUPS.values() for t in g})

START, END = "2008-10-01", "2025-01-01"


def fetch(t):
    df = yf.download(t, start=START, end=END, progress=False, auto_adjust=True)
    if df is None or df.empty:
        return None
    c = df["Close"]
    if isinstance(c, pd.DataFrame):
        c = c.iloc[:, 0]
    c = c.dropna()
    return c if len(c) > 250 else None


def ret(s):
    return s.pct_change().dropna()


def ols(y, x):
    n = len(y)
    if n < 5:
        return (np.nan, np.nan, np.nan, n)
    X = np.column_stack([np.ones(n), x])
    b, *_ = np.linalg.lstsq(X, y, rcond=None)
    resid = y - X @ b
    dof = n - 2
    if dof <= 0:
        return (b[0], b[1], np.nan, n)
    s2 = (resid @ resid) / dof
    se = np.sqrt(s2 * np.linalg.inv(X.T @ X)[1, 1])
    return (b[0], b[1], (b[1] / se if se > 0 else np.nan), n)


def win_car(rs, rm, year, w):
    (sm, sd), (em, ed) = WINDOWS[w]
    # estimation window = 100 trading days ending 10 sessions before window start
    wstart = pd.Timestamp(f"{year}-{sm}-{sd}")
    wend = pd.Timestamp(f"{year}-{em}-{ed}")
    est_mask = (rs.index < wstart - pd.Timedelta(days=10))
    est = rs[est_mask].tail(100)
    if len(est) < 60:
        return None
    em_ = rm.reindex(est.index).dropna()
    est = est.reindex(em_.index)
    if len(est) < 60:
        return None
    a, b, *_ = ols(est.values, em_.values)
    if not np.isfinite(b):
        return None
    evt = (rs.index >= wstart) & (rs.index <= wend)
    if evt.sum() < 20:
        return None
    ar = rs[evt].values - (a + b * rm[evt].values)
    return float(np.sum(ar))


def main():
    nifty = fetch(NIFTY)
    rm = ret(nifty)
    closes = {}
    for t in ALL:
        s = fetch(t)
        if s is not None:
            closes[t] = ret(s)
    years = sorted(LPA)

    # ── A) monsoon-beta per name per window ──────────────────────────────────
    print("MONSOON-BETA (season-CAR ~ centred LPA%) BY CALENDAR WINDOW")
    print("  value = slope (CAR per +1pp LPA); t in (); * if |t|>=1.5, sign must be +\n")
    name_win = {}
    hdr = f"{'TICKER':14}" + "".join(f"{w[:8]:>16}" for w in WINDOWS)
    print(hdr); print("-" * len(hdr))
    for t, rs in closes.items():
        idx = rs.index.intersection(rm.index)
        rs2, rm2 = rs.loc[idx], rm.loc[idx]
        row = f"{t:14}"
        name_win[t] = {}
        for w in WINDOWS:
            cars, lpas = [], []
            for y in years:
                c = win_car(rs2, rm2, y, w)
                if c is not None:
                    cars.append(c); lpas.append(LPA[y])
            if len(cars) >= 6:
                _, mb, mt, n = ols(np.array(cars), np.array(lpas, float) - np.mean(lpas))
                name_win[t][w] = {"beta": round(mb, 4), "t": round(mt, 2), "n": n}
                star = "*" if (np.isfinite(mt) and abs(mt) >= 1.5 and mb > 0) else " "
                row += f"{mb:+.4f}({mt:+.1f}){star:>2}".rjust(16)
            else:
                name_win[t][w] = None
                row += f"{'na':>16}"
        print(row)

    # ── B) group-mean CAR by regime, per window ──────────────────────────────
    print("\nGROUP-MEAN CAR by REGIME and WINDOW  (normal>=96 vs deficient<96)")
    group_stats = {}
    for w in WINDOWS:
        print(f"\n  [{w}]  {'GROUP':12}{'NORM%':>9}{'DEF%':>9}{'N-D%':>9}{'allyr%':>9}{'t(all)':>9}")
        group_stats[w] = {}
        for g, members in GROUPS.items():
            allc, normc, defc = [], [], []
            for y in years:
                vals = []
                for t in members:
                    if t in closes:
                        idx = closes[t].index.intersection(rm.index)
                        c = win_car(closes[t].loc[idx], rm.loc[idx], y, w)
                        if c is not None:
                            vals.append(c)
                if vals:
                    gm = float(np.mean(vals))
                    allc.append(gm)
                    (normc if LPA[y] >= NORMAL_CUT else defc).append(gm)
            if allc:
                arr = np.array(allc)
                tall = arr.mean() / (arr.std(ddof=1) / np.sqrt(len(arr))) if len(arr) > 1 and arr.std(ddof=1) > 0 else np.nan
                nd = (np.mean(normc) - np.mean(defc)) if normc and defc else np.nan
                group_stats[w][g] = {
                    "normal_pct": round(np.mean(normc) * 100, 2) if normc else None,
                    "deficient_pct": round(np.mean(defc) * 100, 2) if defc else None,
                    "nd_pct": round(nd * 100, 2) if np.isfinite(nd) else None,
                    "allyr_pct": round(arr.mean() * 100, 2), "t_all": round(tall, 2),
                }
                gs = group_stats[w][g]
                print(f"  {'':6}{g:12}{str(gs['normal_pct']):>9}{str(gs['deficient_pct']):>9}"
                      f"{str(gs['nd_pct']):>9}{str(gs['allyr_pct']):>9}{str(gs['t_all']):>9}")

    OUT.write_text(json.dumps({"name_win": name_win, "group_stats": group_stats}, indent=2))
    print(f"\nWrote {OUT}")


if __name__ == "__main__":
    main()
