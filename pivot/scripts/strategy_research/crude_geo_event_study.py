"""Crude / geopolitical-shock view — EVENT STUDY (yfinance, Opus-designed).

Run with the repo venv:
    pivot/.venv/bin/python pivot/scripts/strategy_research/crude_geo_event_study.py

What it does (the user's preferred lens — "who ACTUALLY moved, and were they
REALLY connected to the driver?"):

  1. Pulls daily history (yfinance) for a crude-sensitive NSE candidate universe
     + NIFTY (^NSEI, the benchmark) + Brent (BZ=F, the DRIVER).
  2. Detects ANALOG crude-shock events directly from Brent: clustered 10-trading-
     day moves above a magnitude threshold, split into SPIKE (escalation) and
     CRASH (de-escalation / demand collapse) samples, de-duplicated to local
     extremes. Named geopolitical context is annotated for the nearest known flare.
  3. MARKET-MODEL abnormal returns vs NIFTY: estimation window [-140,-21],
     event window [-5,+20]. Per-name CAAR across the analog sample + cross-event
     t-stat. Ranks who actually moved.
  4. CONNECTEDNESS regressions on the FULL daily sample:
        r_i = a + b_brent * r_Brent + b_mkt * r_NIFTY  (controlled, the real test)
     separates a genuine crude-beta from plain market beta. Reports brent-beta,
     market-beta, their t-stats, R^2, and a connected? yes/no with expected sign.

NO fabrication: every number is printed from a real run. If a symbol returns no
data it is dropped and reported, never invented.
"""
from __future__ import annotations

import json
import os
import sys
import warnings
from dataclasses import dataclass, asdict
from datetime import datetime

import numpy as np
import pandas as pd
from scipy import stats

warnings.filterwarnings("ignore")

import yfinance as yf

OUT_DIR = os.path.join(os.path.dirname(__file__), "_out")
os.makedirs(OUT_DIR, exist_ok=True)
CACHE = os.path.join(OUT_DIR, "crude_prices.pkl")

START = "2016-01-01"
END = datetime.utcnow().strftime("%Y-%m-%d")

BENCH = "^NSEI"      # NIFTY 50 — benchmark
DRIVER = "BZ=F"      # Brent crude front-month — the event DRIVER

# Crude-sensitive candidate universe (role = a-priori hypothesis, TESTED below).
# sign_hyp = expected sign of the Brent-beta: +1 helped by crude UP, -1 hurt.
UNIVERSE: dict[str, tuple[str, int]] = {
    # Upstream producers — realise higher crude price (helped by crude UP)
    "ONGC.NS": ("upstream", +1),
    "OIL.NS": ("upstream", +1),
    # Oil marketing companies / refiners — import crude, marketing-margin squeezed
    "BPCL.NS": ("omc", -1),
    "HINDPETRO.NS": ("omc", -1),   # HPCL
    "IOC.NS": ("omc", -1),
    # Integrated (refining + petchem + upstream + now telecom/retail)
    "RELIANCE.NS": ("integrated", 0),
    # Paints — crude derivatives (monomers/solvents/TiO2) are key inputs
    "ASIANPAINT.NS": ("paints", -1),
    "BERGEPAINT.NS": ("paints", -1),
    # Aviation — ATF (jet fuel) is ~40% of cost
    "INDIGO.NS": ("aviation", -1),
    # Tyres — crude-linked carbon black / synthetic rubber inputs
    "MRF.NS": ("tyres", -1),
    "APOLLOTYRE.NS": ("tyres", -1),
    # Gold ETF — escalation hedge
    "GOLDBEES.NS": ("gold_hedge", +1),
}

# Known geopolitical / macro crude episodes for annotation (nearest-label only).
NAMED_EVENTS = {
    "2018-10-03": "Brent ~$86 peak (Iran-sanctions fear) then slide",
    "2020-03-09": "OPEC+ breakup + COVID demand crash (Brent collapse)",
    "2020-04-21": "WTI negative / Brent sub-$20 trough",
    "2022-02-24": "Russia invades Ukraine (Brent spikes >$100)",
    "2022-03-08": "Brent ~$128 post-invasion peak",
    "2023-10-07": "Hamas attack / Israel-Gaza war begins",
    "2024-04-13": "Iran direct strike on Israel",
    "2024-10-01": "Iran missile barrage on Israel",
    "2025-06-13": "Israel-Iran direct strikes (Brent jumps)",
}

