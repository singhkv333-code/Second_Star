"""IT-in-trouble — event study + connectedness regressions (REAL yfinance data).

View Markets view: "India's IT giants are in trouble."

Measurable framing
-------------------
Indian large-cap IT (TCS / INFY / WIPRO / HCLTECH / TECHM, proxied by Nifty IT
^CNXIT) UNDERPERFORMS NIFTY (^NSEI) over the ~1-4 weeks around a weak-results /
guidance-cut earnings print. "Trouble" = demand / discretionary-spend /
guidance-cut driven, NOT an FX call (a FALLING rupee HELPS IT margins — the
connectedness block proves IT returns load POSITIVELY on USDINR-up, so rupee
weakness is a tailwind, not the source of trouble).

Method (event study, market-model abnormal returns)
---------------------------------------------------
* Estimation window  : [t0-130, t0-11]  (120 trading days), OLS r_i ~ a + b*r_nifty
* Event window (react): [t0-1,  t0+5]    immediate guidance reaction
* Drift window (PEAD) : [t0+1,  t0+20]   post-print drift
* CAAR = mean CAR across the analog event sample; t = CAAR / (sd(CAR)/sqrt(N))

Connectedness (anti-spurious — the user's explicit ask)
-------------------------------------------------------
Full-sample daily OLS, separates genuine IT-sector beta from market beta + FX:
  r_i = a + b_mkt*r_nifty + b_fx*dlog(USDINR) + b_it*IT_resid + e
where IT_resid = Nifty-IT return orthogonalised to NIFTY (the IT-specific
factor). A name is GENUINELY connected to the IT-trouble view iff b_it is
large & significant (loads on the IT-specific factor) — not merely b_mkt.

Pure yfinance + numpy. No Azure/LLM. Real numbers only; degrades + says so on
missing data. Run with the repo venv:
    pivot/.venv/bin/python pivot/scripts/strategy_research/it_trouble_event_study.py
"""
from __future__ import annotations

import sys
import warnings
from datetime import datetime

import numpy as np
import pandas as pd

warnings.simplefilter("ignore")

try:
    import yfinance as yf
except Exception as e:  # pragma: no cover
    print(f"yfinance import failed: {e}", file=sys.stderr)
    sys.exit(1)

# ── Universe ────────────────────────────────────────────────────────────────
IT_LARGE = ["TCS.NS", "INFY.NS", "WIPRO.NS", "HCLTECH.NS", "TECHM.NS"]
IT_MID = ["LTIM.NS", "COFORGE.NS", "PERSISTENT.NS", "MPHASIS.NS"]
# Controls / defensives (the rotation destination) + spurious-market names.
DEFENSIVES = ["HINDUNILVR.NS", "NESTLEIND.NS", "ITC.NS", "DABUR.NS",
              "SUNPHARMA.NS", "CIPLA.NS"]
SPURIOUS = ["RELIANCE.NS", "MARUTI.NS", "HDFCBANK.NS"]  # big NIFTY weights, not IT
BENCH = "^NSEI"          # NIFTY 50
SECTOR = "^CNXIT"        # Nifty IT
USDINR = "INR=X"         # USD/INR (up = rupee weaker = IT tailwind)

UNIVERSE = IT_LARGE + IT_MID + DEFENSIVES + SPURIOUS
ALL_TICKERS = list(dict.fromkeys(UNIVERSE + [BENCH, SECTOR, USDINR]))

# ── Analog events ───────────────────────────────────────────────────────────
# TCS result date kicks off IT earnings season each quarter (first large-cap to
# report). ALL anchors give the unconditional benchmark; WEAK_ANALOGS are the
# quarters real market history flagged as weak / guidance-cut (the view's driver).
ALL_ANCHORS = [
    "2022-04-11", "2022-07-08", "2022-10-10", "2023-01-09",
    "2023-04-12", "2023-07-12", "2023-10-11", "2024-01-11",
    "2024-04-12", "2024-07-11", "2024-10-10", "2025-01-09",
]
# Curated weak / guidance-cut prints (rationale in the report):
#  2022-04-11 Q4FY22 margin/attrition peak; 2022-07-08 Q1FY23 margin squeeze;
#  2023-01-09 Q3FY23 cautious discretionary; 2023-04-12 Q4FY23 Infy guidance
#  SHOCK-cut to 4-7%; 2023-07-12 Q1FY24 Infy cut FY24 to 1-3.5%; 2023-10-11
#  Q2FY24 muted; 2024-04-12 Q4FY24 soft FY25 guide; 2025-01-09 Q3FY25 cautious.
WEAK_ANALOGS = [
    "2022-04-11", "2022-07-08", "2023-01-09", "2023-04-12",
    "2023-07-12", "2023-10-11", "2024-04-12", "2025-01-09",
]

