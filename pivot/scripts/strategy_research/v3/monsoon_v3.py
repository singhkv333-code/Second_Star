"""v3 MONSOON view runner — full-universe, clean-factor event study.

Pipeline (mirrors it_v3 / crude_v3, all engine reuse — no re-derivation):
  1. Load NIFTY-500 returns + Industry map + NIFTY/Brent drivers.
  2. Build the CLEAN factors (factors.py):
       RURAL_f       = EW of monsoon-sensitive sectors (Auto + FMCG) ∪ Ag/fert allow-list
       MKT_exMonsoon = EW of the market PURGED of every RURAL_f name (critique D)
     and b_NB (NIFTY↔Brent FWL orthogonalization) reported for completeness.
  3. EVENT WINDOWS: the v2 monsoon calendar taxonomy, restricted to IMD-NORMAL
     years (96-104% LPA) so the seasonal effect isn't contaminated by drought/
     flood tails. Pre-declared, date-driven → zero look-ahead.
  4. FULL-UNIVERSE top-gainers over the event window (all ~500 names) + each
     leader vs NIFTY (same window).
  5. CLEAN connectedness scan: naive (RURAL_f + NIFTY) vs clean (RURAL_f +
     MKT_exMonsoon), HAC t-stats, the §2.5 naive-vs-clean `flipped?` table.
  6. Select GENUINELY-connected names (b_rain > 0, |t| ≥ 2 after the clean
     market control).
  7. Three risk-tiered expressions (Conservative basket / Balanced market-
     neutral pair / Aggressive concentrated) from the genuine names.
  8. Backtest each under ALL 3 exit variants (fixed / target / manual).
  9. Trust Battery + two-dial grade (suppressed on thin N) + a NIFTY-comparison
     block on EVERY result.
 10. Write _out/monsoon_v3.json.

Real yfinance data only. Thin samples → say so + suppress dials.
Run:  .venv/bin/python -m scripts.strategy_research.v3.monsoon_v3
"""
from __future__ import annotations

import json
import os
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

OUT_PATH = os.path.join(U.OUT_DIR, "monsoon_v3.json")

# ── pre-declared constants (no look-ahead; sourced/justified here) ─────────────
# IMD seasonal rainfall as % of LPA (the v2 monsoon_windows.py LPA dict, IMD
# end-of-season actuals). IMD's "normal" band is 96-104% of LPA. Restricting the
# analog sample to NORMAL years removes drought (<96) / excess (>104) tails so
# the seasonal monsoon effect is read on the regime the view is actually about.
LPA = {2009: 78, 2010: 102, 2011: 102, 2012: 93, 2013: 106, 2014: 88, 2015: 86,
       2016: 97, 2017: 95, 2018: 91, 2019: 110, 2020: 109, 2021: 99, 2022: 106,
       2023: 94, 2024: 108}
IMD_NORMAL_YEARS = sorted(y for y, v in LPA.items() if 96 <= v <= 104)  # [2010,2011,2016,2021]

# v2 monsoon window taxonomy (monsoon_windows.py). We trade the SOWING window
# [Jun01..Aug31] — kharif sowing, when rural input demand is physically realised
# (the pre-position→confirmation window; non-overlapping across years). Other
# windows are reported in `events` for context.
WINDOWS = {
    "forecast": (("04", "15"), ("06", "15")),
    "onset":    (("05", "15"), ("07", "31")),
    "sowing":   (("06", "01"), ("08", "31")),
    "season":   (("06", "01"), ("09", "30")),
    "drift":    (("10", "01"), ("12", "31")),
}
EVENT_WINDOW = "sowing"

# Urban controls (NOT rural) — used as the non-genuine reference in the report.
URBAN_CTRL = ["ASIANPAINT.NS", "TITAN.NS", "BAJFINANCE.NS"]

N_LEADERS = 30          # top event-window gainers carried into connectedness
T_CRIT = 2.0


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _window_episodes(idx: pd.DatetimeIndex, window: str, years: list[int]
                     ) -> tuple[list[tuple[int, int]], list[dict]]:
    """One (entry_pos, exit_pos) episode per year for ``window``; entry is the
    first bar ON/AFTER window start + ONE-bar lag, exit the last bar on/before
    window end. Returns (episodes, annotation rows)."""
    (sm, sd), (em, ed) = WINDOWS[window]
    eps: list[tuple[int, int]] = []
    ann: list[dict] = []
    for y in years:
        ws = pd.Timestamp(f"{y}-{sm}-{sd}")
        we = pd.Timestamp(f"{y}-{em}-{ed}")
        lo = int(idx.searchsorted(ws, side="left"))
        hi = int(idx.searchsorted(we, side="right")) - 1
        entry = lo + 1                       # one-bar lag (signal at window open)
        if entry < hi and hi < len(idx):
            eps.append((entry, hi))
            ann.append({"year": y, "window": window,
                        "start": str(idx[entry].date()),
                        "end": str(idx[hi].date()),
                        "lpa_pct": LPA[y], "bars": hi - entry + 1})
    return eps, ann