EST_PRE, EST_POST = -140, -21     # estimation window (rel. to event day 0)
EVT_PRE, EVT_POST = -5, +20       # event window
SHOCK_THRESH = 0.15               # |10-trading-day Brent return| >= 15% = a shock
MIN_GAP = 25                      # trading days between distinct event anchors


def _flatten(df: pd.DataFrame, sym: str) -> pd.Series | None:
    """Pull the adjusted close Series for one symbol out of a yf.download frame."""
    if df is None or len(df) == 0:
        return None
    if isinstance(df.columns, pd.MultiIndex):
        # layout (field, ticker) after group_by='column'
        for field in ("Close", "Adj Close"):
            if (field, sym) in df.columns:
                return df[(field, sym)].dropna()
        # layout (ticker, field)
        if sym in df.columns.get_level_values(0):
            sub = df[sym]
            for field in ("Close", "Adj Close"):
                if field in sub.columns:
                    return sub[field].dropna()
        return None
    for field in ("Close", "Adj Close"):
        if field in df.columns:
            return df[field].dropna()
    return None


def load_prices() -> pd.DataFrame:
    if os.path.exists(CACHE):
        px = pd.read_pickle(CACHE)
        print(f"[cache] loaded {px.shape[1]} symbols x {px.shape[0]} rows from {CACHE}")
        return px
    syms = list(UNIVERSE) + [BENCH, DRIVER]
    print(f"[yfinance] downloading {len(syms)} symbols {START}..{END} ...")
    raw = yf.download(syms, start=START, end=END, auto_adjust=True,
                      progress=False, group_by="column", threads=True)
    cols: dict[str, pd.Series] = {}
    missing: list[str] = []
    for s in syms:
        ser = _flatten(raw, s)
        if ser is None or ser.dropna().shape[0] < 250:
            missing.append(s)
            continue
        ser.index = pd.to_datetime(ser.index).tz_localize(None)
        cols[s] = ser
    if missing:
        print(f"[warn] no/short data, DROPPED: {missing}")
    px = pd.DataFrame(cols).sort_index()
    px.to_pickle(CACHE)
    print(f"[yfinance] cached {px.shape[1]} symbols x {px.shape[0]} rows -> {CACHE}")
    return px


