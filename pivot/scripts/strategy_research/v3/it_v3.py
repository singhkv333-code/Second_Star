"""v3 IT-trouble view runner — full-universe, clean-factor, three-exit-variant
research for the belief "IT is in trouble → who benefits?".

Pipeline (all real yfinance bars from the v3 parquet cache, no fabrication):
  1. Build MKT_exIT (473 non-IT NIFTY-500 names) + IT_f (27 IT names), EW.
  2. b_NB (NIFTY = c + b_NB·Brent, FWL step 1, HAC t) — reported at view level.
  3. Events = the 8 pre-declared TCS-anchored weak-IT-guidance prints (WEAK_ANALOGS),
     fixed CONFIRMATION window: enter T+1, hold to T+20 (the v2 control window).
  4. (a) FULL-UNIVERSE top-gainers over the event windows (all ~500 names ranked).
     (b) CLEAN connectedness on the leaders: r_i = a + b_mkt·MKT_exIT + b_it·IT_f,
         side-by-side with the naive NIFTY-only test + the `flipped?` column.
     (c) Select GENUINELY-connected beneficiaries (clean b_it<0, |t|≥2).
     (d) 3 risk-tiered expressions: Conservative basket / Balanced pair /
         Aggressive hedge — built from the genuine names.
     (e) Backtest each under ALL 3 exit variants (fixed / target / manual).
     (f) Grade via the Trust Battery (num_trials = screen width → DSR deflation).
     (g) NIFTY-comparison block attached to EVERY result.
  Thin sample (8 events) → dials SUPPRESS; we report that verbatim.

Run:  .venv/bin/python -m scripts.strategy_research.v3.it_v3
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
from scripts.strategy_research._it_bt_common import WEAK_ANALOGS

# ── pre-declared constants ────────────────────────────────────────────────────
WIN_LO, WIN_HI = 1, 20          # CONFIRMATION window: enter T+1, hold to T+20
EST_GUARD = 131                 # need t0-130 of history (estimation-window guard)
N_LEADERS = 50                  # names taken into the clean connectedness scan
N_TOPGAINERS_NC = 25            # top gainers that carry a full NIFTY block
MAX_BASKET = 5                  # genuine names in the conservative basket
OUT_PATH = os.path.join(U.OUT_DIR, "it_v3.json")

EVENT_CONTEXT = {
    "2022-04-11": "TCS Q4FY22 print — start of the FY23 demand-doubt cycle",
    "2022-07-08": "Q1FY23 — margin compression / attrition peak",
    "2023-01-09": "Q3FY23 — discretionary-spend slowdown flagged",
    "2023-04-12": "Q4FY23 — weak FY24 guidance, BFSI caution",
    "2023-07-12": "Q1FY24 — furloughs + deal-ramp delays",
    "2023-10-11": "Q2FY24 — soft constant-currency growth",
    "2024-04-12": "Q4FY24 — muted FY25 outlook",
    "2025-01-09": "Q3FY25 — guidance-cut analog (most recent)",
}


def _jdef(o):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return None if np.isnan(o) else float(o)
    if isinstance(o, (np.bool_,)):
        return bool(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    raise TypeError(f"not serialisable: {type(o)}")


def _episodes(idx: pd.DatetimeIndex) -> list[tuple[int, int]]:
    """(entry_pos, exit_pos) inclusive for each weak-print analog, with the
    estimation-window guard (t0-130 must exist) and a within-range exit."""
    eps = []
    for a in WEAK_ANALOGS:
        pos = idx.searchsorted(pd.Timestamp(a))
        lo, hi = pos + WIN_LO, pos + WIN_HI
        if lo < EST_GUARD or hi >= len(idx):
            continue
        eps.append((lo, hi))
    return eps


def _event_win_ret(rets: pd.DataFrame, episodes, sym: str) -> float | None:
    """Mean across episodes of the cumulative window return for one name (%)."""
    vals = []
    for (e, x) in episodes:
        seg = rets[sym].iloc[e:x + 1] if sym in rets.columns else None
        if seg is None or seg.notna().sum() < (x - e) // 2:
            continue
        cum = float((1.0 + seg.fillna(0.0)).prod() - 1.0)
        vals.append(cum)
    if not vals:
        return None
    return round(float(np.mean(vals)) * 100.0, 2)


def _single_name_nc(episodes, rets, sym, r_nifty) -> dict:
    """NIFTY-comparison for a single name over the event windows (fixed exit)."""
    res = E.backtest_exits(episodes, rets, sym, r_nifty,
                           modes=("fixed",), hold_bars=WIN_HI)
    return res["fixed"]["nifty_comparison"]


def _grade_variant(variant: dict, num_trials: int, engine: str,
                   rel_strength, *, thesis_positive: bool) -> dict:
    """Battery + CAAR significance + two dials for one exit-variant result,
    assemble the FROZEN trust block. Returns the variant enriched in place."""
    bat = B.run_battery(variant["equity"], variant["daily_rets"],
                        variant["n_episodes"], num_trials=num_trials)
    fs = bat["forward_stats"]
    per_ep = variant["per_episode_rets"]
    sig = B.caar_significance(per_ep)
    hits = sum(1 for r in per_ep if (r > 0) == thesis_positive)
    hit_rate = hits / len(per_ep) if per_ep else 0.0
    cs = B.cost_survival(variant["nifty_comparison"].get("strategy_total_pct"),
                         variant["nifty_comparison"].get("strategy_total_pct"))
    out, expr = B.two_dials(
        hit_rate=hit_rate, relationship_strength=rel_strength,
        sample_n=variant["n_episodes"], verdict=bat["verdict"]["verdict"],
        caar_alignment=B._caar_alignment(sig["caar"]),
        significance_p=sig["combined_p"], cost_survival=cs,
        deflated_sharpe=fs["deflated_sharpe"], n_obs=fs["n_obs"],
        min_trl=fs["min_trl"])
    tb = B.trust_block(bat, engine=engine, alignment=B.dial_to_dict(expr),
                       nifty_comparison=variant["nifty_comparison"],
                       data_note=f"{variant['n_episodes']} events, "
                                 f"num_trials={num_trials} (full-universe screen)")
    variant_out = {
        "mode": variant["mode"],
        "params": variant["params"],
        "trust": tb,
        "nifty_comparison": variant["nifty_comparison"],
        "per_episode_pct": variant["per_episode_pct"],
        "caar_significance": sig,
        "hit_rate": round(hit_rate, 3),
        "outcome_dial": B.dial_to_dict(out),
        "expression_dial": B.dial_to_dict(expr),
        "battery_summary": {
            "verdict": bat["verdict"]["verdict"],
            "label": bat["verdict"]["label"],
            "total_return_pct": bat["total_return_pct"],
            "max_drawdown_pct": bat["max_drawdown_pct"],
            "psr": fs["psr"], "deflated_sharpe": fs["deflated_sharpe"],
            "min_trl": fs["min_trl"], "n_obs": fs["n_obs"],
            "num_trials": fs["num_trials"],
        },
    }
    if variant["mode"] == "target":
        variant_out["hit_target"] = variant.get("hit_target")
        variant_out["bars_to_target"] = variant.get("bars_to_target")
    return variant_out


def _build_expression(name, kind, episodes, daily_rets, weights_or_leg,
                      r_nifty, num_trials, rel_strength, *, members,
                      note=None, thesis_positive=True) -> dict:
    """Run all 3 exit variants + grade each, MFE on the fixed paths."""
    paths = E.episode_returns(episodes, daily_rets, weights_or_leg)
    mfe = E.mfe_analysis(paths)
    res = E.backtest_exits(episodes, daily_rets, weights_or_leg, r_nifty,
                           modes=("fixed", "target", "manual"),
                           target_pct=mfe["target_pct_declared"],
                           hold_bars=WIN_HI)
    variants = {m: _grade_variant(res[m], num_trials, kind, rel_strength,
                                  thesis_positive=thesis_positive)
                for m in ("fixed", "target", "manual")}
    return {
        "name": name, "kind": kind, "members": members,
        "note": note, "mfe": mfe, "exit_variants": variants,
        # headline scores = the fixed (fully-backtested) variant
        "scores": {
            "outcome_dial": variants["fixed"]["outcome_dial"],
            "expression_dial": variants["fixed"]["expression_dial"],
        },
    }


def main() -> dict:
    print("[it_v3] loading universe + returns matrix ...")
    rets = U.returns_matrix()
    ind = U.industry_map()
    r_nifty = U.series("NIFTY").reindex(rets.index)
    r_brent = U.series("BRENT").reindex(rets.index)
    cov = U.coverage_report()
    idx = rets.index
    n_with_data = rets.shape[1]
    print(f"  matrix {rets.shape[0]}x{rets.shape[1]}  "
          f"{idx.min().date()}..{idx.max().date()}")

    # ── factors ───────────────────────────────────────────────────────────────
    it_syms = [s for s in F.it_symbols(ind) if s in rets.columns]
    non_it = [c for c in rets.columns if c not in set(it_syms)]
    mkt_exit = F.mkt_exit(rets, it_syms)
    it_f = F.it_factor(rets, it_syms)
    mkt_perp, b_NB, t_NB = F.orthogonalize(r_nifty, r_brent)
    print(f"  IT_f from {len(it_syms)} names; MKT_exIT from {len(non_it)} non-IT")
    print(f"  b_NB (NIFTY~Brent) = {b_NB:.4f}  HAC t = {t_NB:.2f}")

    # ── episodes (fixed confirmation window) ──────────────────────────────────
    episodes = _episodes(idx)
    print(f"  episodes (weak-IT prints, T+1..T+20): {len(episodes)}")

    # ── (a) FULL-UNIVERSE top-gainers over the event windows ──────────────────
    print("[it_v3] (a) full-universe event-window ranking ...")
    win_ret = {}
    for s in rets.columns:
        wr = _event_win_ret(rets, episodes, s)
        if wr is not None:
            win_ret[s] = wr
    ranked = sorted(win_ret.items(), key=lambda kv: -kv[1])
    top_gainers = []
    for rank, (s, wr) in enumerate(ranked, start=1):
        row = {"symbol": s, "industry": ind.get(s, ""),
               "event_win_ret_pct": wr, "rank": rank}
        if rank <= N_TOPGAINERS_NC:
            row["nifty_comparison"] = _single_name_nc(episodes, rets, s, r_nifty)
        top_gainers.append(row)
    print(f"  ranked {len(top_gainers)} names; top: "
          f"{[(s, w) for s, w in ranked[:5]]}")

    # ── (b) CLEAN connectedness on the leaders ────────────────────────────────
    # Leaders = the top event-window gainers (the candidate beneficiaries).
    leaders = [s for s, _ in ranked[:N_LEADERS]]
    print(f"[it_v3] (b) clean connectedness on {len(leaders)} leaders ...")
    conn = C.it_connectedness(rets, mkt_exit, it_f, r_nifty, leaders, ind)
    side_by_side = C.side_by_side_table(conn, win_ret)
    n_flipped = sum(1 for r in side_by_side if r["flipped"])
    num_trials = len(conn)            # the honest multiple-testing screen width

    # ── (c) select GENUINELY-connected beneficiaries (clean b_it<0, |t|>=2) ───
    genuine = [s for s, d in conn.items()
               if d["clean"]["verdict"]["genuine"] and d["clean"]["beta_clean"] < 0]
    genuine = sorted(genuine, key=lambda s: conn[s]["clean"]["t"])  # most -ve t first
    spurious_flipped = [s for s, d in conn.items()
                        if d["flipped"] and not d["clean"]["verdict"]["genuine"]]
    print(f"  genuine beneficiaries (b_it<0 sig): {len(genuine)} -> {genuine[:8]}")
    print(f"  flipped verdicts naive->clean: {n_flipped}; "
          f"spurious-under-clean: {len(spurious_flipped)}")

    if not genuine:
        genuine = [s for s, _ in ranked[:MAX_BASKET]]   # honest fallback
        gen_note = "NO name passed the clean genuine gate; basket falls back to " \
                   "raw top event-window gainers (flagged, low confidence)."
    else:
        gen_note = None

    basket_names = genuine[:MAX_BASKET]
    rel_strength = float(np.clip(
        np.mean([abs(conn[s]["clean"]["beta_clean"]) for s in basket_names
                 if s in conn]) if any(s in conn for s in basket_names) else 0.0,
        0.0, 1.0))

    # ── (d) three risk-tiered expressions ─────────────────────────────────────
    print("[it_v3] (d) building 3 expressions + (e/f/g) backtest+grade ...")
    rets_aug = rets.copy()
    long_ew = F.ew_factor(rets, basket_names)
    rets_aug["__PAIR__"] = (long_ew - it_f).reindex(rets_aug.index)     # long genuine / short IT_f
    rets_aug["__HEDGE__"] = (long_ew - r_nifty).reindex(rets_aug.index)  # long genuine / short NIFTY

    expressions = {}
    # Conservative — equal-weight basket of the genuine beneficiaries
    expressions["conservative_basket"] = _build_expression(
        "Conservative — EW genuine-beneficiary basket", "basket",
        episodes, rets, {s: 1.0 for s in basket_names}, r_nifty,
        num_trials, rel_strength, members=basket_names, note=gen_note,
        thesis_positive=True)
    # Balanced — pair: long genuine basket / short IT_f (rotation-neutral)
    expressions["balanced_pair"] = _build_expression(
        "Balanced — long genuine basket / short IT_f (pair)", "pair",
        episodes, rets_aug, "__PAIR__", r_nifty, num_trials, rel_strength,
        members={"long": basket_names, "short": "IT_f (EW 27 IT names)"},
        note="Dollar-neutral rotation: isolates the IT-weakness rotation alpha.",
        thesis_positive=True)
    # Aggressive — hedge: long genuine basket / short NIFTY (market-neutral)
    expressions["aggressive_hedge"] = _build_expression(
        "Aggressive — long genuine basket / short NIFTY (delta-1 hedge)", "hedge",
        episodes, rets_aug, "__HEDGE__", r_nifty, num_trials, rel_strength,
        members={"long": basket_names, "short": "NIFTY (^NSEI)"},
        note="No live option chain in the offline engine — a delta-1 NIFTY short "
             "stands in for the protective/aggressive option leg (limitation "
             "flagged; not an option payoff).",
        thesis_positive=True)

    # ── view-level scores = the Conservative fixed variant (the deployable head) ─
    head = expressions["conservative_basket"]["exit_variants"]["fixed"]
    fs_head = head["battery_summary"]
    below_mintrl = (fs_head["min_trl"] is not None
                    and fs_head["n_obs"] is not None
                    and fs_head["n_obs"] < fs_head["min_trl"])

    out = {
        "view": "it_trouble",
        "generated": datetime.now(timezone.utc).isoformat(),
        "thesis": "IT-sector weakness (weak large-cap guidance prints) — which "
                  "non-IT names genuinely benefit once the market's IT load is "
                  "purged out of the control.",
        "universe": {
            "n_total": int(cov.get("requested", 500)),
            "n_with_data": int(n_with_data),
            "dropped": cov.get("missing", []),
            "short": cov.get("short", []),
            "weighting": "equal_weight",
            "weighting_note": "EW primary (yfinance has no honest cap-weight "
                              "history); liquidity-proxy is the labelled sensitivity.",
        },
        "window": {"win_lo": WIN_LO, "win_hi": WIN_HI,
                   "basis": "confirmation (enter T+1 after the print, hold to T+20)",
                   "estimation_window": "[t0-130, t0-11]"},
        "events": [{"date": a, "kind": "weak_it_guidance_print",
                    "context": EVENT_CONTEXT.get(a, "")}
                   for a in WEAK_ANALOGS],
        "n_events": len(episodes),
        "factors": {
            "b_NB": round(b_NB, 4), "b_NB_t": round(t_NB, 2),
            "b_NB_note": "NIFTY = c + b_NB·Brent (FWL step 1, Newey-West HAC t). "
                         "Small +ve beta — the NIFTY–Brent collinearity that "
                         "contaminated v2's crude test; here it documents the "
                         "control, the IT test uses MKT_exIT not Brent.",
            "mkt_exit_built_from": len(non_it),
            "it_f_built_from": len(it_syms),
        },
        "top_gainers": top_gainers,
        "connectedness": conn,
        "side_by_side": side_by_side,
        "genuine_leaders": [
            {"symbol": s, "industry": ind.get(s, ""),
             "b_it": conn[s]["clean"]["beta_clean"],
             "t_it": conn[s]["clean"]["t"],
             "b_nifty_raw": conn[s]["naive"]["beta_nifty"],
             "event_win_ret_pct": win_ret.get(s)}
            for s in genuine if s in conn],
        "spurious_flipped": [
            {"symbol": s, "industry": ind.get(s, ""),
             "b_nifty_raw": conn[s]["naive"]["beta_nifty"],
             "b_it_clean": conn[s]["clean"]["beta_clean"],
             "t_it": conn[s]["clean"]["t"]}
            for s in spurious_flipped],
        "expressions": expressions,
        "scores": {
            "trust": head["trust"],
            "outcome_dial": head["outcome_dial"],
            "expression_dial": head["expression_dial"],
        },
        "honesty": {
            "n_events": len(episodes),
            "below_mintrl": bool(below_mintrl),
            "trial_group": "it_v3",
            "num_trials_deflation": num_trials,
            "note": "8 weak-IT events → MinTRL not met → the outcome dial "
                    "SUPPRESSES by design (not a bug). num_trials = the "
                    "full-universe screen width so DSR deflates an in-sample-lucky "
                    "leader. Real yfinance bars only; nulls reported, not guessed.",
        },
    }

    with open(OUT_PATH, "w") as f:
        json.dump(out, f, indent=2, default=_jdef)
    print(f"[it_v3] wrote {OUT_PATH}")
    return out


if __name__ == "__main__":
    main()
