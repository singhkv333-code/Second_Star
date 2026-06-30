"""v3 connectedness — the clean multi-factor regressions + the genuine/spurious
verdict + the naive-vs-clean "flipped?" side-by-side table (§2).

Every daily regression: full overlapping sample, Newey-West HAC t-stats (via
``factors.ols_hac``), after the |r|>0.5 mask that ``returns_matrix`` already
applied. The single deliverable that answers the v2 rejection is the ``flipped?``
column — where the NIFTY-only (naive) test and the clean factor test disagree on
``genuine``.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .factors import ols_hac


def verdict_genuine(beta: float, t: float, sign_hyp: int,
                    t_crit: float = 2.0) -> dict:
    """Genuine iff sign-correct AND |t| ≥ t_crit. ``sign_hyp`` ∈ {-1,0,+1}
    (0 = no sign restriction). Mirrors crude_geo_event_study.connectedness'
    ``connected`` flag but on the CLEAN factor."""
    sign_ok = (sign_hyp == 0) or (np.sign(beta) == np.sign(sign_hyp))
    genuine = bool(sign_ok and abs(t) >= t_crit)
    marginal = bool(sign_ok and 1.7 <= abs(t) < t_crit)
    return {"beta": round(float(beta), 4), "t": round(float(t), 2),
            "sign_hyp": int(sign_hyp), "sign_ok": bool(sign_ok),
            "genuine": genuine, "marginal": marginal}


def _aligned(*series: pd.Series, keys: list[str]) -> pd.DataFrame:
    return pd.concat(series, axis=1, keys=keys).dropna()


def _reg(df: pd.DataFrame, ycol: str, xcols: list[str]) -> dict:
    """OLS-HAC of ``ycol`` on intercept + ``xcols``. Returns betas/ts by name."""
    y = df[ycol].values
    X = np.column_stack([np.ones(len(df))] + [df[c].values for c in xcols])
    beta, se, t, r2, n = ols_hac(y, X)
    out = {"n": int(n), "r2": round(float(r2), 3)}
    for i, c in enumerate(xcols, start=1):
        out[f"b_{c}"] = round(float(beta[i]), 4)
        out[f"t_{c}"] = round(float(t[i]), 2)
    return out


def it_connectedness(rets, mkt_exit_f, it_f, r_nifty, leaders, industry,
                     *, min_n=200, t_crit=2.0) -> dict:
    """Per leader: 3 regressions side by side.
       naive : r_i = a + b_nifty·r_NIFTY
       clean1: r_i = a + b_mktexit·MKT_exIT
       full  : r_i = a + b_mkt·MKT_exIT + b_it·IT_f   <-- THE TEST (b_it)
    GENUINE IT-trouble beneficiary requires b_it < 0 significant. We default the
    sign hypothesis to -1 (beneficiary of IT weakness); a co-mover (+1) is also
    flagged. Returns {symbol: {...}}."""
    out: dict[str, dict] = {}
    for s in leaders:
        if s not in rets.columns:
            continue
        df = _aligned(rets[s], r_nifty, mkt_exit_f, it_f,
                      keys=["y", "nifty", "mktexit", "itf"])
        if len(df) < min_n:
            continue
        naive = _reg(df, "y", ["nifty"])
        clean1 = _reg(df, "y", ["mktexit"])
        full = _reg(df, "y", ["mktexit", "itf"])
        b_it, t_it = full["b_itf"], full["t_itf"]
        sign_hyp = -1 if b_it < 0 else +1
        v_clean = verdict_genuine(b_it, t_it, sign_hyp, t_crit)
        # naive verdict: is the NIFTY beta itself "significant" (the v2 lens)?
        v_naive = verdict_genuine(naive["b_nifty"], naive["t_nifty"],
                                  0, t_crit)
        out[s] = {
            "industry": industry.get(s, ""),
            "naive": {"beta_nifty": naive["b_nifty"], "t": naive["t_nifty"],
                      "verdict": v_naive},
            "clean": {"beta_clean": b_it, "t": t_it, "factor": "IT_f",
                      "beta_mktexit": full["b_mktexit"],
                      "t_mktexit": full["t_mktexit"],
                      "beta_mktexit_solo": clean1["b_mktexit"],
                      "verdict": v_clean},
            "naive_clean_delta": round(naive["b_nifty"] - b_it, 4),
            "r2_full": full["r2"], "n": full["n"],
            "flipped": bool(v_naive["genuine"] != v_clean["genuine"]),
        }
    return out


def crude_connectedness(rets, r_brent, mkt_perp, r_nifty, sign_hyps, leaders,
                        *, min_n=200, t_crit=2.0) -> dict:
    """Per leader: naive (collinear NIFTY+Brent) vs clean (Brent + MKT_perpBrent).
       naive: r_i = a + b_brent_naive·Brent + b_mkt·NIFTY
       clean: r_i = a + b_brent·Brent + b_mktperp·MKT_perpBrent   <-- THE TEST
    ``sign_hyps`` maps symbol -> expected Brent-beta sign (+1 upstream, -1 OMC/
    paints/aviation/tyres, 0 integrated)."""
    out: dict[str, dict] = {}
    for s in leaders:
        if s not in rets.columns:
            continue
        df = _aligned(rets[s], r_brent, r_nifty, mkt_perp,
                      keys=["y", "brent", "nifty", "perp"])
        if len(df) < min_n:
            continue
        naive = _reg(df, "y", ["brent", "nifty"])
        clean = _reg(df, "y", ["brent", "perp"])
        sign_hyp = int(sign_hyps.get(s, 0))
        v_clean = verdict_genuine(clean["b_brent"], clean["t_brent"],
                                  sign_hyp, t_crit)
        v_naive = verdict_genuine(naive["b_brent"], naive["t_brent"],
                                  sign_hyp, t_crit)
        out[s] = {
            "naive": {"beta_brent": naive["b_brent"], "t": naive["t_brent"],
                      "beta_mkt": naive["b_nifty"], "verdict": v_naive},
            "clean": {"beta_clean": clean["b_brent"], "t": clean["t_brent"],
                      "factor": "b_brent", "beta_mktperp": clean["b_perp"],
                      "t_mktperp": clean["t_perp"], "verdict": v_clean},
            "naive_clean_delta": round(naive["b_brent"] - clean["b_brent"], 4),
            "r2_clean": clean["r2"], "n": clean["n"],
            "flipped": bool(v_naive["genuine"] != v_clean["genuine"]),
        }
    return out


def monsoon_connectedness(rets, rural_f, mkt_exmonsoon, r_nifty, leaders,
                          sign_hyps=None, *, min_n=200, t_crit=2.0) -> dict:
    """Per leader: naive (RURAL_f + NIFTY) vs clean (RURAL_f + MKT_exMonsoon).
       clean: r_i = a + b_rain·RURAL_f + b_mktperp·MKT_exMonsoon  <-- THE TEST
    Default sign hypothesis +1 (rural names move WITH the rural factor)."""
    sign_hyps = sign_hyps or {}
    out: dict[str, dict] = {}
    for s in leaders:
        if s not in rets.columns:
            continue
        df = _aligned(rets[s], rural_f, r_nifty, mkt_exmonsoon,
                      keys=["y", "rural", "nifty", "exmon"])
        if len(df) < min_n:
            continue
        naive = _reg(df, "y", ["rural", "nifty"])
        clean = _reg(df, "y", ["rural", "exmon"])
        sign_hyp = int(sign_hyps.get(s, 1))
        v_clean = verdict_genuine(clean["b_rural"], clean["t_rural"],
                                  sign_hyp, t_crit)
        v_naive = verdict_genuine(naive["b_rural"], naive["t_rural"],
                                  sign_hyp, t_crit)
        out[s] = {
            "naive": {"beta_rain": naive["b_rural"], "t": naive["t_rural"],
                      "verdict": v_naive},
            "clean": {"beta_clean": clean["b_rural"], "t": clean["t_rural"],
                      "factor": "b_rain", "beta_mktex": clean["b_exmon"],
                      "t_mktex": clean["t_exmon"], "verdict": v_clean},
            "naive_clean_delta": round(naive["b_rural"] - clean["b_rural"], 4),
            "r2_clean": clean["r2"], "n": clean["n"],
            "flipped": bool(v_naive["genuine"] != v_clean["genuine"]),
        }
    return out


def side_by_side_table(conn_result: dict, win_ret: dict | None = None
                       ) -> list[dict]:
    """Flatten a connectedness dict into the §2.5 rows for the report.
    ``win_ret`` optionally maps symbol -> event-window return %."""
    win_ret = win_ret or {}
    rows: list[dict] = []
    for s, d in conn_result.items():
        rows.append({
            "symbol": s,
            "industry": d.get("industry", ""),
            "event_win_ret_pct": win_ret.get(s),
            "b_raw": d["naive"].get("beta_nifty", d["naive"].get("beta_brent",
                     d["naive"].get("beta_rain"))),
            "b_clean": d["clean"]["beta_clean"],
            "t_clean": d["clean"]["t"],
            "naive_clean_delta": d["naive_clean_delta"],
            "verdict_naive": d["naive"]["verdict"]["genuine"],
            "verdict_clean": d["clean"]["verdict"]["genuine"],
            "flipped": d["flipped"],
        })
    return rows
