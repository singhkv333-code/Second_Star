"""IT-in-trouble — EMPIRICAL TOP-GAINERS scan + connectedness (REAL yfinance).

The core ask: at the time each weak Indian-IT result/guidance event happened,
WHICH stocks and WHICH non-stock securities were the TOP GAINERS over the event
window — and were they GENUINELY connected to the IT-trouble event, or just
market beta / coincidence (spurious)?

What this does (all real numbers, no LLM, degrades honestly on yfinance misses):
  1. Universe = the ~80 NSE names in backend.services.sector_universe
     (symbol_sector_map) + non-stock securities (sector indices, commodities,
     FX, ETFs).  Driver = USDINR (FX nuance: a FALLING rupee is a *symptom* of
     global stress here, not a clean tailwind) and Nifty-IT (^CNXIT).
  2. For each weak-IT event t0, rank EVERY name by total return over the event
     window [-2, +20] trading days. Print top-15 stocks + the non-stock ranking.
  3. AGGREGATE across events: mean rank, hit-frequency (top-15 / top-decile),
     mean window return — the names that reliably gained when IT was in trouble.
  4. CONNECTEDNESS test on the aggregate top gainers:
       (a) Full-sample factor OLS  r_i = a + b_mkt*NIFTY + b_fx*dUSDINR
           + b_it*IT_resid + e   (IT_resid = Nifty-IT orthogonalised to NIFTY).
           A genuine IT-trouble winner loads on IT_resid (negative = rises when
           the IT-specific factor falls) and/or on dUSDINR (export FX), NOT just
           on b_mkt.
       (b) Event-window ABNORMAL return: pre-event market-model beta (estimated
           on [t0-130, t0-11], strictly OOS) -> CAAR over the events. If a
           name's window gain is mostly ABNORMAL (beyond market beta) it really
           moved BECAUSE of the event; if the gain ~= beta*NIFTY it's SPURIOUS.
     Verdict = GENUINE / WEAK / SPURIOUS from both layers.

Run:  pivot/.venv/bin/python pivot/scripts/strategy_research/it_trouble_topgainers_scan.py
"""
from __future__ import annotations

import json
import os
import sys
import time
import warnings

import numpy as np
import pandas as pd

warnings.simplefilter("ignore")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from backend.services.sector_universe import symbol_sector_map  # noqa: E402

try:
    import yfinance as yf
except Exception as e:  # pragma: no cover
    print(f"yfinance import failed: {e}", file=sys.stderr); sys.exit(1)

OUT_DIR = os.path.join(os.path.dirname(__file__), "_out")
os.makedirs(OUT_DIR, exist_ok=True)

# ── Drivers / benchmarks ──────────────────────────────────────────────────────
BENCH = "^NSEI"          # NIFTY 50 (market factor)
IT_IDX = "^CNXIT"        # Nifty IT (the event sector)
USDINR = "INR=X"         # USD/INR (up = rupee weaker)

# ── Non-stock securities to rank (sector indices + commodities + FX + ETFs) ───
SECTOR_IDX = {
    "^CNXIT": "Nifty IT", "^CNXFMCG": "Nifty FMCG", "^CNXPHARMA": "Nifty Pharma",
    "^CNXAUTO": "Nifty Auto", "^CNXENERGY": "Nifty Energy", "^CNXMETAL": "Nifty Metal",
    "^NSEBANK": "Bank Nifty", "^CNXPSE": "Nifty PSE", "^CNXINFRA": "Nifty Infra",
    "^CNXREALTY": "Nifty Realty", "^CNXFIN": "Nifty Fin Svc", "^NSEI": "NIFTY 50",
}
COMMOD_FX = {"BZ=F": "Brent", "CL=F": "WTI", "GC=F": "Gold", "INR=X": "USDINR"}
ETFS = {"GOLDBEES.NS": "GoldBeES", "NIFTYBEES.NS": "NiftyBeES", "ITBEES.NS": "ITBeES"}
NONSTOCK = {**SECTOR_IDX, **COMMOD_FX, **ETFS}

# ── Stock universe = sector_universe (NSE), suffixed .NS ───────────────────────
SECMAP = symbol_sector_map()                      # {SYMBOL: sector}
STOCKS = [f"{s}.NS" for s in SECMAP]              # 80 names
STOCK_SECTOR = {f"{s}.NS": sec for s, sec in SECMAP.items()}