EST_LEN, EST_GAP = 120, 10
REACT_WIN = (-1, 5)
DRIFT_WIN = (1, 20)


def fetch() -> pd.DataFrame:
    print(f"Downloading {len(ALL_TICKERS)} symbols from yfinance (2021-01-01 → today)...")
    raw = yf.download(ALL_TICKERS, start="2021-01-01", end="2025-12-31",
                      auto_adjust=True, progress=False)
    close = raw["Close"] if isinstance(raw.columns, pd.MultiIndex) else raw
    close = close.dropna(how="all")
    got = [c for c in ALL_TICKERS if c in close.columns and close[c].notna().sum() > 200]
    missing = [c for c in ALL_TICKERS if c not in got]
    if missing:
        print(f"  WARNING degraded — insufficient data, dropped: {missing}")
    return close[got]


def logret(s: pd.Series) -> pd.Series:
    return np.log(s / s.shift(1))


def ols(y: np.ndarray, X: np.ndarray):
    """Return (beta vector, tstat vector, r2). X includes intercept column."""
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    resid = y - X @ beta
    n, k = X.shape
    dof = n - k
    if dof <= 0:
        return beta, np.full(k, np.nan), np.nan
    sigma2 = (resid @ resid) / dof
    XtX_inv = np.linalg.inv(X.T @ X)
    se = np.sqrt(np.diag(sigma2 * XtX_inv)) if (sigma2 := sigma2) >= 0 else np.full(k, np.nan)
    tstat = beta / se
    ss_tot = ((y - y.mean()) ** 2).sum()
    r2 = 1 - (resid @ resid) / ss_tot if ss_tot > 0 else np.nan
    return beta, tstat, r2


def event_study(rets: pd.DataFrame, names: list[str], anchors: list[str],
                bench_col: str):
    """Market-model CAAR over react + drift windows for each name across anchors."""
    idx = rets.index
    nifty = rets[bench_col].values
    out = {}
    for name in names:
        if name not in rets.columns:
            continue
        y_all = rets[name].values
        react_cars, drift_cars = [], []
        for a in anchors:
            adate = pd.Timestamp(a)
            pos = idx.searchsorted(adate)
            if pos >= len(idx):
                continue
            t0 = pos
            est_lo, est_hi = t0 - EST_GAP - EST_LEN, t0 - EST_GAP
            if est_lo < 0 or t0 + DRIFT_WIN[1] >= len(idx):
                continue
            ny = nifty[est_lo:est_hi]
            sy = y_all[est_lo:est_hi]
            m = np.isfinite(ny) & np.isfinite(sy)
            if m.sum() < 60:
                continue
            X = np.column_stack([np.ones(m.sum()), ny[m]])
            beta, *_ = np.linalg.lstsq(X, sy[m], rcond=None)
            a0, b1 = beta

            def car(lo, hi):
                seg = slice(t0 + lo, t0 + hi + 1)
                ar = y_all[seg] - (a0 + b1 * nifty[seg])
                ar = ar[np.isfinite(ar)]
                return float(ar.sum())

            react_cars.append(car(*REACT_WIN))
            drift_cars.append(car(*DRIFT_WIN))

        def agg(cars):
            arr = np.array(cars, dtype=float)
            if len(arr) < 2:
                return (np.nan, np.nan, len(arr))
            caar = arr.mean()
            t = caar / (arr.std(ddof=1) / np.sqrt(len(arr))) if arr.std(ddof=1) > 0 else np.nan
            return (caar, t, len(arr))

        out[name] = {"react": agg(react_cars), "drift": agg(drift_cars),
                     "react_cars": react_cars, "drift_cars": drift_cars}
    return out