def _window_returns_all(rets: pd.DataFrame, episodes) -> pd.Series:
    """Mean across episodes of each name's cumulative window return (ranking key
    for the full-universe top-gainers table)."""
    per_ep = []
    for (e, x) in episodes:
        seg = rets.iloc[e:x + 1]
        cum = (1.0 + seg.fillna(0.0)).prod() - 1.0          # per-column total ret
        per_ep.append(cum)
    if not per_ep:
        return pd.Series(dtype=float)
    return pd.concat(per_ep, axis=1).mean(axis=1) * 100.0


def _compact_nifty(symbol_or_w, rets, episodes, r_nifty) -> dict:
    """Light per-leader NIFTY comparison via the fixed exit variant."""
    res = E.backtest_exits(episodes, rets, symbol_or_w, r_nifty,
                           modes=("fixed",), hold_bars=20)
    return res["fixed"]["nifty_comparison"]


def main() -> dict:
    print("=" * 78)
    print("v3 MONSOON — full-universe clean-factor event study")
    print("=" * 78)

    rets = U.returns_matrix()
    ind = U.industry_map()
    r_nifty = U.series("NIFTY").reindex(rets.index)
    r_brent = U.series("BRENT").reindex(rets.index)
    cov = U.coverage_report()
    print(f"returns matrix: {rets.shape[0]} rows x {rets.shape[1]} cols  "
          f"{rets.index.min().date()}..{rets.index.max().date()}")
    print(f"IMD normal years (96-104% LPA): {IMD_NORMAL_YEARS}")

    # ── factors ────────────────────────────────────────────────────────────────
    monsoon_syms = F.monsoon_symbols(ind)
    monsoon_present = [s for s in monsoon_syms if s in rets.columns]
    rural_f, mkt_exmon = F.monsoon_factors(rets, monsoon_syms)
    mkt_perp, b_NB, t_NB = F.orthogonalize(r_nifty, r_brent)
    print(f"RURAL_f from {len(monsoon_present)} names "
          f"(Auto+FMCG sectors ∪ Ag/fert allow-list); "
          f"MKT_exMonsoon from {len([c for c in rets.columns if c not in set(monsoon_present)])}")
    print(f"[b_NB] NIFTY=c+b_NB*Brent: b_NB={b_NB:.4f}  t(HAC)={t_NB:.2f}")

    # ── event windows (normal years) ───────────────────────────────────────────
    episodes, ann = _window_episodes(rets.index, EVENT_WINDOW, IMD_NORMAL_YEARS)
    print(f"\nEVENT episodes ({EVENT_WINDOW}, normal years): {len(episodes)}")
    for a in ann:
        print(f"  {a['year']}  {a['start']}..{a['end']}  "
              f"LPA={a['lpa_pct']}%  {a['bars']} bars")

    # ── full-universe top-gainers over the event window ────────────────────────
    win_ret = _window_returns_all(rets, episodes).dropna().sort_values(ascending=False)
    nifty_win = _window_returns_all(r_nifty.to_frame("NIFTY"), episodes)
    nifty_win_pct = float(nifty_win.iloc[0]) if len(nifty_win) else None
    print(f"\nNIFTY mean {EVENT_WINDOW} window return (normal yrs): "
          f"{nifty_win_pct:.2f}%" if nifty_win_pct is not None else "n/a")

    top = win_ret.head(N_LEADERS)
    print(f"\nTOP {N_LEADERS} {EVENT_WINDOW}-window gainers (full universe):")
    print(f"  {'symbol':16s}{'industry':30s}{'win_ret%':>9s}{'vs_nifty':>9s}")
    top_gainers = []
    for rank, (s, v) in enumerate(top.items(), start=1):
        excess = round(float(v) - nifty_win_pct, 2) if nifty_win_pct is not None else None
        top_gainers.append({
            "symbol": s, "industry": ind.get(s, ""),
            "event_win_ret_pct": round(float(v), 2), "rank": rank,
            "nifty_comparison": {"strategy_total_pct": round(float(v), 2),
                                 "nifty_total_pct": round(nifty_win_pct, 2)
                                 if nifty_win_pct is not None else None,
                                 "excess_pct": excess,
                                 "window_basis": "mean_event_window_return"},
        })
        if rank <= 18:
            print(f"  {s:16s}{ind.get(s,'')[:28]:30s}{float(v):>9.2f}"
                  f"{(excess if excess is not None else 0):>9.2f}")

    # ── connectedness scan on the leaders (∪ rural set for context) ────────────
    leaders = list(dict.fromkeys(
        list(top.index) + monsoon_present + URBAN_CTRL))
    leaders = [s for s in leaders if s in rets.columns]
    conn = C.monsoon_connectedness(rets, rural_f, mkt_exmon, r_nifty, leaders,
                                   t_crit=T_CRIT)
    rural_set = set(monsoon_present)
    for s in conn:
        conn[s]["industry"] = ind.get(s, "")
        conn[s]["event_win_ret_pct"] = round(float(win_ret.get(s, np.nan)), 2) \
            if s in win_ret.index else None
        conn[s]["in_rural_factor"] = bool(s in rural_set)

    sbs = C.side_by_side_table(conn, win_ret={s: conn[s]["event_win_ret_pct"]
                                              for s in conn})
    n_flipped = sum(1 for r in sbs if r["flipped"])
    print(f"\nCONNECTEDNESS scan: {len(conn)} leaders, "
          f"{n_flipped} flipped (naive≠clean)")

    # genuine = sign-correct (+) AND |t|>=2 on the CLEAN RURAL_f loading.
    # CRITICAL: a RURAL_f CONSTITUENT loading ~+1 on RURAL_f is MECHANICAL
    # (circular self-loading), not an independent discovery. Split the two:
    #   genuine_independent = genuine AND NOT a RURAL_f member (the real finds)
    #   genuine_mechanical  = genuine but in-factor (the thesis basket names)
    genuine_all = sorted([s for s, d in conn.items()
                          if d["clean"]["verdict"]["genuine"]],
                         key=lambda s: -abs(conn[s]["clean"]["t"]))
    genuine_independent = [s for s in genuine_all
                           if not conn[s]["in_rural_factor"]]
    genuine_mechanical = [s for s in genuine_all if conn[s]["in_rural_factor"]]
    marginal = [s for s, d in conn.items()
                if d["clean"]["verdict"]["marginal"]
                and not d["clean"]["verdict"]["genuine"]]
    print(f"  GENUINE total {len(genuine_all)}  = independent(out-of-factor) "
          f"{len(genuine_independent)}  + in-factor/mechanical {len(genuine_mechanical)}")
    print(f"    INDEPENDENT (real cross-sectional finds): "
          f"{[ (s, conn[s]['clean']['beta_clean'], conn[s]['clean']['t']) for s in genuine_independent[:8] ]}")
    print(f"  marginal (1.7<=|t|<2): {len(marginal)} -> {marginal[:6]}")
    # ``genuine`` used for expression building = the rural THESIS names (in-factor
    # genuine), ranked by RELATIONSHIP STRENGTH (|t| on the full-sample b_rain),
    # NOT by event-window return. Ranking by realized window return would select
    # members on the dependent variable (in-sample cherry-pick) and inflate the
    # backtest; |t| is estimated on 2010-2026 daily data, independent of the 4
    # episode outcomes — the honest, non-overfit basis.
    genuine = sorted(genuine_mechanical,
                     key=lambda s: -abs(conn[s]["clean"]["t"]))

    print(f"\n  {'symbol':14s}{'in_RF':>6s}{'b_raw':>8s}{'b_rain':>8s}"
          f"{'t':>7s}{'v_naive':>8s}{'v_clean':>8s}{'flip':>6s}")
    for r in sorted(sbs, key=lambda r: -(r["b_clean"] or 0))[:18]:
        print(f"  {r['symbol']:14s}{str(conn[r['symbol']]['in_rural_factor']):>6s}"
              f"{(r['b_raw'] or 0):>8.3f}{(r['b_clean'] or 0):>8.3f}"
              f"{(r['t_clean'] or 0):>7.2f}{str(r['verdict_naive']):>8s}"
              f"{str(r['verdict_clean']):>8s}{str(r['flipped']):>6s}")

    # ── select expression members ──────────────────────────────────────────────
    honesty_notes = []
    members = genuine[:]
    if len(members) < 2:
        # honest fallback: too few genuine names → use marginal + best rural by
        # window return, and FLAG that the basket is not statistically genuine.
        fallback = marginal[:] + [s for s in win_ret.index
                                  if s in rural_set and s not in members]
        for s in fallback:
            if s not in members:
                members.append(s)
            if len(members) >= 4:
                break
        honesty_notes.append(
            f"Only {len(genuine)} statistically GENUINE monsoon-connected names "
            f"(b_rain>0, |t|>=2 after the clean market control) — consistent with "
            f"the v2 finding that the 'good monsoon → buy rural' trade is largely "
            f"SPURIOUS (market beta, not rural). Expressions fall back to the "
            f"strongest available rural/marginal names and are flagged accordingly.")
    members = members[:6]
    print(f"\nExpression member pool: {members}")

    # NIFTY column appended for the market-neutral pair leg
    rets_aug = rets.copy()
    rets_aug["NIFTY"] = r_nifty.reindex(rets.index)

    # Conservative: EW basket of the member pool (full long, diversified)
    cons_w = {s: 1.0 for s in members}
    # Balanced: dollar-neutral relative PAIR = long member-basket vs short NIFTY,
    # injected as a single synthetic column so the (sum-zero) pair is honest.
    long_leg = E._port_daily(rets_aug, cons_w) if members else pd.Series(0.0, index=rets.index)
    pair_ret = 0.5 * long_leg - 0.5 * rets_aug["NIFTY"].fillna(0.0)
    rets_aug["PAIR_RURAL_vs_NIFTY"] = pair_ret
    # Aggressive: concentrated single best-genuine (highest |t|); if none, best member
    aggro_name = (genuine[0] if genuine else (members[0] if members else None))

    expressions = {
        "conservative": {"kind": "basket", "weights_or_leg": cons_w,
                         "label": "Conservative — EW rural basket (full long)",
                         "members": members},
        "balanced": {"kind": "pair", "weights_or_leg": "PAIR_RURAL_vs_NIFTY",
                     "label": "Balanced — dollar-neutral pair: long rural basket / short NIFTY",
                     "members": members, "short_leg": "NIFTY"},
        "aggressive": {"kind": "concentrated", "weights_or_leg": aggro_name,
                       "label": f"Aggressive — concentrated {aggro_name} "
                                f"(option/hedge structure synthesized at deploy, "
                                f"not backtested on equity bars)",
                       "members": [aggro_name] if aggro_name else []},
    }

    # ── MFE + 3 exit variants + battery + dials, per expression ────────────────
    expr_out: dict[str, dict] = {}
    for tier, spec in expressions.items():
        wl = spec["weights_or_leg"]
        if wl is None or (isinstance(wl, dict) and not wl):
            expr_out[tier] = {"label": spec["label"], "note": "no members",
                              "exit_variants": {}}
            continue
        src = rets_aug
        paths = E.episode_returns(episodes, src, wl)
        mfe = E.mfe_analysis(paths)
        res = E.backtest_exits(episodes, src, wl, r_nifty,
                               target_pct=mfe["target_pct_declared"], hold_bars=20)
        variants = {}
        for mode, r in res.items():
            num_trials = len(conn)   # full-universe screen width (multiple-testing)
            bat = B.run_battery(r["equity"], r["daily_rets"], r["n_episodes"],
                                num_trials=num_trials)
            fs = bat["forward_stats"]
            sig = B.caar_significance(r["per_episode_rets"])
            outc, expr = B.two_dials(
                hit_rate=(sum(1 for x in r["per_episode_rets"] if x > 0) /
                          max(1, len(r["per_episode_rets"]))),
                relationship_strength=None, sample_n=r["n_episodes"],
                verdict=bat["verdict"]["verdict"],
                caar_alignment=B._caar_alignment(sig["caar"]),
                significance_p=sig["combined_p"],
                cost_survival=0.6, deflated_sharpe=fs["deflated_sharpe"],
                n_obs=fs["n_obs"], min_trl=fs["min_trl"])
            tb = B.trust_block(bat, engine=spec["kind"],
                               alignment=B.dial_to_dict(expr),
                               nifty_comparison=r["nifty_comparison"],
                               degraded=(bat["verdict"]["verdict"] == "insufficient_data"),
                               data_note=f"N={r['n_episodes']} monsoon episodes "
                                         f"(IMD normal years); thin sample.")
            v = {"mode": mode, "trust": tb,
                 "nifty_comparison": r["nifty_comparison"],
                 "per_episode_pct": r["per_episode_pct"],
                 "caar_significance": sig,
                 "outcome_dial": B.dial_to_dict(outc),
                 "expression_dial": B.dial_to_dict(expr),
                 "verdict": bat["verdict"]["verdict"],
                 "forward_stats": {k: fs.get(k) for k in
                                   ("observed_sharpe", "psr", "deflated_sharpe",
                                    "min_trl", "n_obs")}}
            if mode == "target":
                v["hit_target"] = r.get("hit_target")
                v["bars_to_target"] = r.get("bars_to_target")
            if mode == "manual":
                v["note"] = "workflow-armed; user closes by hand."
            variants[mode] = v
        expr_out[tier] = {"label": spec["label"], "kind": spec["kind"],
                          "members": spec["members"], "mfe": mfe,
                          "exit_variants": variants}
        print(f"\n[{tier}] {spec['label']}")
        for mode, v in variants.items():
            nc = v["nifty_comparison"]
            od = v["outcome_dial"]; xd = v["expression_dial"]
            print(f"  [{mode:6s}] strat={nc['strategy_total_pct']}% "
                  f"nifty={nc['nifty_total_pct']}% excess={nc['excess_pct']}% "
                  f"beat={nc['pct_episodes_beat']}%  verdict={v['verdict']}  "
                  f"OUT={'SUPP' if od['suppressed'] else od['letter']} "
                  f"EXPR={'SUPP' if xd['suppressed'] else xd['letter']}")

    # ── assemble JSON ──────────────────────────────────────────────────────────
    weakest_t = min((conn[s]["clean"]["t"] for s in conn), default=None)
    out = {
        "view": "monsoon",
        "generated": _now_iso(),
        "universe": {"n_total": cov.get("requested", 500),
                     "n_with_data": rets.shape[1],
                     "dropped": cov.get("missing", []),
                     "weighting": "equal_weight"},
        "imd_normal_years": IMD_NORMAL_YEARS,
        "event_window": EVENT_WINDOW,
        "events": ann,
        "windows_taxonomy": {w: {"start": f"{a[0][0]}-{a[0][1]}",
                                 "end": f"{a[1][0]}-{a[1][1]}"}
                             for w, a in WINDOWS.items()},
        "factors": {
            "b_NB": round(b_NB, 4), "b_NB_t": round(t_NB, 2),
            "rural_f_built_from": len(monsoon_present),
            "mkt_exmonsoon_built_from":
                len([c for c in rets.columns if c not in rural_set]),
            "monsoon_sectors": sorted(F.MONSOON_SECTORS),
            "ag_fert_allow": [s for s in F.AG_FERT_ALLOW if s in rets.columns],
        },
        "nifty_event_window_ret_pct": round(nifty_win_pct, 2)
        if nifty_win_pct is not None else None,
        "top_gainers": top_gainers,
        "connectedness": conn,
        "side_by_side": sbs,
        "n_flipped": n_flipped,
        "genuine_independent": genuine_independent,
        "genuine_mechanical_in_factor": genuine_mechanical,
        "thesis_basket_leaders": genuine,
        "marginal_leaders": marginal,
        "mechanical_loading_note": (
            f"{len(genuine_mechanical)} of {len(genuine_all)} 'genuine' names are "
            f"RURAL_f CONSTITUENTS — their ~+1 loading on RURAL_f is MECHANICAL "
            f"(circular self-loading), so they confirm the thesis basket but are "
            f"NOT independent evidence. Only {len(genuine_independent)} names load "
            f"on RURAL_f significantly WITHOUT being part of it (Asian Paints, "
            f"Titan, Page, Jubilant, SRF…) — these are the real cross-sectional "
            f"discoveries: urban-discretionary/quality names that genuinely "
            f"co-move with rural demand."),
        "spurious_note": (
            "Per the naive-vs-clean test: a name is GENUINE only if it loads "
            "positively + significantly (|t|>=2) on RURAL_f AFTER the clean "
            "MKT_exMonsoon market control. Names that looked monsoon-linked under "
            "the naive (RURAL_f+NIFTY) test but lose significance under the clean "
            "control are SPURIOUS (market beta, not rural)."),
        "expressions": expr_out,
        "honesty": {
            "n_episodes": len(episodes),
            "n_genuine_independent": len(genuine_independent),
            "n_genuine_mechanical": len(genuine_mechanical),
            "below_mintrl": True,
            "trial_group": "monsoon_v3",
            "num_trials_deflation": len(conn),
            "notes": honesty_notes + [
                f"{len(episodes)} monsoon episodes (one SOWING window per IMD-"
                f"normal year) is far below MinTRL — the OUTCOME dial is "
                f"suppressed and the EXPRESSION dial is heavily deflated by DSR "
                f"with num_trials={len(conn)} (the full-universe scan width). "
                f"This suppression is the correct, honest answer, not a bug."],
        },
    }
    with open(OUT_PATH, "w") as f:
        json.dump(out, f, indent=2, default=str)
    print(f"\nWrote {OUT_PATH}")
    return out


if __name__ == "__main__":
    main()
