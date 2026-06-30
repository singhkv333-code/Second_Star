"""Monsoon (IMD LPA%) event study — View Markets "Weather/Monsoon" view.

REPRODUCIBLE quant research (Opus-designed, NO backend LLM). Data = yfinance only.

Framing under test (the most relatable Indian-retail monsoon view):
    "A NORMAL/above-normal monsoon (IMD seasonal rainfall >= ~96% of the Long
     Period Average) lifts RURAL incomes -> rural-skewed demand (two-wheelers,
     tractors, agri-inputs, rural FMCG) OUTPERFORMS NIFTY over the Jun-Sep
     south-west monsoon season."

Why this framing (vs the deficient-monsoon irrigation/pumps hedge):
  * The resolver is unambiguous and official: IMD's end-of-season LPA%.
  * ~55-60% of India's population is rural and a large share of FMCG/2W/tractor
    volumes are rural -> a normal monsoon is the single most-cited "good for the
    aam aadmi" macro event. It is the framing a retail user actually asks about.
  * It is LONG-only and defined-risk-friendly (no forced single-stock short),
    which fits register-not-execute + India microstructure.

METHOD (event study, the user's preferred lens):
  1. Outcome = season cumulative ABNORMAL return (CAR) of each candidate vs NIFTY
     over the SW-monsoon window [Jun 1 .. Sep 30], market-model adjusted.
  2. Analog sample = the historical monsoon seasons, each tagged with the known
     IMD LPA% and a NORMAL (>=96) / DEFICIENT (<96) regime.
  3. Market model: estimate (alpha,beta) on the PRE-season estimation window
     (Jan 1 .. May 31 of the same year, ~100 trading days). Abnormal return
     AR_t = r_t - (alpha + beta*r_nifty_t); CAR = sum over the season.
     AAR/CAAR + t-stats across the analog sample, split by regime.
  4. CONNECTEDNESS (the "were they REALLY connected?" test): for each name
     regress its per-year season CAR on that year's IMD LPA% (centred). The
     slope = MONSOON-BETA (sensitivity of seasonal out/under-performance to the
     rainfall outcome). market-beta comes from the daily market model. A name is
     GENUINELY monsoon-connected only if monsoon-beta is positive AND its
     across-year t-stat is meaningful; otherwise its season moves are market
     beta / idiosyncratic = SPURIOUS for this view.

Everything printed is computed from real yfinance pulls. No fabricated numbers.
"""
from __future__ import annotations

import json
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

warnings.filterwarnings("ignore")

OUT = Path(__file__).resolve().parent / "monsoon_event_study_out.json"
NIFTY = "^NSEI"

# ── IMD all-India SW-monsoon seasonal rainfall, % of Long Period Average ──────
# Source: IMD end-of-season statements (well-documented public record). NORMAL
# band is 96-104%; <96 = below-normal/deficient, >104 = above-normal. These are
# the RESOLVER values for the view's analog sample.
LPA = {
    2009: 78,   # severe drought
    2010: 102,
    2011: 102,
    2012: 93,   # below normal
    2013: 106,
    2014: 88,   # deficient
    2015: 86,   # deficient (back-to-back)
    2016: 97,
    2017: 95,   # marginally below
    2018: 91,   # below normal
    2019: 110,  # above normal
    2020: 109,  # above normal
    2021: 99,
    2022: 106,
    2023: 94,   # below normal (El Nino)
    2024: 108,  # above normal
}
NORMAL_CUT = 96  # >=96 == "normal/above" regime

# ── Candidate universe (rural-skewed theme + urban controls) ─────────────────
UNIVERSE = {
    # Two-wheelers / tractors / rural autos
    "M&M.NS": "Auto/Tractor (M&M)",
    "ESCORTS.NS": "Tractor (Escorts Kubota)",
    "HEROMOTOCO.NS": "Two-wheeler (Hero)",
    "TVSMOTOR.NS": "Two-wheeler (TVS)",
    "BAJAJ-AUTO.NS": "Two-wheeler (Bajaj Auto)",
    # Agri-inputs / fertiliser / crop-protection
    "COROMANDEL.NS": "Fertiliser (Coromandel)",
    "UPL.NS": "Agrochem (UPL)",
    "RALLIS.NS": "Agrochem (Rallis)",
    "PIIND.NS": "Agrochem (PI Inds)",
    "CHAMBLFERT.NS": "Fertiliser (Chambal)",
    # Rural-skewed FMCG
    "HINDUNILVR.NS": "FMCG (HUL)",
    "DABUR.NS": "FMCG (Dabur)",
    "MARICO.NS": "FMCG (Marico)",
    "ITC.NS": "FMCG (ITC)",
    "EMAMILTD.NS": "FMCG (Emami)",
    "GODREJCP.NS": "FMCG (Godrej CP)",
    "TATACONSUM.NS": "FMCG (Tata Consumer)",
    # Urban / non-rural CONTROLS (should be weakly/not monsoon-connected)
    "ASIANPAINT.NS": "Urban-discretionary (Asian Paints)",
    "TITAN.NS": "Urban-discretionary (Titan)",
    "BAJFINANCE.NS": "Urban-financial (Bajaj Finance)",
}

