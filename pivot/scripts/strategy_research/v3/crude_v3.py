"""v3 Crude / geopolitical-shock view runner — full NIFTY-500 universe.

Pipeline (run from repo root):
    .venv/bin/python -m scripts.strategy_research.v3.crude_v3

  1. FWL orthogonalization:  r_NIFTY = c + b_NB·r_Brent + u
        -> REPORT b_NB and t(b_NB) [Newey-West HAC]; u = MKT_⊥Brent (residual
        market with the crude channel removed). Also from the EW NIFTY-500 market
        as a robustness leg.
  2. Detect Brent CRASH / de-escalation episodes (crude-DOWN) directly from Brent
        10-trading-day moves (v2 detect_events, MIN_GAP=25, one-bar lag).
  3. FULL-UNIVERSE top-gainers scan over the concatenated crash windows (all ~500
        names) -> the candidate crude-DOWN beneficiaries.
  4. CLEAN connectedness per leader, side by side:
        naive: r_i = a + b_brent_naive·Brent + b_mkt·NIFTY          (collinear, v2)
        clean: r_i = a + b_brent·Brent + b_mktperp·MKT_⊥Brent       (ISOLATED)  <- THE TEST
        + the naive-vs-clean ``flipped?`` verdict column. Confirms paints genuine
        / tyres spurious under the clean model.
  5. Select GENUINELY-connected crude-DOWN beneficiaries (clean b_brent < 0,
        |t| >= 2.0) and build three risk-tiered expressions:
        Conservative = EW beneficiary basket
        Balanced     = beta-neutral pair (long basket / short upstream)
        Aggressive   = 1.5x leveraged directional basket (labelled proxy — no
                       historical option chain, so no option payoff is fabricated)
  6. Backtest each expression under ALL THREE exit variants (fixed / target /
        manual), grade with the full v2 Trust Battery, and attach a NIFTY-
        comparison block to EVERY result.

Real yfinance data only. Thin samples (≈7 crash episodes) -> dials SUPPRESS; that
suppression is reported verbatim, never overridden.
"""
from __future__ import annotations

import json
import warnings
from datetime import datetime, timezone

warnings.filterwarnings("ignore")

import numpy as np
import pandas as pd

from scripts.strategy_research.v3 import universe as U
from scripts.strategy_research.v3 import factors as F
from scripts.strategy_research.v3 import connectedness as C
from scripts.strategy_research.v3 import exits as E
from scripts.strategy_research.v3 import battery as B
from scripts.strategy_research.crude_geo_event_study import detect_events

HOLD = 20            # trading-day hold per crash episode (entry+1 .. entry+HOLD)
OIL_GAS_INDUSTRY = "Oil Gas & Consumable Fuels"

# A-priori expected sign of the Brent-beta (TESTED, not assumed): +1 helped by
# crude UP (upstream), -1 helped by crude DOWN (consumers: OMC/paints/aviation/
# tyres), 0 integrated. Names declared with the .NS suffix.
CRUDE_SIGN: dict[str, int] = {
    # upstream producers — realise higher crude (crude-UP names)
    "ONGC.NS": +1, "OIL.NS": +1,
    # OMC / refiners — import crude, marketing margin squeezed when crude up
    "BPCL.NS": -1, "HINDPETRO.NS": -1, "IOC.NS": -1,
    # paints — crude-derivative inputs (monomers / solvents / TiO2)
    "ASIANPAINT.NS": -1, "BERGEPAINT.NS": -1, "KANSAINER.NS": -1, "AKZOINDIA.NS": -1,
    # aviation — ATF ~40% of cost
    "INDIGO.NS": -1, "SPICEJET.NS": -1,
    # tyres — carbon black / synthetic rubber inputs
    "MRF.NS": -1, "APOLLOTYRE.NS": -1, "CEATLTD.NS": -1, "BALKRISIND.NS": -1,
    "JKTYRE.NS": -1, "TVSSRICHAK.NS": -1,
    # integrated
    "RELIANCE.NS": 0,
}
# Names force-included as connectedness leaders so the paints/tyres confirmation
# is always reported even if they didn't top the gainers scan.
FORCE_LEADERS = list(CRUDE_SIGN)
SHORT_LEG = "ONGC.NS"     # upstream short for the balanced pair (crude-UP leg)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── crash episodes from Brent ────────────────────────────────────────────────
def crash_episodes(rets: pd.DataFrame) -> tuple[list[tuple[int, int]], list[dict]]:
    brent_px = U.driver_close("BRENT")
    ev = detect_events(brent_px)
    crashes = ev[ev["kind"] == "CRASH"].copy()
    idx = rets.index
    eps: list[tuple[int, int]] = []
    meta: list[dict] = []
    for _, r in crashes.iterrows():
        pos = int(idx.get_indexer([pd.Timestamp(r["date"])], method="nearest")[0])
        if pos + HOLD >= len(idx) or pos + 1 >= len(idx):
            continue
        eps.append((pos + 1, pos + HOLD))     # next-bar entry, HOLD-bar window
        meta.append({"date": str(pd.Timestamp(r["date"]).date()), "kind": "CRASH",
                     "brent_10d_ret_pct": round(float(r["brent_10d_ret"]) * 100, 1),
                     "context": r.get("context", "")})
    return eps, meta


