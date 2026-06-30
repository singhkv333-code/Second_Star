"""Rewire the 3 live View-Markets views (IT / Monsoon / Crude) to the v3
full-universe, clean-factor research outputs.

For each view we map the v3 JSON's three tiers (conservative / balanced /
aggressive) onto the existing ViewExpression rows BY TIER (newest row of that
tier wins — i.e. the v2 refined rows), UPDATE them in place, and DELETE the
stale leftover rows so each view ends with exactly the three v3 expressions.

Per updated expression we rewrite ``config`` to carry:
  * ``config.scores.backtest``       — the v3 default-exit metrics (router reads this)
  * ``config.scores.clean_factors``  — endogeneity-corrected betas + b_NB (NEW)
  * ``config.scores.nifty_comparison`` + ``config.nifty_comparison`` (NEW, served)
  * ``config.scores.exit_variants``  + ``config.exit_variants``   (NEW, the 3 exits + default)
  * ``structure.exit_options`` / ``structure.default_exit`` — user-configurable params
and refresh ``rationale`` / ``risk_profile`` / ``historical_strength`` to the
full-universe clean-factor methodology. ``workflow_id`` is PRESERVED (no order
placed, no workflow rebuilt, nothing activated). View-level confidence dials are
refreshed from the representative expression's default exit via
``confidence.persist_confidence``. The whole thing is one committed txn.

Run:  .venv/bin/python scripts/strategy_research/v3/rewire_v3.py
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from backend.database import SessionLocal
from backend.models import ExpressionKind, MarketView, ViewExpression
from backend.view_markets.confidence import (
    DialScore,
    TwoDialScore,
    persist_confidence,
)

HERE = os.path.dirname(__file__)
OUT = os.path.join(HERE, "_out")

IT_VIEW = "4f40f896-0953-4d66-bf6f-1932667b531e"
MONSOON_VIEW = "81809245-feeb-4ead-9f35-eb8166757cb7"
CRUDE_VIEW = "19f04e99-b704-4166-b99a-697049885d44"

DISCLAIMER = (
    "This is analysis, not financial advice. Pivot registers the trigger; "
    "you confirm and place each order in your broker app."
)

# Order in which to break ties when choosing the default exit variant.
_EXIT_PRIORITY = ["fixed", "manual", "target"]


# ── small shape-tolerant getters ─────────────────────────────────────────────
def _g(d, *path, default=None):
    cur = d
    for k in path:
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return cur


def _members_long(expr: dict) -> list[str]:
    m = expr.get("members")
    if isinstance(m, dict):
        return list(m.get("long") or [])
    if isinstance(m, list):
        return list(m)
    return []


def _short_leg(expr: dict) -> str | None:
    m = expr.get("members")
    if isinstance(m, dict):
        return m.get("short")
    return None


def _variant_metrics(v: dict) -> dict:
    """Flatten one exit variant into a compact metrics summary."""
    trust = v.get("trust") or {}
    fs = _g(trust, "metrics", "forward_stats", default={}) or {}
    nc = v.get("nifty_comparison") or {}
    od = v.get("outcome_dial") or {}
    ed = v.get("expression_dial") or {}
    return {
        "mode": v.get("mode") or _g(v, "params", "mode") or "fixed",
        "params": v.get("params") or {},
        "trust_verdict": trust.get("verdict"),
        "trust_label": trust.get("label"),
        "trust_confidence": trust.get("confidence"),
        "total_return_pct": _g(trust, "metrics", "total_return_pct",
                               default=nc.get("strategy_total_pct")),
        "max_drawdown_pct": _g(trust, "metrics", "max_drawdown_pct"),
        "psr": fs.get("psr"),
        "deflated_sharpe": fs.get("deflated_sharpe"),
        "min_trl": fs.get("min_trl"),
        "n_obs": fs.get("n_obs"),
        "num_trials": fs.get("num_trials"),
        "outcome_dial": od,
        "expression_dial": ed,
        "nifty_comparison": nc,
        "caar_significance": v.get("caar_significance") or {},
    }


def _choose_default_variant(variants: dict) -> str:
    """Pick the variant with the best un-suppressed expression dial; tie-break by
    the priority order. Falls back to the first priority key present."""
    def key(name: str):
        v = variants[name]
        ed = v.get("expression_dial") or {}
        od = v.get("outcome_dial") or {}
        sup = bool(ed.get("suppressed", ed.get("score") is None))
        es = ed.get("score") if ed.get("score") is not None else -1
        os_ = od.get("score") if od.get("score") is not None else -1
        prio = _EXIT_PRIORITY.index(name) if name in _EXIT_PRIORITY else 99
        # not-suppressed first, then higher expr score, then outcome, then prio
        return (0 if not sup else 1, -es, -os_, prio)

    return sorted(variants.keys(), key=key)[0]


def _dialscore_from(d: dict) -> DialScore:
    return DialScore(
        dimension=d.get("dimension", "outcome"),
        score=d.get("score"),
        letter=d.get("letter"),
        suppressed=bool(d.get("suppressed", d.get("score") is None)),
        verdict=d.get("verdict"),
        components=d.get("components") or {},
        rationale=d.get("rationale") or "",
    )


def _grade_str(od: dict, ed: dict) -> str | None:
    """Letter for the router's best-expression projection: prefer the
    un-suppressed expression letter, else the outcome letter."""
    if ed.get("letter"):
        return ed["letter"]
    if od.get("letter"):
        return od["letter"]
    return None


# ── per-view clean-factor block builders ─────────────────────────────────────
def _clean_factors_it(d: dict) -> dict:
    f = d["factors"]
    return {
        "method": "FWL orthogonalized market control (MKT_exIT) + IT_f factor; Newey-West HAC t",
        "control_factor": "MKT_exIT (NIFTY-500 EW market with the 27 IT names purged)",
        "thesis_factor": "IT_f (EW 27 IT names)",
        "test": "r_i = a + b_mkt·MKT_exIT + b_it·IT_f ; genuine gate b_it<0 & |HAC t|>=2",
        "b_NB": f.get("b_NB"),
        "b_NB_t": f.get("b_NB_t"),
        "b_NB_note": f.get("b_NB_note"),
        "naive_vs_clean_flipped": len(d.get("spurious_flipped") or []),
        "n_leaders_screened": len(d.get("side_by_side") or []),
        "genuine_leaders": d.get("genuine_leaders") or [],
    }


def _clean_factors_monsoon(d: dict) -> dict:
    f = d["factors"]
    return {
        "method": "FWL orthogonalized market control (MKT_exMonsoon) + RURAL_f factor; HAC t",
        "control_factor": "MKT_exMonsoon (market with Auto + FMCG sectors purged)",
        "thesis_factor": "RURAL_f (rural-demand basket)",
        "test": "r_i = a + b_mkt·MKT_exMonsoon + b_rain·RURAL_f ; genuine gate b_rain>0 & |t|>=2",
        "b_NB": f.get("b_NB"),
        "b_NB_t": f.get("b_NB_t"),
        "naive_vs_clean_flipped": d.get("n_flipped"),
        "genuine_independent": d.get("genuine_independent") or [],
        "genuine_mechanical_in_factor": d.get("genuine_mechanical_in_factor"),
        "mechanical_loading_note": d.get("mechanical_loading_note"),
        "spurious_note": d.get("spurious_note"),
    }


def _clean_factors_crude(d: dict) -> dict:
    f = d["factors"]
    flipped = [r for r in (d.get("side_by_side") or []) if r.get("flipped")]
    return {
        "method": "FWL orthogonalized market control (MKT_perpBrent); per-name b_brent; HAC t",
        "control_factor": "MKT_⊥Brent (NIFTY residual orthogonal to Brent)",
        "test": "r_i = a + b_mkt·MKT_⊥Brent + b_brent·Brent ; genuine crude-DOWN gate b_brent<0 & t<=-2",
        "b_NB": f.get("b_NB"),
        "b_NB_t": f.get("b_NB_t"),
        "b_NB_ewmkt": f.get("b_NB_ewmkt"),
        "b_NB_ewmkt_t": f.get("b_NB_ewmkt_t"),
        "interpretation": f.get("interpretation"),
        "naive_vs_clean_flipped": len(flipped),
        "genuine_beneficiaries": d.get("genuine_beneficiaries") or [],
        "paints_check": d.get("paints_check") or {},
        "tyres_check": d.get("tyres_check") or {},
        "side_by_side_flips": flipped[:12],
    }


# ── instruments from members ─────────────────────────────────────────────────
def _instruments(long_members: list[str], short_leg: str | None) -> list[dict]:
    out: list[dict] = []
    for sym in long_members:
        out.append({
            "symbol": sym,
            "exchange": "NSE",
            "segment": "EQ",
            "instrument_type": "equity",
            "role": "long",
            "tradeable": True,
        })
    if short_leg:
        out.append({
            "symbol": short_leg,
            "exchange": "NSE",
            "segment": "INDEX" if "NIFTY" in short_leg.upper() or "NSEI" in short_leg.upper() else "EQ",
            "instrument_type": "index" if ("NIFTY" in short_leg.upper() or "NSEI" in short_leg.upper()) else "factor_or_equity",
            "role": "short",
            "tradeable": True,
            "note": "Short leg — deploy as index future / SSF / inverse exposure. Register-not-execute.",
        })
    return out


# ── build the new config for one expression ──────────────────────────────────
def build_config(view_key: str, expr: dict, clean_factors: dict) -> tuple[dict, dict, dict]:
    """Return (config, default_outcome_dial, default_expression_dial)."""
    variants = expr["exit_variants"]
    default = _choose_default_variant(variants)
    dv = variants[default]
    dvm = _variant_metrics(dv)
    nc = dvm["nifty_comparison"]
    od = dvm["outcome_dial"]
    ed = dvm["expression_dial"]

    long_members = _members_long(expr)
    short_leg = _short_leg(expr)
    kind = expr.get("kind") or expr.get("engine") or "basket"
    name = expr.get("name") or expr.get("label") or kind

    # compact per-variant summary for config.exit_variants / scores.exit_variants
    exit_variants = {vk: _variant_metrics(vv) for vk, vv in variants.items()}

    # user-configurable exit params, surfaced in structure
    exit_options = {}
    for vk, vv in variants.items():
        params = vv.get("params") or {}
        exit_options[vk] = {
            "mode": vv.get("mode") or vk,
            "hold_bars": params.get("hold_bars"),
            "target_pct": params.get("target_pct"),
            "editable": True,
        }

    mfe = expr.get("mfe") or {}

    backtest = {
        "version": "v3_full_universe_clean_factor",
        "engine": dvm.get("trust_label") and (dv.get("trust") or {}).get("engine") or kind,
        "default_exit": default,
        "grade": _grade_str(od, ed),
        "trust_verdict": dvm["trust_verdict"],
        "trust_label": dvm["trust_label"],
        "trust_conf": dvm["trust_confidence"],
        "total_return_pct": nc.get("strategy_total_pct", dvm["total_return_pct"]),
        "nifty_total_pct": nc.get("nifty_total_pct"),
        "excess_return_pct": nc.get("excess_pct"),
        "nifty_beta": nc.get("nifty_beta"),
        "nifty_beta_t": nc.get("nifty_beta_t"),
        "alpha_ann_pct": nc.get("alpha_ann_pct"),
        "pct_episodes_beat": nc.get("pct_episodes_beat"),
        "n_episodes": nc.get("n_episodes"),
        "max_dd_pct": dvm["max_drawdown_pct"],
        "psr": dvm["psr"],
        "dsr": dvm["deflated_sharpe"],
        "deflated_sharpe": dvm["deflated_sharpe"],
        "min_trl": dvm["min_trl"],
        "n_obs": dvm["n_obs"],
        "num_trials": dvm["num_trials"],
        "min_trl_cleared": (dvm["min_trl"] is not None and dvm["n_obs"] is not None
                            and dvm["n_obs"] >= dvm["min_trl"]),
        "outcome_dial": od,
        "outcome_score": od.get("score"),
        "expression_dial": ed,
        "expression_score": ed.get("score"),
        "caar_significance": dvm["caar_significance"],
        "mfe": mfe,
    }

    scores = {
        "alignment_kind": "event_study",
        "construction_alignment": ed.get("score"),
        "backtest": backtest,
        "clean_factors": clean_factors,
        "nifty_comparison": nc,
        "exit_variants": exit_variants,
    }

    structure: dict = {
        "scheme": "equal_weight",
        "members_long": long_members,
        "short_leg": short_leg,
        "n_names": len(long_members),
        "default_exit": default,
        "exit_options": exit_options,
        "rigor_tier": ("beta_neutral_pair" if kind in ("pair", "hedge") else "long_basket"),
    }

    config = {
        "schema_version": 3,
        "label": f"{name} (v3 full-universe clean-factor)",
        "tier": None,  # set by caller (kept from row)
        "expression_kind": kind,
        "instruments": _instruments(long_members, short_leg),
        "structure": structure,
        "scores": scores,
        # NEW top-level keys (task-literal placement; also mirrored under scores so
        # the existing /api/views projection serves them via the `scores` passthrough)
        "nifty_comparison": nc,
        "exit_variants": exit_variants,
        "warnings": [
            f"Trust verdict caps at '{dvm['trust_verdict']}' — "
            f"{nc.get('n_episodes')} episodes; do not over-size.",
            "Full-universe screen: num_trials deflation applied to DSR (multiple-testing honest).",
            "Register-not-execute: Pivot arms the trigger; you place every order yourself.",
        ],
        "disclaimer": DISCLAIMER,
        "expressability": {
            "symmetric": kind in ("pair", "hedge"),
            "short_mode": ("index_future" if short_leg and "NIFTY" in (short_leg or "").upper()
                           else ("factor_or_ssf" if short_leg else None)),
            "notes": ["Register-not-execute; short leg via index future / SSF where applicable."],
        },
    }
    return config, od, ed


# ── rationale / risk / historical_strength refresh ──────────────────────────
def _refresh_texts(expr: dict, config: dict, clean_factors: dict) -> tuple[str, str, str]:
    bt = config["scores"]["backtest"]
    nc = config["nifty_comparison"]
    name = expr.get("name") or expr.get("label") or ""
    b_nb = clean_factors.get("b_NB")
    b_nb_t = clean_factors.get("b_NB_t")
    flipped = clean_factors.get("naive_vs_clean_flipped")
    long_members = _members_long(expr)

    rationale = (
        f"v3 full-universe clean-factor build. {name}. "
        f"Endogeneity correction: b_NB={b_nb} (HAC t={b_nb_t}) documents the NIFTY-commodity "
        f"collinearity that biased the naive v2 lens; the genuine gate uses the orthogonalized "
        f"market control ({clean_factors.get('control_factor')}). "
        f"The naive NIFTY-only test flipped on {flipped} leaders — those are spurious "
        f"(market-beta, not a thesis edge). Long leg = clean-gate beneficiaries "
        f"{', '.join(s.replace('.NS','') for s in long_members[:6])}."
    )
    risk_profile = (
        f"Trust verdict '{bt.get('trust_verdict')}' (conf {bt.get('trust_conf')}); "
        f"NIFTY-beta {nc.get('nifty_beta')} over {nc.get('n_episodes')} episodes, "
        f"maxDD {bt.get('max_dd_pct')}%. Default exit '{bt.get('default_exit')}'. "
        f"Register-not-execute; short legs (if any) are index-future/SSF proxies — "
        f"basis/roll not modelled. Expression dial "
        f"{'SUPPRESSED (N<MinTRL)' if bt.get('expression_score') is None else bt.get('grade')}."
    )
    historical_strength = (
        f"{nc.get('n_episodes')} episodes. Strategy {nc.get('strategy_total_pct')}% vs "
        f"NIFTY {nc.get('nifty_total_pct')}% (excess {nc.get('excess_pct')}%), "
        f"beats NIFTY in {nc.get('pct_episodes_beat')}% of episodes. "
        f"PSR={bt.get('psr')}, DSR={bt.get('dsr')}, MinTRL={bt.get('min_trl')} "
        f"vs n_obs={bt.get('n_obs')} ({'cleared' if bt.get('min_trl_cleared') else 'NOT cleared'}); "
        f"num_trials={bt.get('num_trials')} deflation. Outcome dial {bt.get('outcome_score')}, "
        f"expression dial {bt.get('expression_score')}. Honest ceiling: '{bt.get('trust_verdict')}'."
    )
    return rationale, risk_profile, historical_strength


# ── per-view orchestration ───────────────────────────────────────────────────
KIND_MAP = {
    "basket": ExpressionKind.basket,
    "pair": ExpressionKind.pair,
    "hedge": ExpressionKind.hedge,
    "option_strategy": ExpressionKind.option_strategy,
    "concentrated": ExpressionKind.option_strategy,  # synth option/hedge at deploy
    "leveraged": ExpressionKind.multi_asset,          # labelled 1.5x proxy
    "multi_asset": ExpressionKind.multi_asset,
}

# tier order for representative dump
TIER_ORDER = {"conservative": 0, "balanced": 1, "aggressive": 2}


def _v3_tier_map(view_key: str, d: dict) -> dict:
    """Return {tier: expr_dict} for the three v3 tiers."""
    ex = d["expressions"]
    if view_key == "it":
        return {
            "conservative": ex["conservative_basket"],
            "balanced": ex["balanced_pair"],
            "aggressive": ex["aggressive_hedge"],
        }
    if view_key == "monsoon":
        return {
            "conservative": ex["conservative"],
            "balanced": ex["balanced"],
            "aggressive": ex["aggressive"],
        }
    # crude — list ordered cons/bal/aggr
    return {"conservative": ex[0], "balanced": ex[1], "aggressive": ex[2]}


def process_view(db, view_key: str, view_id: str, clean_fn) -> dict:
    d = json.load(open(os.path.join(OUT, f"{view_key}_v3.json")))
    clean_factors = clean_fn(d)
    tier_exprs = _v3_tier_map(view_key, d)

    view = db.get(MarketView, view_id)
    assert view is not None, f"view {view_id} missing"

    rows = (
        db.query(ViewExpression)
        .filter(ViewExpression.view_id == view_id)
        .all()
    )
    by_tier: dict[str, list[ViewExpression]] = {}
    for r in rows:
        by_tier.setdefault(str(getattr(r.tier, "value", r.tier)), []).append(r)
    # newest first within tier
    for t in by_tier:
        by_tier[t].sort(key=lambda r: r.created_at or "", reverse=True)

    chosen_ids = set()
    updated = []
    rep = None  # (tier, od, ed) representative for view confidence
    rep_key = None

    for tier, expr in tier_exprs.items():
        config, od, ed = build_config(view_key, expr, clean_factors)
        config["tier"] = tier
        candidates = by_tier.get(tier, [])
        row = candidates[0] if candidates else None
        if row is None:
            row = ViewExpression(
                view_id=view_id,
                tier=tier,
                expression_kind=KIND_MAP.get(config["expression_kind"], ExpressionKind.basket),
                config=config,
            )
            db.add(row)
            db.flush()
        else:
            row.config = config
            row.expression_kind = KIND_MAP.get(config["expression_kind"], row.expression_kind)
            # keep row.tier, row.workflow_id, row.backtest_run_id untouched
        rationale, risk, hist = _refresh_texts(expr, config, clean_factors)
        row.rationale = rationale
        row.risk_profile = risk
        row.historical_strength = hist
        row.time_horizon = "per-episode event window (concatenated); see exit_options for hold/target."
        db.flush()
        chosen_ids.add(str(row.id))

        bt = config["scores"]["backtest"]
        updated.append({
            "tier": tier,
            "expression_id": str(row.id),
            "workflow_id": str(row.workflow_id) if row.workflow_id else None,
            "kind": config["expression_kind"],
            "grade": bt.get("grade"),
            "verdict": bt.get("trust_verdict"),
            "outcome": bt.get("outcome_score"),
            "expression": bt.get("expression_score"),
            "excess_pct": bt.get("excess_return_pct"),
            "nifty_beta": bt.get("nifty_beta"),
            "default_exit": bt.get("default_exit"),
        })

        # representative = best (un-suppressed expr score, then outcome score)
        score_key = (
            0 if ed.get("score") is not None else 1,
            -(ed.get("score") or -1),
            -(od.get("score") or -1),
        )
        if rep is None or score_key < rep_key:
            rep = (tier, od, ed)
            rep_key = score_key

    # delete stale rows not chosen
    deleted = []
    for r in rows:
        if str(r.id) not in chosen_ids:
            deleted.append(str(r.id))
            db.delete(r)
    db.flush()

    # refresh view-level confidence from the representative expression
    _, od, ed = rep
    two = TwoDialScore(
        outcome=_dialscore_from(od),
        expression=_dialscore_from(ed),
        flags=tuple(d.get("honesty", {}).get("flags", []) or []),
    )
    persist_confidence(db, view_id, two)

    return {
        "view_id": view_id,
        "b_NB": clean_factors.get("b_NB"),
        "b_NB_t": clean_factors.get("b_NB_t"),
        "updated": sorted(updated, key=lambda u: TIER_ORDER[u["tier"]]),
        "deleted": deleted,
        "representative_tier": rep[0],
        "conf_outcome": od.get("score"),
        "conf_outcome_letter": od.get("letter"),
        "conf_expression": ed.get("score"),
        "conf_expression_letter": ed.get("letter"),
        "conf_expr_suppressed": ed.get("suppressed", ed.get("score") is None),
    }


def main():
    print("=" * 78)
    print("  REWIRE v3 — full-universe clean-factor research → live ViewExpression rows")
    print("  register-not-execute | workflow_id preserved | nothing activated/placed")
    print("=" * 78)
    out = {}
    with SessionLocal() as db:
        out["IT"] = process_view(db, "it", IT_VIEW, _clean_factors_it)
        out["Monsoon"] = process_view(db, "monsoon", MONSOON_VIEW, _clean_factors_monsoon)
        out["Crude"] = process_view(db, "crude", CRUDE_VIEW, _clean_factors_crude)
        db.commit()
        print("\nDB commit OK.\n")

    for view, r in out.items():
        print("=" * 78)
        print(f"  {view}  (view {r['view_id']})  b_NB={r['b_NB']} (t={r['b_NB_t']})")
        print(f"    view-confidence: outcome {r['conf_outcome_letter']}/{r['conf_outcome']} · "
              f"expression {'SUPPRESSED' if r['conf_expr_suppressed'] else str(r['conf_expression_letter'])+'/'+str(r['conf_expression'])} "
              f"(from {r['representative_tier']})")
        for u in r["updated"]:
            es = "SUPPR" if u["expression"] is None else u["expression"]
            print(f"    [{u['tier']:<12}] {u['expression_id']} kind={u['kind']:<14} "
                  f"grade={u['grade']} verdict={u['verdict']:<9} "
                  f"out={u['outcome']} expr={es} excess={u['excess_pct']}% beta={u['nifty_beta']} "
                  f"exit={u['default_exit']} wf={u['workflow_id']}")
        if r["deleted"]:
            print(f"    deleted stale rows: {r['deleted']}")

    # dump machine-readable summary
    with open(os.path.join(OUT, "rewire_v3_summary.json"), "w") as f:
        json.dump(out, f, indent=2, default=str)
    print("\nSummary → scripts/strategy_research/v3/_out/rewire_v3_summary.json")


if __name__ == "__main__":
    main()