START = "2008-10-01"
END = "2025-01-01"


def fetch_close(ticker: str) -> pd.Series | None:
    df = yf.download(ticker, start=START, end=END, progress=False, auto_adjust=True)
    if df is None or df.empty:
        return None
    close = df["Close"]
    if isinstance(close, pd.DataFrame):
        close = close.iloc[:, 0]
    close = close.dropna()
    return close if len(close) > 250 else None


def daily_ret(s: pd.Series) -> pd.Series:
    return s.pct_change().dropna()


def ols(y: np.ndarray, x: np.ndarray):
    """Simple OLS y = a + b*x. Returns (a, b, b_tstat, r2, n)."""
    n = len(y)
    if n < 5:
        return (np.nan, np.nan, np.nan, np.nan, n)
    X = np.column_stack([np.ones(n), x])
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    a, b = beta
    resid = y - X @ beta
    dof = n - 2
    if dof <= 0:
        return (a, b, np.nan, np.nan, n)
    sigma2 = (resid @ resid) / dof
    xtx_inv = np.linalg.inv(X.T @ X)
    se_b = np.sqrt(sigma2 * xtx_inv[1, 1])
    tb = b / se_b if se_b > 0 else np.nan
    ss_tot = np.sum((y - y.mean()) ** 2)
    r2 = 1 - (resid @ resid) / ss_tot if ss_tot > 0 else np.nan
    return (a, b, tb, r2, n)


def season_car(rs: pd.Series, rm: pd.Series, year: int):
    """Market-model season CAR for one year. Estimation Jan1-May31, event Jun1-Sep30."""
    est = (rs.index >= f"{year}-01-01") & (rs.index <= f"{year}-05-31")
    evt = (rs.index >= f"{year}-06-01") & (rs.index <= f"{year}-09-30")
    ys, xs = rs[est].values, rm[est].values
    if len(ys) < 40 or evt.sum() < 40:
        return None
    a, b, *_ = ols(ys, xs)
    if not np.isfinite(b):
        return None
    ar = rs[evt].values - (a + b * rm[evt].values)
    car = float(np.sum(ar))           # cumulative abnormal return over season
    raw = float(np.sum(rs[evt].values))   # raw season return
    mkt = float(np.sum(rm[evt].values))   # nifty season return
    return {"car": car, "raw": raw, "mkt": mkt, "beta_est": float(b), "n_evt": int(evt.sum())}


def tstat(x: np.ndarray) -> float:
    x = np.asarray([v for v in x if np.isfinite(v)], dtype=float)
    if len(x) < 2 or x.std(ddof=1) == 0:
        return np.nan
    return float(x.mean() / (x.std(ddof=1) / np.sqrt(len(x))))