# ── full-universe event-window return scan ───────────────────────────────────
def window_returns(rets: pd.DataFrame, episodes) -> pd.Series:
    """Mean cumulative return over the concatenated crash windows, per name."""
    per_ep = []
    for (e, x) in episodes:
        seg = rets.iloc[e:x + 1]
        valid = seg.notna().sum() >= int(0.5 * (x + 1 - e))
        cum = (1.0 + seg.fillna(0.0)).prod() - 1.0
        per_ep.append(cum.where(valid))
    M = pd.concat(per_ep, axis=1)
    return (M.mean(axis=1, skipna=True) * 100.0).sort_values(ascending=False)


def _basket_series(rets: pd.DataFrame, members: list[str]) -> pd.Series:
    cols = [m for m in members if m in rets.columns]
    return rets[cols].mean(axis=1, skipna=True)


# ── grade one (episodes, weights_or_leg) under all 3 exit variants ───────────
def grade_expression(name, kind_label, episodes, daily_rets, weights_or_leg,
                     r_nifty, *, target_pct, num_trials, note=None,
                     payoff_pop=None) -> dict:
    # gross (cost-free) fixed run to measure cost survival
    gross = E.backtest_exits(episodes, daily_rets, weights_or_leg, r_nifty,
                             modes=("fixed",), target_pct=None, hold_bars=HOLD,
                             cost_rt=0.0)["fixed"]
    res = E.backtest_exits(episodes, daily_rets, weights_or_leg, r_nifty,
                           modes=("fixed", "target", "manual"),
                           target_pct=target_pct, hold_bars=HOLD)
    variants: dict[str, dict] = {}
    for mode, r in res.items():
        bat = B.run_battery(r["equity"], r["daily_rets"], r["n_episodes"],
                            num_trials=num_trials)
        fs = bat["forward_stats"]
        sig = B.caar_significance(r["per_episode_rets"])
        net_total = r["nifty_comparison"]["strategy_total_pct"]
        cs = B.cost_survival(gross["nifty_comparison"]["strategy_total_pct"],
                             net_total)
        hit_rate = (float(np.mean([1.0 if x > 0 else 0.0
                    for x in r["per_episode_rets"]]))
                    if r["per_episode_rets"] else None)
        outcome, expr = B.two_dials(
            hit_rate=hit_rate, relationship_strength=None,
            sample_n=r["n_episodes"], verdict=bat["verdict"]["verdict"],
            caar_alignment=B._caar_alignment(sig["caar"]),
            significance_p=sig["combined_p"], cost_survival=cs,
            deflated_sharpe=fs["deflated_sharpe"], n_obs=fs["n_obs"],
            min_trl=fs["min_trl"], payoff_pop=payoff_pop)
        tb = B.trust_block(bat, engine=kind_label,
                           alignment=B.dial_to_dict(expr),
                           nifty_comparison=r["nifty_comparison"],
                           data_note=note)
        block = {
            "trust": tb,
            "nifty_comparison": r["nifty_comparison"],
            "per_episode_pct": r["per_episode_pct"],
            "caar_significance": sig,
            "battery_summary": {
                "verdict": bat["verdict"]["verdict"],
                "label": bat["verdict"]["label"],
                "total_return_pct": bat["total_return_pct"],
                "max_drawdown_pct": bat["max_drawdown_pct"],
                "psr": fs["psr"], "deflated_sharpe": fs["deflated_sharpe"],
                "min_trl": fs["min_trl"], "n_obs": fs["n_obs"],
                "observed_sharpe": fs["observed_sharpe"],
                "num_trials": fs["num_trials"],
                "cost_survival": round(cs, 3) if cs is not None else None,
                "hit_rate": round(hit_rate, 3) if hit_rate is not None else None,
            },
            "outcome_dial": B.dial_to_dict(outcome),
            "expression_dial": B.dial_to_dict(expr),
        }
        if mode == "target":
            block["hit_target"] = r.get("hit_target")
            block["bars_to_target"] = r.get("bars_to_target")
        if note:
            block["note"] = note
        variants[mode] = block
    return {"name": name, "engine": kind_label, "exit_variants": variants}


