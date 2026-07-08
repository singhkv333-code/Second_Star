"""v3 Trust Battery + grading — wraps the EXACT v2 calls (no re-derivation).

forward_stats_block / monte_carlo_robustness / sub_period_robustness /
trust_verdict + the two-dial confidence scorer, plus:
  * caar_significance — classical + BMP-style standardized-CAR + non-parametric
    sign test on per-event CARs (pure numpy), combined the confidence.py way.
  * trial_deflate     — re-run forward_stats with num_trials = the full-universe
    screen width (the honest multiple-testing correction, §6).
  * trust_block       — assemble the FROZEN TRUST_BLOCK_KEYS envelope so
    rewire_v3 / the card consume it byte-compatibly with deployment/backtest.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from backend.services.forward_stats import forward_stats_block, max_drawdown_pct
from backend.services.backtest.validation.monte_carlo import monte_carlo_robustness
from backend.services.backtest.validation.sub_periods import sub_period_robustness
from backend.services.backtest.validation.verdict import trust_verdict
from backend.view_markets import confidence as conf
from backend.view_markets.deployment.backtest import (
    TRUST_BLOCK_KEYS, TRUST_METRICS_KEYS, ENGINE_BY_KIND,  # noqa: F401
)

_CAAR_SCALE = 20.0


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def two_sided_p(t: float | None) -> float | None:
    if t is None or not np.isfinite(t):
        return None
    return 2.0 * (1.0 - 0.5 * (1.0 + math.erf(abs(t) / math.sqrt(2.0))))


# ── battery on a concatenated equity curve ────────────────────────────────────
def run_battery(equity: list[float], daily_rets: list[float], n_episodes: int,
                num_trials: int) -> dict:
    """fs / mc / sp / verdict on the concatenated curve (mirrors
    _crude_bt_common._battery_on / _it_bt_common.run_battery)."""
    fs = forward_stats_block(equity, num_trials=num_trials)
    mc = monte_carlo_robustness(daily_rets, drawdown_tolerance_pct=-20.0)
    sp = sub_period_robustness(equity, n_periods=4)
    total = (equity[-1] / equity[0] - 1.0) * 100.0 if equity and equity[0] else None
    mdd = max_drawdown_pct(equity)
    verdict = trust_verdict(forward_stats=fs, monte_carlo=mc, sub_periods=sp,
                            total_return_pct=total, n_trades=n_episodes)
    return {"forward_stats": fs, "monte_carlo": mc, "sub_periods": sp,
            "verdict": verdict, "total_return_pct": round(total, 2) if total else None,
            "max_drawdown_pct": round(mdd, 2) if mdd is not None else None,
            "n_obs": fs["n_obs"]}


def trial_deflate(equity: list[float], daily_rets: list[float], n_episodes: int,
                  *, num_trials: int) -> dict:
    """Re-run the battery with ``num_trials`` = the full-universe screen width so
    DSR deflates an in-sample-lucky leader (§6). This IS the honest answer to
    'the full-universe scan inflates the best name'."""
    return run_battery(equity, daily_rets, n_episodes, num_trials)


# ── event-study CAAR significance (classical + BMP + non-parametric) ──────────
def caar_significance(per_event_cars: list[float]) -> dict:
    """Cross-event significance on per-episode CARs (events non-overlapping by
    construction, so classical is valid here).
      * classical : mean / (std/√N)            (crude_geo market_model_car path)
      * bmp       : standardized-CAR cross-event t (each CAR / its own est.-window
                    sigma proxied by the cross-sectional std, BMP-style)
      * nonparam  : sign test vs 0 (binomial → normal approx)
    Combined p uses the confidence.py rule: agreeing-min when both reject at .10,
    else conservative-max."""
    arr = np.array([c for c in per_event_cars if np.isfinite(c)], float)
    n = len(arr)
    if n < 2:
        return {"caar": None, "classical_t": None, "classical_p": None,
                "bmp_t": None, "bmp_p": None, "nonparam_p": None,
                "combined_p": None, "n": n}
    mean = float(arr.mean())
    sd = float(arr.std(ddof=1))
    classical_t = mean / (sd / math.sqrt(n)) if sd > 0 else float("nan")
    # BMP-style: standardize each CAR by cross-event sigma, then t on the SCARs
    scar = arr / sd if sd > 0 else arr
    bmp_t = float(scar.mean() / (scar.std(ddof=1) / math.sqrt(n))) \
        if scar.std(ddof=1) > 0 else float("nan")
    # non-parametric sign test
    pos = int((arr > 0).sum())
    z = (pos - n / 2.0) / (math.sqrt(n) / 2.0) if n > 0 else 0.0
    nonparam_p = two_sided_p(z)
    classical_p = two_sided_p(classical_t)
    bmp_p = two_sided_p(bmp_t)
    ps = [p for p in (bmp_p, nonparam_p) if p is not None]
    both_agree = all(p < 0.10 for p in ps) if ps else False
    combined = (min(ps) if both_agree else max(ps)) if ps else None
    return {"caar": round(mean * 100, 3), "classical_t": round(classical_t, 2),
            "classical_p": round(classical_p, 4) if classical_p else None,
            "bmp_t": round(bmp_t, 2), "bmp_p": round(bmp_p, 4) if bmp_p else None,
            "nonparam_p": round(nonparam_p, 4) if nonparam_p else None,
            "combined_p": round(combined, 4) if combined else None,
            "n": n, "both_agree": both_agree}


def _caar_alignment(caar_pct: float | None) -> float | None:
    if caar_pct is None:
        return None
    return max(0.0, min(1.0, 0.5 + (abs(caar_pct) / 100.0) * _CAAR_SCALE))


# ── two-dial alignment (real confidence module, suppression gate enforced) ────
def two_dials(*, hit_rate, relationship_strength, sample_n, verdict,
              caar_alignment, significance_p, cost_survival, deflated_sharpe,
              n_obs, min_trl, payoff_pop=None):
    """Score both dials with the REAL backend.view_markets.confidence module.
    SUPPRESSES below MinTRL / insufficient_data. Returns (outcome, expression)
    DialScore objects."""
    outcome = conf.score_outcome_dial(
        hit_rate=hit_rate, relationship_strength=relationship_strength,
        sample_n=sample_n, min_trl=None, verdict=verdict)
    expr = conf.score_expression_dial(
        caar_bhar_alignment=caar_alignment, significance_p=significance_p,
        cost_survival=cost_survival, payoff_pop=payoff_pop, verdict=verdict,
        deflated_sharpe=deflated_sharpe, n_obs=n_obs, min_trl=min_trl)
    return outcome, expr


def dial_to_dict(d) -> dict:
    return {"dimension": d.dimension, "score": d.score, "letter": d.letter,
            "suppressed": d.suppressed, "verdict": d.verdict,
            "rationale": d.rationale}


def cost_survival(gross_pct: float | None, net_pct: float | None) -> float | None:
    if gross_pct is None or gross_pct == 0:
        return None
    if gross_pct <= 0:
        return 0.0
    return max(0.0, min(1.0, (net_pct or 0.0) / gross_pct))


# ── FROZEN TRUST_BLOCK_KEYS envelope ──────────────────────────────────────────
def trust_block(battery: dict, *, engine: str, alignment: dict | None,
                nifty_comparison: dict | None, degraded: bool = False,
                data_note: str | None = None, backtest_run_id=None) -> dict:
    """Assemble the FROZEN config['scores']['trust'] envelope, byte-compatible
    with deployment/backtest.TRUST_BLOCK_KEYS / TRUST_METRICS_KEYS."""
    v = battery["verdict"]
    fs = battery["forward_stats"]
    bench_ret = nifty_comparison.get("nifty_total_pct") if nifty_comparison else None
    metrics = {
        "total_return_pct": battery.get("total_return_pct"),
        "max_drawdown_pct": battery.get("max_drawdown_pct"),
        "n_trades": battery.get("n_obs"),
        "benchmark_return_pct": bench_ret,
        "forward_stats": fs,
        "monte_carlo": battery.get("monte_carlo"),
        "sub_periods": battery.get("sub_periods"),
    }
    block = {
        "verdict": v["verdict"],
        "label": v["label"],
        "confidence": v.get("confidence"),
        "rationale": v.get("rationale"),
        "flags": v.get("flags"),
        "engine": engine,
        "backtest_run_id": backtest_run_id,
        "metrics": {k: metrics.get(k) for k in TRUST_METRICS_KEYS},
        "alignment": alignment,
        "degraded": degraded,
        "data_note": data_note,
        "as_of": _now_iso(),
    }
    return {k: block.get(k) for k in TRUST_BLOCK_KEYS}