# ── Events: 8 curated weak/guidance-cut TCS-anchored analogs + 2 dated extras ─
# Core 8 (reused verbatim from the prior it_trouble scripts):
CORE_ANALOGS = ["2022-04-11", "2022-07-08", "2023-01-09", "2023-04-12",
                "2023-07-12", "2023-10-11", "2024-04-12", "2025-01-09"]
# Extended weak prints I can date with confidence (labelled, run separately):
#  2019-07-09 TCS Q1FY20 — sharp margin miss, stock fell ~6% on the day (weak);
#  2025-04-10 TCS Q4FY25 — soft/cautious FY26 commentary amid US tariff demand fear.
EXTRA_ANALOGS = ["2019-07-09", "2025-04-10"]
EXT_ANALOGS = CORE_ANALOGS + EXTRA_ANALOGS

WIN_LO, WIN_HI = -2, 20          # event window (trading days) for the gainer scan
EST_LEN, EST_GAP = 120, 10       # pre-event market-model estimation window


# ── Data ──────────────────────────────────────────────────────────────────────
def _dl(batch, start, end, tries=4):
    """Download one batch with retries (yfinance bulk download is flaky)."""
    for k in range(tries):
        try:
            raw = yf.download(batch, start=start, end=end, auto_adjust=True,
                              progress=False, threads=False)
            close = raw["Close"] if isinstance(raw.columns, pd.MultiIndex) else raw
            if isinstance(close, pd.Series):
                close = close.to_frame(batch[0])
            ok = [c for c in batch if c in close.columns and close[c].notna().sum() > 200]
            if ok:
                return close[ok]
        except Exception as e:
            print(f"  [retry {k+1}/{tries}] batch {batch[:2]}…: {type(e).__name__}")
        time.sleep(2.5)
    return pd.DataFrame()


def fetch(tickers, start="2018-06-01", end="2025-12-31", chunk=12) -> pd.DataFrame:
    uniq = list(dict.fromkeys(tickers))
    frames = []
    for i in range(0, len(uniq), chunk):
        batch = uniq[i:i + chunk]
        df = _dl(batch, start, end)
        if not df.empty:
            frames.append(df)
    if not frames:
        print("  [FATAL] no data downloaded at all"); sys.exit(1)
    close = pd.concat(frames, axis=1)
    close = close.loc[:, ~close.columns.duplicated()]
    have = [c for c in uniq if c in close.columns and close[c].notna().sum() > 200]
    missing = [c for c in uniq if c not in have]
    if missing:
        print(f"  [degraded] dropped (insufficient yfinance history): {missing}\n")
    return close[have]


def logret(s):
    return np.log(s / s.shift(1))


def ols(y, X):
    """(beta, tstat, r2) — X includes an intercept column."""
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    resid = y - X @ beta
    n, k = X.shape
    dof = n - k
    if dof <= 0:
        return beta, np.full(k, np.nan), np.nan
    sigma2 = (resid @ resid) / dof
    se = np.sqrt(np.diag(sigma2 * np.linalg.pinv(X.T @ X)))
    t = beta / se
    ss = ((y - y.mean()) ** 2).sum()
    r2 = 1 - (resid @ resid) / ss if ss > 0 else np.nan
    return beta, t, r2


# ── (2) Top-gainers scan ──────────────────────────────────────────────────────
def window_returns(close: pd.DataFrame, names, anchors):
    """For each anchor, total return of each name over [WIN_LO, WIN_HI].
    Returns {anchor: Series(name -> ret)} using only names with full coverage."""
    idx = close.index
    per_event = {}
    for a in anchors:
        pos = idx.searchsorted(pd.Timestamp(a))
        lo, hi = pos + WIN_LO, pos + WIN_HI
        if lo < 0 or hi >= len(idx):
            print(f"  [skip] {a}: window out of data range"); continue
        rets = {}
        for n in names:
            if n not in close.columns:
                continue
            p0, p1 = close[n].iloc[lo], close[n].iloc[hi]
            if np.isfinite(p0) and np.isfinite(p1) and p0 > 0:
                rets[n] = p1 / p0 - 1.0
        per_event[a] = pd.Series(rets).sort_values(ascending=False)
    return per_event