def detect_events(brent: pd.Series) -> pd.DataFrame:
    """Cluster 10-trading-day Brent moves into de-duplicated SPIKE/CRASH anchors."""
    b = brent.dropna()
    ret10 = b.pct_change(10)
    cand = ret10[ret10.abs() >= SHOCK_THRESH]
    anchors: list[tuple[pd.Timestamp, float, str]] = []
    used: list[int] = []
    idx = b.index
    pos = {d: i for i, d in enumerate(idx)}
    # walk candidates, greedily take local extreme within +-MIN_GAP
    for d in cand.index:
        i = pos[d]
        if any(abs(i - u) < MIN_GAP for u in used):
            continue
        lo = max(0, i - MIN_GAP // 2)
        hi = min(len(idx), i + MIN_GAP // 2)
        window = ret10.iloc[lo:hi]
        # local extreme of the same sign
        sign = np.sign(ret10.iloc[i])
        same = window[np.sign(window) == sign]
        if same.empty:
            continue
        ext_date = same.abs().idxmax()
        ei = pos[ext_date]
        if any(abs(ei - u) < MIN_GAP for u in used):
            continue
        used.append(ei)
        anchors.append((ext_date, float(ret10.loc[ext_date]),
                        "SPIKE" if ret10.loc[ext_date] > 0 else "CRASH"))
    rows = []
    for d, r, kind in sorted(anchors):
        label = ""
        for nd, lbl in NAMED_EVENTS.items():
            if abs((d - pd.Timestamp(nd)).days) <= 20:
                label = lbl
                break
        rows.append({"date": d, "brent_10d_ret": r, "kind": kind, "context": label})
    return pd.DataFrame(rows)


def ols(y: np.ndarray, X: np.ndarray) -> tuple[np.ndarray, np.ndarray, float]:
    """OLS with classical SEs. X includes intercept column. Returns (beta, t, R2)."""
    XtX = X.T @ X
    XtX_inv = np.linalg.inv(XtX)
    beta = XtX_inv @ X.T @ y
    resid = y - X @ beta
    n, k = X.shape
    dof = max(n - k, 1)
    sigma2 = (resid @ resid) / dof
    se = np.sqrt(np.diag(sigma2 * XtX_inv))
    tvals = beta / se
    ss_tot = ((y - y.mean()) ** 2).sum()
    r2 = 1.0 - (resid @ resid) / ss_tot if ss_tot > 0 else float("nan")
    return beta, tvals, r2


def market_model_car(rets: pd.DataFrame, events: pd.DataFrame) -> dict:
    """Per-name CAAR over the analog sample + cross-event t-stat (market model)."""
    mkt = rets[BENCH]
    names = [s for s in UNIVERSE if s in rets.columns]
    dates = rets.index
    pos = {d: i for i, d in enumerate(dates)}
    out: dict[str, dict] = {}
    for kind in ("SPIKE", "CRASH"):
        evs = events[events["kind"] == kind]
        for s in names:
            cars: list[float] = []
            for d in evs["date"]:
                if d not in pos:
                    # snap to nearest trading day
                    near = dates[dates.get_indexer([d], method="nearest")][0]
                    d = near
                i = pos[d]
                est_lo, est_hi = i + EST_PRE, i + EST_POST
                evt_lo, evt_hi = i + EVT_PRE, i + EVT_POST
                if est_lo < 0 or evt_hi >= len(dates):
                    continue
                ry = rets[s].iloc[est_lo:est_hi].values
                rm = mkt.iloc[est_lo:est_hi].values
                ok = np.isfinite(ry) & np.isfinite(rm)
                if ok.sum() < 60:
                    continue
                X = np.column_stack([np.ones(ok.sum()), rm[ok]])
                beta, _, _ = ols(ry[ok], X)
                a, b = beta[0], beta[1]
                ey = rets[s].iloc[evt_lo:evt_hi].values
                em = mkt.iloc[evt_lo:evt_hi].values
                ar = ey - (a + b * em)
                car = np.nansum(ar)
                if np.isfinite(car):
                    cars.append(float(car))
            if len(cars) >= 2:
                arr = np.array(cars)
                t = arr.mean() / (arr.std(ddof=1) / np.sqrt(len(arr))) if arr.std(ddof=1) > 0 else float("nan")
                out.setdefault(kind, {})[s] = {
                    "caar_pct": round(arr.mean() * 100, 2),
                    "t_stat": round(float(t), 2),
                    "n_events": len(cars),
                    "role": UNIVERSE[s][0],
                }
    return out


def connectedness(rets: pd.DataFrame) -> dict:
    """Controlled regression r_i ~ a + b_brent*Brent + b_mkt*NIFTY (full sample)."""
    brent = rets[DRIVER]
    mkt = rets[BENCH]
    names = [s for s in UNIVERSE if s in rets.columns]
    out: dict[str, dict] = {}
    for s in names:
        df = pd.concat([rets[s], brent, mkt], axis=1, keys=["y", "b", "m"]).dropna()
        if len(df) < 200:
            continue
        y = df["y"].values
        X = np.column_stack([np.ones(len(df)), df["b"].values, df["m"].values])
        beta, tv, r2 = ols(y, X)
        # univariate Brent-beta (no market control) for contrast
        Xu = np.column_stack([np.ones(len(df)), df["b"].values])
        bu, tu, _ = ols(y, Xu)
        b_brent, t_brent = beta[1], tv[1]
        sign_hyp = UNIVERSE[s][1]
        sign_ok = (sign_hyp == 0) or (np.sign(b_brent) == np.sign(sign_hyp))
        connected = bool(abs(t_brent) >= 2.0 and sign_ok)
        out[s] = {
            "role": UNIVERSE[s][0],
            "brent_beta": round(float(b_brent), 3),
            "brent_beta_t": round(float(t_brent), 2),
            "brent_beta_uni": round(float(bu[1]), 3),
            "market_beta": round(float(beta[2]), 3),
            "market_beta_t": round(float(tv[2]), 2),
            "r2": round(float(r2), 3),
            "n": len(df),
            "sign_hyp": sign_hyp,
            "sign_ok": bool(sign_ok),
            "connected": connected,
        }
    return out


def main() -> None:
    px = load_prices()
    print("\nSymbols with data:", list(px.columns))
    print("Date range:", px.index.min().date(), "->", px.index.max().date())
    rets = px.pct_change()
    # Clean obvious yfinance data errors (split/adjustment junctions). Indian
    # equities/ETFs are circuit-limited to <=20%/day; indices & Brent rarely move
    # >50% in a day. Any |daily return| > 0.5 is a bad tick (e.g. GOLDBEES.NS has a
    # 100x adjustment glitch on 2019-12-23). Drop to NaN — never invent a value.
    bad = (rets.abs() > 0.5).sum().sum()
    rets = rets.mask(rets.abs() > 0.5)
    rets = rets.dropna(how="all")
    print(f"[clean] masked {int(bad)} bad-tick daily returns (|r|>0.5) across all symbols")

    events = detect_events(px[DRIVER])
    print("\n=== DETECTED ANALOG CRUDE EVENTS (from Brent 10d move) ===")
    for _, r in events.iterrows():
        print(f"  {r['date'].date()}  {r['kind']:5s}  Brent10d={r['brent_10d_ret']*100:+6.1f}%  {r['context']}")
    print(f"  total: {len(events)} events "
          f"({(events['kind']=='SPIKE').sum()} spikes / {(events['kind']=='CRASH').sum()} crashes)")

    car = market_model_car(rets, events)
    for kind in ("SPIKE", "CRASH"):
        print(f"\n=== MARKET-MODEL CAAR vs NIFTY — {kind} sample "
              f"(event window [{EVT_PRE},{EVT_POST}]) ===")
        rows = car.get(kind, {})
        ranked = sorted(rows.items(), key=lambda kv: kv[1]["caar_pct"], reverse=True)
        print(f"  {'symbol':14s} {'role':11s} {'CAAR%':>8s} {'t':>6s} {'N':>3s}")
        for s, d in ranked:
            star = " *" if abs(d["t_stat"]) >= 1.7 else ""
            print(f"  {s:14s} {d['role']:11s} {d['caar_pct']:>8.2f} {d['t_stat']:>6.2f} {d['n_events']:>3d}{star}")

    conn = connectedness(rets)
    print("\n=== CONNECTEDNESS — controlled OLS  r_i ~ a + b_Brent*Brent + b_mkt*NIFTY (full daily sample) ===")
    print(f"  {'symbol':14s} {'role':11s} {'bBrent':>7s} {'t':>6s} {'bMkt':>6s} {'t':>6s} {'R2':>6s} {'conn?':>6s}")
    for s, d in sorted(conn.items(), key=lambda kv: kv[1]["brent_beta_t"], reverse=True):
        print(f"  {s:14s} {d['role']:11s} {d['brent_beta']:>7.3f} {d['brent_beta_t']:>6.2f} "
              f"{d['market_beta']:>6.2f} {d['market_beta_t']:>6.1f} {d['r2']:>6.3f} {str(d['connected']):>6s}")

    blob = {
        "generated": datetime.utcnow().isoformat(),
        "events": [{**r, "date": r["date"].strftime("%Y-%m-%d")} for r in events.to_dict("records")],
        "caar": car,
        "connectedness": conn,
    }
    with open(os.path.join(OUT_DIR, "crude_event_study.json"), "w") as f:
        json.dump(blob, f, indent=2, default=str)
    print(f"\n[saved] {os.path.join(OUT_DIR, 'crude_event_study.json')}")


if __name__ == "__main__":
    main()