def main() -> None:
    print("=" * 78)
    print("v3 CRUDE VIEW — full NIFTY-500 universe, clean (orthogonalized) factors")
    print("=" * 78)

    rets = U.returns_matrix()
    ind = U.industry_map()
    r_nifty = U.series("NIFTY").reindex(rets.index)
    r_brent = U.series("BRENT").reindex(rets.index)
    n_constituents = int(rets.shape[1])   # before any synthetic columns
    print(f"returns matrix: {rets.shape[0]} rows x {rets.shape[1]} cols  "
          f"{rets.index.min().date()}..{rets.index.max().date()}")

    # ── 1. FWL orthogonalization: b_NB + MKT_⊥Brent ──────────────────────────
    mkt_perp, b_NB, t_NB = F.orthogonalize(r_nifty, r_brent)
    # robustness: b_NB from the EW NIFTY-500 market instead of ^NSEI
    mkt_all = F.ew_factor(rets, list(rets.columns))
    mkt_perp_ew, b_NB_ew, t_NB_ew = F.orthogonalize(mkt_all.reindex(rets.index),
                                                    r_brent)
    print(f"\n[b_NB] r_NIFTY = c + b_NB·r_Brent + u   (the NIFTY–Brent beta)")
    print(f"   ^NSEI    : b_NB = {b_NB:+.4f}   t(HAC) = {t_NB:.2f}   "
          f"resid n = {len(mkt_perp)}")
    print(f"   EW mkt   : b_NB = {b_NB_ew:+.4f}   t(HAC) = {t_NB_ew:.2f}  (robustness)")

    # ── 2. crash episodes ────────────────────────────────────────────────────
    episodes, ev_meta = crash_episodes(rets)
    print(f"\nDetected {len(episodes)} Brent CRASH / de-escalation episodes "
          f"(crude-DOWN, HOLD={HOLD}d, one-bar lag):")
    for m in ev_meta:
        print(f"   {m['date']}  Brent10d={m['brent_10d_ret_pct']:+6.1f}%  {m['context']}")

    # ── 3. full-universe top-gainers scan ────────────────────────────────────
    win = window_returns(rets, episodes)
    n_scanned = int(win.notna().sum())
    print(f"\nFULL-UNIVERSE top-gainers over the crash windows ({n_scanned} names "
          f"with data) — top 15:")
    for s, v in win.head(15).items():
        print(f"   {s:16s} {ind.get(s,''):28.28s} {v:+7.2f}%")

    # leaders = top-40 gainers ∪ curated crude names present in the matrix
    top_leaders = list(win.head(40).index)
    leaders = list(dict.fromkeys(
        top_leaders + [s for s in FORCE_LEADERS if s in rets.columns]))
    num_trials = len(leaders)

    # ── 4. clean connectedness (naive vs clean) ──────────────────────────────
    sign_hyps = {s: CRUDE_SIGN.get(s, -1) for s in leaders}  # crude-DOWN beneficiary default
    conn = C.crude_connectedness(rets, r_brent, mkt_perp, r_nifty, sign_hyps,
                                 leaders)
    win_map = {s: round(float(win.get(s, float('nan'))), 2) for s in conn}
    table = C.side_by_side_table(conn, win_map)

    print(f"\nCLEAN connectedness on {len(conn)} leaders — naive(collinear NIFTY+"
          f"Brent) vs clean(Brent + MKT_⊥Brent):")
    print(f"  {'symbol':16s}{'b_brent_naive':>14s}{'b_brent_clean':>14s}"
          f"{'t_clean':>9s}{'gen_n':>7s}{'gen_c':>7s}{'flip':>6s}")
    # show the curated crude names (the headline confirmation) + a few top movers
    show = [s for s in FORCE_LEADERS if s in conn] + \
           [s for s in top_leaders[:8] if s not in FORCE_LEADERS]
    for s in show:
        d = conn[s]
        print(f"  {s:16s}{d['naive']['beta_brent']:>14.3f}"
              f"{d['clean']['beta_clean']:>14.3f}{d['clean']['t']:>9.2f}"
              f"{str(d['naive']['verdict']['genuine']):>7s}"
              f"{str(d['clean']['verdict']['genuine']):>7s}"
              f"{str(d['flipped']):>6s}")

    # ── 5. select genuine crude-DOWN beneficiaries (clean b_brent<0 & |t|>=2) ─
    beneficiaries = sorted(
        [s for s, d in conn.items()
         if d["clean"]["beta_clean"] < 0 and d["clean"]["t"] <= -2.0],
        key=lambda s: conn[s]["clean"]["t"])          # most-significant first
    basket_note = None
    if not beneficiaries:
        # fall back to a-priori consumer roles, clearly labelled
        beneficiaries = [s for s in FORCE_LEADERS
                         if s in conn and CRUDE_SIGN.get(s) == -1][:6]
        basket_note = ("No name cleared the clean t<=-2 crude-DOWN-beneficiary "
                       "bar; basket falls back to a-priori consumer roles.")
    basket = beneficiaries[:6]
    print(f"\nGENUINE crude-DOWN beneficiaries (clean b_brent<0, t<=-2): "
          f"{beneficiaries if beneficiaries else 'NONE'}")
    print(f"Basket (≤6): {basket}" + (f"  [{basket_note}]" if basket_note else ""))

    # paints-genuine / tyres-spurious confirmation
    def _flag(sym):
        d = conn.get(sym)
        if not d:
            return "n/a"
        return ("GENUINE" if d["clean"]["verdict"]["genuine"] else "spurious")
    paints = {s: _flag(s) for s in ("ASIANPAINT.NS", "BERGEPAINT.NS") if s in conn}
    tyres = {s: _flag(s) for s in ("MRF.NS", "APOLLOTYRE.NS", "BALKRISIND.NS") if s in conn}
    print(f"  paints: {paints}")
    print(f"  tyres : {tyres}")

    # ── 6. MFE -> pre-declared target, then 3 expressions × 3 exit variants ───
    basket_w = {s: 1.0 for s in basket}
    paths = E.episode_returns(episodes, rets, basket_w)
    mfe = E.mfe_analysis(paths)
    target_pct = mfe["target_pct_declared"]
    print(f"\nMFE on the basket: median={mfe['median_pct']}% "
          f"p25={mfe['p25']}% p75={mfe['p75']}% -> pre-declared target={target_pct}% "
          f"(rounded {mfe['rounding']}); sensitivity={mfe['sensitivity_pct']}%")

    # synthetic columns for the pair + leveraged expressions (positions align
    # because we add them onto the same rets index)
    basket_ret = _basket_series(rets, basket)
    expressions: list[dict] = []

    # Conservative — EW beneficiary basket (long-only delivery)
    expressions.append(grade_expression(
        "Conservative — EW crude-DOWN beneficiary basket", "basket",
        episodes, rets, basket_w, r_nifty, target_pct=target_pct,
        num_trials=num_trials, note=basket_note))

    # Balanced — beta-neutral pair: long basket / short upstream (ONGC)
    pair_note = None
    if SHORT_LEG in rets.columns and len(basket):
        sh = rets[SHORT_LEG]
        df = pd.concat([basket_ret, sh], axis=1, keys=["L", "S"]).dropna()
        beta = float(np.cov(df["L"], df["S"])[0, 1] / np.var(df["S"])) \
            if np.var(df["S"]) > 0 else 1.0
        rets["PAIR_SPREAD"] = (basket_ret - beta * sh)
        pair_note = (f"Beta-neutral spread: long EW beneficiary basket, short "
                     f"{SHORT_LEG} (upstream), beta={round(beta,3)} (2-leg costs).")
        expressions.append(grade_expression(
            "Balanced — beta-neutral pair (long basket / short ONGC upstream)",
            "pair", episodes, rets, "PAIR_SPREAD", r_nifty,
            target_pct=target_pct, num_trials=num_trials, note=pair_note))
    else:
        print(f"[warn] balanced pair skipped: {SHORT_LEG} not in matrix")

    # Aggressive — 1.5x leveraged directional basket (labelled proxy)
    rets["AGG_LEVER"] = 1.5 * basket_ret
    agg_note = ("1.5x leveraged directional proxy on the beneficiary basket. A "
                "live aggressive tier would use a defined-risk debit spread; no "
                "historical option chain exists, so NO option payoff is fabricated "
                "— the leverage proxy is labelled as such. POP=None (not an option).")
    expressions.append(grade_expression(
        "Aggressive — 1.5x leveraged directional basket (defined-risk proxy)",
        "leveraged", episodes, rets, "AGG_LEVER", r_nifty,
        target_pct=target_pct, num_trials=num_trials, note=agg_note,
        payoff_pop=None))

    print("\nEXPRESSIONS × EXIT VARIANTS (strategy% / nifty% / excess% / beta / dials):")
    for ex in expressions:
        print(f"\n  {ex['name']}")
        for mode, v in ex["exit_variants"].items():
            nc = v["nifty_comparison"]
            od = v["outcome_dial"]; ed = v["expression_dial"]
            o = "SUPPR" if od["suppressed"] else f"{od['letter']}{od['score']}"
            e_ = "SUPPR" if ed["suppressed"] else f"{ed['letter']}{ed['score']}"
            print(f"    [{mode:6s}] strat={nc['strategy_total_pct']:+7.2f}% "
                  f"nifty={nc['nifty_total_pct']:+7.2f}% excess={nc['excess_pct']:+7.2f}% "
                  f"beta={nc['nifty_beta']} beat={nc['pct_episodes_beat']}% "
                  f"verdict={v['battery_summary']['verdict']} "
                  f"DSR={v['battery_summary']['deflated_sharpe']} "
                  f"out={o} expr={e_}")

    # ── top-gainers JSON rows (all scanned names; nifty_comparison on top 25) ─
    top_rows = []
    enrich = set(list(win.head(25).index) + [s for s in FORCE_LEADERS if s in conn])
    for rank, (s, v) in enumerate(win.dropna().items(), start=1):
        row = {"symbol": s, "industry": ind.get(s, ""),
               "event_win_ret_pct": round(float(v), 2), "rank": rank}
        if s in enrich:
            nc = E.backtest_exits(episodes, rets, s, r_nifty, modes=("fixed",),
                                  target_pct=None, hold_bars=HOLD)["fixed"]
            row["nifty_comparison"] = nc["nifty_comparison"]
        top_rows.append(row)

    n_episodes = len(episodes)
    blob = {
        "view": "crude_down",
        "generated": _now(),
        "universe": {"n_total": len(U.load_universe()),
                     "n_with_data": n_constituents,
                     "weighting": "equal_weight",
                     "n_scanned_event_window": n_scanned},
        "events": ev_meta,
        "factors": {
            "b_NB": round(b_NB, 4), "b_NB_t": round(t_NB, 2),
            "b_NB_ewmkt": round(b_NB_ew, 4), "b_NB_ewmkt_t": round(t_NB_ew, 2),
            "mkt_perp_built_from": "^NSEI residual vs Brent (FWL step 1)",
            "interpretation": (
                f"NIFTY loads {b_NB:+.4f} on Brent (t={t_NB:.2f}); MKT_⊥Brent "
                "removes that crude channel so per-name b_brent is no longer "
                "biased by the market's own crude sensitivity."),
        },
        "top_gainers": top_rows,
        "connectedness": conn,
        "side_by_side": table,
        "genuine_beneficiaries": beneficiaries,
        "basket": basket,
        "basket_note": basket_note,
        "paints_check": paints,
        "tyres_check": tyres,
        "mfe": mfe,
        "expressions": expressions,
        "honesty": {
            "n_events": n_episodes,
            "trial_group": "crude_v3",
            "num_trials_deflation": num_trials,
            "note": (f"{n_episodes} crash episodes — below MinTRL for honest dial "
                     "scoring; outcome/expression dials suppress on "
                     "insufficient_data. Aggressive tier is a labelled leverage "
                     "proxy (no historical option chain)."),
        },
    }
    out_path = U.OUT_DIR + "/crude_v3.json"
    with open(out_path, "w") as f:
        json.dump(blob, f, indent=2, default=str)
    print(f"\n[saved] {out_path}")
    print("DONE.")


if __name__ == "__main__":
    main()