def aggregate_gainers(per_event, topn=15):
    """Mean rank / hit-frequency / mean window return across events."""
    all_names = set()
    for s in per_event.values():
        all_names |= set(s.index)
    rows = []
    nev = len(per_event)
    for n in all_names:
        ranks, rets, hits, decile_hits = [], [], 0, 0
        for s in per_event.values():
            if n in s.index:
                r = int(s.index.get_loc(n)) + 1       # 1 = best
                ranks.append(r); rets.append(float(s[n]))
                if r <= topn:
                    hits += 1
                if r <= max(1, len(s) // 10):
                    decile_hits += 1
        if not ranks:
            continue
        rows.append({
            "name": n, "events": len(ranks), "mean_rank": np.mean(ranks),
            "mean_ret%": 100 * np.mean(rets), "median_ret%": 100 * np.median(rets),
            "top_freq": hits / len(ranks),
            "decile_freq": decile_hits / len(ranks),
        })
    return pd.DataFrame(rows).sort_values(["mean_rank"]).reset_index(drop=True)


# ── (3) Connectedness ─────────────────────────────────────────────────────────
def factor_betas(rets: pd.DataFrame, names):
    """Full-sample OLS r_i ~ NIFTY + dUSDINR + IT_resid (IT_resid = Nifty-IT ⟂ NIFTY).

    Factors are built once on the factor-only common window; each name is then
    regressed PAIRWISE on its own full overlapping history (no global dropna that
    would crush the sample to the shortest-listed name). The 3 driver tickers are
    excluded from `names` so we never regress a factor on itself."""
    factor_cols = {BENCH, IT_IDX, USDINR}
    fac = rets[[c for c in [BENCH, IT_IDX, USDINR] if c in rets.columns]].dropna()
    nifty = fac[BENCH].values
    Xb = np.column_stack([np.ones(len(nifty)), nifty])
    bb, *_ = np.linalg.lstsq(Xb, fac[IT_IDX].values, rcond=None)
    fac = fac.assign(_it_resid=fac[IT_IDX].values - Xb @ bb)
    rows = []
    for n in names:
        if n not in rets.columns or n in factor_cols:
            continue
        d = pd.concat([rets[n].rename("_y"), fac[[BENCH, USDINR, "_it_resid"]]],
                      axis=1).dropna()
        if len(d) < 120:
            continue
        y = d["_y"].values
        X = np.column_stack([np.ones(len(d)), d[BENCH].values,
                             d[USDINR].values, d["_it_resid"].values])
        beta, t, r2 = ols(y, X)
        rows.append({"name": n, "b_mkt": beta[1], "t_mkt": t[1],
                     "b_fx": beta[2], "t_fx": t[2],
                     "b_it": beta[3], "t_it": t[3], "r2": r2, "n_obs": len(d)})
    return pd.DataFrame(rows), len(fac)


def event_abnormal(close: pd.DataFrame, rets: pd.DataFrame, names, anchors):
    """Pre-event market-model beta (OOS) -> per-event window abnormal return.
    Reports raw window CAAR (total), abnormal CAAR (beyond market beta), t-stat,
    and beta-implied share. Distinguishes BECAUSE-of-event from market-beta drift."""
    idx = rets.index
    nifty = rets[BENCH].values
    out = {}
    for n in names:
        if n not in rets.columns:
            continue
        y = rets[n].values
        raw_w, abn_w = [], []
        for a in anchors:
            pos = idx.searchsorted(pd.Timestamp(a))
            elo, ehi = pos - EST_GAP - EST_LEN, pos - EST_GAP
            wlo, whi = pos + WIN_LO, pos + WIN_HI
            if elo < 0 or whi >= len(idx):
                continue
            ny, sy = nifty[elo:ehi], y[elo:ehi]
            m = np.isfinite(ny) & np.isfinite(sy)
            if m.sum() < 60 or np.std(ny[m]) == 0:
                continue
            X = np.column_stack([np.ones(m.sum()), ny[m]])
            b0, b1 = np.linalg.lstsq(X, sy[m], rcond=None)[0]
            seg = slice(wlo, whi + 1)
            ys, ns = y[seg], nifty[seg]
            mm = np.isfinite(ys) & np.isfinite(ns)
            raw = float(np.nansum(ys[mm]))                 # log-return sum ≈ window ret
            abn = float(np.nansum(ys[mm] - (b0 + b1 * ns[mm])))
            raw_w.append(raw); abn_w.append(abn)
        if len(abn_w) < 2:
            continue
        abn_arr = np.array(abn_w)
        t = abn_arr.mean() / (abn_arr.std(ddof=1) / np.sqrt(len(abn_arr))) \
            if abn_arr.std(ddof=1) > 0 else np.nan
        out[n] = {"raw_caar%": 100 * np.mean(raw_w), "abn_caar%": 100 * np.mean(abn_w),
                  "abn_t": float(t), "N": len(abn_w)}
    return out


def verdict(fb_row, ev):
    """GENUINE / WEAK / SPURIOUS from factor loadings + event-window abnormality."""
    it_sig = fb_row is not None and abs(fb_row["b_it"]) > 0.15 and abs(fb_row["t_it"]) > 3
    fx_sig = fb_row is not None and abs(fb_row["b_fx"]) > 0.10 and abs(fb_row["t_fx"]) > 2.5
    abn_sig = ev is not None and np.isfinite(ev["abn_t"]) and ev["abn_t"] > 1.3 and ev["abn_caar%"] > 0
    # raw gain that is mostly market beta (abnormal small vs raw) => spurious
    beta_driven = ev is not None and ev["raw_caar%"] > 0 and ev["abn_caar%"] < 0.4 * ev["raw_caar%"]
    if abn_sig and (it_sig or fx_sig):
        return "GENUINE"
    if abn_sig:
        return "GENUINE (event-abnormal; factor link weak)"
    if (it_sig or fx_sig) and not beta_driven:
        return "STRUCTURAL (factor-linked, event signal weak)"
    return "SPURIOUS (market beta / coincidence)"


# ── Main ──────────────────────────────────────────────────────────────────────
def run(anchors, label, close, rets, fb, nobs):
    print("=" * 90)
    print(f"TOP-GAINERS SCAN — {label}  (events N={len(anchors)}, window [{WIN_LO},+{WIN_HI}] td)")
    print("=" * 90)
    # Stocks
    pe_stk = window_returns(close, STOCKS, anchors)
    print("\n--- PER-EVENT TOP-15 STOCKS (total window return %) ---")
    for a, s in pe_stk.items():
        top = s.head(15)
        line = ", ".join(f"{n.replace('.NS','')}({STOCK_SECTOR.get(n,'?')[:4]}) {100*v:+.1f}"
                         for n, v in top.items())
        print(f"  {a}: {line}")
    agg_stk = aggregate_gainers(pe_stk, topn=15)
    print(f"\n--- AGGREGATE TOP-20 STOCKS across {len(pe_stk)} events (by mean rank) ---")
    print(f"  {'name':13s} {'sec':6s} {'ev':>3s} {'mRank':>6s} {'meanRet%':>9s} "
          f"{'medRet%':>8s} {'top15f':>7s} {'decf':>6s}")
    for _, r in agg_stk.head(20).iterrows():
        print(f"  {r['name'].replace('.NS',''):13s} {STOCK_SECTOR.get(r['name'],'?')[:6]:6s} "
              f"{int(r['events']):3d} {r['mean_rank']:6.1f} {r['mean_ret%']:9.2f} "
              f"{r['median_ret%']:8.2f} {r['top_freq']:7.2f} {r['decile_freq']:6.2f}")

    # Non-stock securities
    pe_ns = window_returns(close, list(NONSTOCK), anchors)
    agg_ns = aggregate_gainers(pe_ns, topn=6)
    print(f"\n--- AGGREGATE NON-STOCK SECURITIES across {len(pe_ns)} events (by mean rank) ---")
    print(f"  {'security':14s} {'ev':>3s} {'mRank':>6s} {'meanRet%':>9s} {'medRet%':>8s} {'top6f':>6s}")
    for _, r in agg_ns.iterrows():
        print(f"  {NONSTOCK.get(r['name'], r['name']):14s} {int(r['events']):3d} "
              f"{r['mean_rank']:6.1f} {r['mean_ret%']:9.2f} {r['median_ret%']:8.2f} "
              f"{r['top_freq']:6.2f}")

    # Connectedness on the aggregate top gainers (stocks top-12 + top non-stock)
    top_stocks = list(agg_stk.head(12)["name"])
    top_ns = [r for r in agg_ns.head(6)["name"] if r not in (BENCH,)]
    targets = top_stocks + top_ns
    ev = event_abnormal(close, rets, targets, anchors)
    fb_map = {r["name"]: r for _, r in fb.iterrows()}
    print(f"\n--- CONNECTEDNESS on aggregate top gainers (full-sample n={nobs} obs) ---")
    print("  factor OLS r_i~NIFTY+dUSDINR+IT_resid ; event window abnormal vs pre-event beta")
    print(f"  {'name':13s} {'b_mkt':>6s} {'b_fx':>6s}{'(t)':>5s} {'b_it':>6s}{'(t)':>5s} "
          f"{'R2':>5s} | {'rawCAAR':>8s} {'abnCAAR':>8s} {'abnT':>5s}  VERDICT")
    conn_rows = []
    for n in targets:
        fbr = fb_map.get(n); evr = ev.get(n)
        v = verdict(fbr, evr)
        nm = NONSTOCK.get(n, n.replace(".NS", ""))
        bm = fbr["b_mkt"] if fbr is not None else np.nan
        bf = fbr["b_fx"] if fbr is not None else np.nan
        tf = fbr["t_fx"] if fbr is not None else np.nan
        bi = fbr["b_it"] if fbr is not None else np.nan
        ti = fbr["t_it"] if fbr is not None else np.nan
        r2 = fbr["r2"] if fbr is not None else np.nan
        rc = evr["raw_caar%"] if evr else np.nan
        ac = evr["abn_caar%"] if evr else np.nan
        at = evr["abn_t"] if evr else np.nan
        print(f"  {nm:13s} {bm:6.2f} {bf:6.2f}{tf:5.1f} {bi:6.2f}{ti:5.1f} {r2:5.2f} | "
              f"{rc:8.2f} {ac:8.2f} {at:5.1f}  {v}")
        conn_rows.append({"name": nm, "b_mkt": _f(bm), "b_fx": _f(bf), "t_fx": _f(tf),
                          "b_it": _f(bi), "t_it": _f(ti), "r2": _f(r2),
                          "raw_caar%": _f(rc), "abn_caar%": _f(ac), "abn_t": _f(at),
                          "verdict": v})
    print()
    return {"label": label, "anchors": anchors,
            "agg_stocks": _df_json(agg_stk.head(20)),
            "agg_nonstock": [{**r, "label": NONSTOCK.get(r["name"], r["name"])}
                             for r in _df_json(agg_ns)],
            "connectedness": conn_rows}


def _f(x):
    return None if x is None or (isinstance(x, float) and not np.isfinite(x)) else round(float(x), 4)


def _df_json(df):
    return [{k: _f(v) if isinstance(v, (int, float, np.floating)) else v
             for k, v in row.items()} for row in df.to_dict("records")]


def main():
    print("Downloading universe (80 stocks + indices/commodities/FX/ETFs) from yfinance...\n")
    all_tk = STOCKS + list(NONSTOCK) + [BENCH, IT_IDX, USDINR]
    close = fetch(all_tk)
    print(f"Price matrix: {close.shape[0]} days × {close.shape[1]} symbols "
          f"({close.index[0].date()} → {close.index[-1].date()})\n")
    rets = close.apply(logret)
    fb, nobs = factor_betas(rets, STOCKS + [n for n in NONSTOCK if n != BENCH])

    results = {}
    results["core8"] = run(CORE_ANALOGS, "CORE 8 weak/guidance-cut analogs",
                           close, rets, fb, nobs)
    results["ext10"] = run(EXT_ANALOGS, "EXTENDED 10 (core 8 + 2019-07-09, 2025-04-10)",
                           close, rets, fb, nobs)

    out_path = os.path.join(OUT_DIR, "it_trouble_topgainers.json")
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\nSaved machine-readable results -> {out_path}")


if __name__ == "__main__":
    main()