def main() -> None:
    print("Fetching NIFTY benchmark ...")
    nifty = fetch_close(NIFTY)
    assert nifty is not None, "NIFTY download failed"
    rm = daily_ret(nifty)

    closes: dict[str, pd.Series] = {}
    for t in UNIVERSE:
        s = fetch_close(t)
        if s is None:
            print(f"  [skip] {t}: insufficient yfinance history")
            continue
        closes[t] = s
    print(f"Fetched {len(closes)}/{len(UNIVERSE)} names.\n")

    years = sorted(LPA)
    rows = []
    per_name_year: dict[str, dict[int, dict]] = {}

    for t, s in closes.items():
        rs = daily_ret(s)
        idx = rs.index.intersection(rm.index)
        rs2, rm2 = rs.loc[idx], rm.loc[idx]
        # daily market beta over the FULL overlapping sample
        a_d, mbeta, mt, mr2, nd = ols(rs2.values, rm2.values)

        cars, lpas, used_years = [], [], []
        normal_cars, deficient_cars = [], []
        yd = {}
        for y in years:
            r = season_car(rs2, rm2, y)
            if r is None:
                continue
            yd[y] = r
            cars.append(r["car"])
            lpas.append(LPA[y])
            used_years.append(y)
            (normal_cars if LPA[y] >= NORMAL_CUT else deficient_cars).append(r["car"])
        per_name_year[t] = yd

        if len(cars) < 5:
            continue
        cars_a = np.array(cars)
        lpas_a = np.array(lpas, dtype=float)
        # CONNECTEDNESS: season CAR ~ LPA% (centred). slope = monsoon-beta.
        _, mon_beta, mon_t, mon_r2, n_yr = ols(cars_a, lpas_a - lpas_a.mean())
        caar = float(cars_a.mean())
        caar_t = tstat(cars_a)
        n_caar = float(np.mean(normal_cars)) if normal_cars else np.nan
        d_caar = float(np.mean(deficient_cars)) if deficient_cars else np.nan
        spread = n_caar - d_caar if (normal_cars and deficient_cars) else np.nan
        # GENUINELY connected: positive monsoon-beta with |t|>=1.5 AND normal>deficient
        connected = bool(np.isfinite(mon_beta) and mon_beta > 0 and abs(mon_t) >= 1.5
                         and np.isfinite(spread) and spread > 0)

        rows.append({
            "ticker": t, "label": UNIVERSE[t], "n_years": n_yr,
            "caar_pct": round(caar * 100, 2), "caar_t": round(caar_t, 2),
            "normal_caar_pct": round(n_caar * 100, 2),
            "deficient_caar_pct": round(d_caar * 100, 2),
            "normal_minus_deficient_pct": round(spread * 100, 2) if np.isfinite(spread) else None,
            "monsoon_beta": round(mon_beta, 4), "monsoon_beta_t": round(mon_t, 2),
            "monsoon_r2": round(mon_r2, 3),
            "market_beta": round(mbeta, 3), "market_beta_t": round(mt, 1),
            "connected": connected,
        })

    rows.sort(key=lambda r: (r["connected"], r["normal_minus_deficient_pct"] or -99,
                             r["monsoon_beta_t"]), reverse=True)

    hdr = (f"{'TICKER':14}{'CAAR%':>8}{'t':>6}{'NORM%':>8}{'DEF%':>8}"
           f"{'N-D%':>8}{'monB':>8}{'monT':>7}{'mktB':>7}{'CONN':>6}")
    print(hdr); print("-" * len(hdr))
    for r in rows:
        print(f"{r['ticker']:14}{r['caar_pct']:>8}{r['caar_t']:>6}"
              f"{r['normal_caar_pct']:>8}{r['deficient_caar_pct']:>8}"
              f"{str(r['normal_minus_deficient_pct']):>8}{r['monsoon_beta']:>8}"
              f"{r['monsoon_beta_t']:>7}{r['market_beta']:>7}"
              f"{'YES' if r['connected'] else 'no':>6}")

    # Regime-level NIFTY sanity: average NIFTY season return by regime
    nif_norm, nif_def = [], []
    for y in years:
        evt = (rm.index >= f"{y}-06-01") & (rm.index <= f"{y}-09-30")
        if evt.sum() < 40:
            continue
        v = float(np.sum(rm[evt].values))
        (nif_norm if LPA[y] >= NORMAL_CUT else nif_def).append(v)
    print(f"\nNIFTY season return  normal-yrs mean={np.mean(nif_norm)*100:.2f}%  "
          f"deficient-yrs mean={np.mean(nif_def)*100:.2f}%  "
          f"(n_norm={len(nif_norm)}, n_def={len(nif_def)})")

    connected = [r for r in rows if r["connected"]]
    print(f"\nGENUINELY monsoon-connected ({len(connected)}): "
          + ", ".join(r["ticker"] for r in connected))
    print("SPURIOUS / market-beta only: "
          + ", ".join(r["ticker"] for r in rows if not r["connected"]))

    OUT.write_text(json.dumps({"rows": rows, "lpa": LPA,
                               "nifty_normal_mean_pct": round(np.mean(nif_norm) * 100, 2),
                               "nifty_deficient_mean_pct": round(np.mean(nif_def) * 100, 2)},
                              indent=2))
    print(f"\nWrote {OUT}")


if __name__ == "__main__":
    main()