def connectedness(rets: pd.DataFrame, names: list[str], bench_col: str,
                  sector_col: str, fx_col: str):
    """Full-sample OLS separating market beta, FX beta, IT-specific beta."""
    df = rets[[c for c in [bench_col, sector_col, fx_col] if c in rets.columns] +
              [n for n in names if n in rets.columns]].dropna()
    nifty = df[bench_col].values
    # IT-specific factor = Nifty IT orthogonalised to NIFTY.
    Xb = np.column_stack([np.ones(len(nifty)), nifty])
    bb, *_ = np.linalg.lstsq(Xb, df[sector_col].values, rcond=None)
    it_resid = df[sector_col].values - Xb @ bb
    fx = df[fx_col].values if fx_col in df.columns else np.zeros(len(df))
    rows = []
    for name in names:
        if name not in df.columns:
            continue
        y = df[name].values
        X = np.column_stack([np.ones(len(y)), nifty, fx, it_resid])
        beta, t, r2 = ols(y, X)
        rows.append({
            "name": name, "b_mkt": beta[1], "t_mkt": t[1],
            "b_fx": beta[2], "t_fx": t[2],
            "b_it": beta[3], "t_it": t[3], "r2": r2,
        })
    return pd.DataFrame(rows), len(df)


def main():
    close = fetch()
    rets = close.apply(logret).dropna(how="all")
    print(f"\nReturn matrix: {rets.shape[0]} days × {rets.shape[1]} symbols "
          f"({rets.index[0].date()} → {rets.index[-1].date()})\n")
    have = lambda lst: [x for x in lst if x in rets.columns]
    study_names = have(IT_LARGE + IT_MID + DEFENSIVES + SPURIOUS)

    for label, anchors in [("ALL EARNINGS ANCHORS", ALL_ANCHORS),
                           ("WEAK / GUIDANCE-CUT ANALOGS", WEAK_ANALOGS)]:
        print("=" * 78)
        print(f"EVENT STUDY — {label}  (N anchors = {len(anchors)})")
        print(f"  market-model abnormal return vs NIFTY; react={REACT_WIN} drift={DRIFT_WIN}")
        print("=" * 78)
        res = event_study(rets, study_names + [SECTOR], anchors, BENCH)
        print(f"{'name':14s} {'react CAAR%':>11s} {'t':>6s} {'N':>3s}  "
              f"{'drift CAAR%':>11s} {'t':>6s} {'N':>3s}")
        order = sorted(res.items(), key=lambda kv: (kv[1]['react'][0]
                       if np.isfinite(kv[1]['react'][0]) else 0))
        for name, d in order:
            rc, rt, rn = d["react"]; dc, dt, dn = d["drift"]
            print(f"{name:14s} {rc*100:11.2f} {rt:6.2f} {rn:3d}  "
                  f"{dc*100:11.2f} {dt:6.2f} {dn:3d}")
        print()

    print("=" * 78)
    print("CONNECTEDNESS — full-sample daily OLS  r_i ~ NIFTY + dUSDINR + IT_resid")
    print("  b_it = loading on the IT-specific factor (Nifty IT ⟂ NIFTY)")
    print("  b_fx = USDINR loading (POSITIVE ⇒ rupee-weakness is a TAILWIND for the name)")
    print("=" * 78)
    conn, nobs = connectedness(rets, study_names, BENCH, SECTOR, USDINR)
    conn = conn.sort_values("b_it", ascending=False)
    print(f"(n = {nobs} daily obs)")
    print(f"{'name':14s} {'b_mkt':>7s} {'t':>6s} {'b_fx':>7s} {'t':>6s} "
          f"{'b_it':>7s} {'t':>6s} {'R2':>5s}  connected?")
    for _, r in conn.iterrows():
        connected = "YES" if (r.b_it > 0.30 and abs(r.t_it) > 3) else "no"
        print(f"{r['name']:14s} {r.b_mkt:7.2f} {r.t_mkt:6.1f} {r.b_fx:7.2f} "
              f"{r.t_fx:6.1f} {r.b_it:7.2f} {r.t_it:6.1f} {r.r2:5.2f}  {connected}")

    # FX sanity: regress Nifty IT itself on USDINR.
    print("\nFX nuance check — Nifty IT daily return ~ dUSDINR:")
    df = rets[[SECTOR, USDINR]].dropna()
    X = np.column_stack([np.ones(len(df)), df[USDINR].values])
    b, t, r2 = ols(df[SECTOR].values, X)
    print(f"  b_fx(Nifty IT) = {b[1]:+.2f}  t = {t[1]:.2f}  "
          f"→ {'rupee-WEAKNESS helps IT (tailwind)' if b[1] > 0 else 'rupee-weakness hurts IT'}")
    print("  ⇒ 'IT in trouble' must be a DEMAND/GUIDANCE call, not an FX call.\n")


if __name__ == "__main__":
    main()
