"""Trust verdict — synthesise the rigor battery into one honest, actionable call.

The battery (PSR · MinTRL · DSR+trials · Monte-Carlo · sub-periods) is only as
useful as the answer it gives a non-statistician: *should I believe this curve?*
This rolls the blocks into a single ordered verdict + a plain-English rationale +
machine-readable risk flags, mirroring the paper-scorecard verdict ladder so a
backtest and its eventual live track record are judged on the same axis.

Primary verdict (statistical-confidence axis, first match wins):
  * ``insufficient_data`` — too few observations / trades to judge.
  * ``no_edge``           — Sharpe not even probably positive (PSR < 0.6) or a loss.
  * ``promising``         — PSR ≥ 0.95 AND DSR ≥ 0.95 AND track record ≥ MinTRL.
  * ``unproven``          — something in between (edge possible, not established).

Risk flags (independent, surfaced alongside — a strategy can be statistically
credible AND fragile):
  * ``selection_bias``       — many variants tried this session (DSR-deflated).
  * ``return_concentrated``  — most of the return came from one sub-period.
  * ``drawdown_risk``        — deep 5%-worst bootstrap drawdown.
  * ``loss_likely``          — >50% of bootstrap paths end below water.

Pure; reads only the already-computed metric blocks.
"""
from __future__ import annotations

from typing import Optional

_MIN_OBS = 20          # below this the track record is too short to judge (matches scorecards)
_MIN_TRADES = 3
_PSR_EDGE = 0.60       # below this the Sharpe isn't even probably positive
_PSR_STRONG = 0.95
_DSR_STRONG = 0.95
_CONC_FRAGILE = 0.60   # >60% of the return from a single sub-period
_DD_DEEP = -30.0       # 5%-worst bootstrap drawdown deeper than this
_PLOSS_HIGH = 0.50
_TRIALS_MANY = 5


def _num(x: object) -> Optional[float]:
    return float(x) if isinstance(x, (int, float)) else None


def trust_verdict(
    *,
    forward_stats: Optional[dict],
    monte_carlo: Optional[dict],
    sub_periods: Optional[dict],
    total_return_pct: Optional[float],
    n_trades: int,
) -> dict:
    fs = forward_stats or {}
    n_obs = int(fs.get("n_obs") or 0)
    psr = _num(fs.get("psr"))
    dsr = _num(fs.get("deflated_sharpe"))
    min_trl = _num(fs.get("min_trl"))
    num_trials = int(fs.get("num_trials") or 1)

    # ---- Risk flags (independent of the primary verdict) ----
    flags: list[str] = []
    conc = _num((sub_periods or {}).get("concentration"))
    if conc is not None and conc > _CONC_FRAGILE:
        flags.append("return_concentrated")
    dd95 = _num((monte_carlo or {}).get("dd_p95_severity_pct"))
    if dd95 is not None and dd95 < _DD_DEEP:
        flags.append("drawdown_risk")
    ploss = _num((monte_carlo or {}).get("prob_loss"))
    if ploss is not None and ploss > _PLOSS_HIGH:
        flags.append("loss_likely")
    if num_trials >= _TRIALS_MANY:
        flags.append("selection_bias")

    # ---- Primary verdict ladder ----
    track_ok = (min_trl is None) or (min_trl <= n_obs)

    if n_obs < _MIN_OBS or n_trades < _MIN_TRADES:
        verdict = "insufficient_data"
        label = "Insufficient data"
        rationale = (
            f"Only {n_obs} return observation(s) across {n_trades} trade(s) — too "
            f"little to judge. Lengthen the window or loosen the entry."
        )
        confidence = 0
    elif (total_return_pct is not None and total_return_pct <= 0) or psr is None or psr < _PSR_EDGE:
        verdict = "no_edge"
        label = "No demonstrable edge"
        psr_txt = f"{psr:.0%}" if psr is not None else "n/a"
        rationale = (
            f"Confidence the Sharpe is genuinely positive (PSR) is only {psr_txt} — "
            f"indistinguishable from no skill after costs."
        )
        confidence = round((psr or 0.0) * 100)
    elif psr >= _PSR_STRONG and dsr is not None and dsr >= _DSR_STRONG and track_ok:
        verdict = "promising"
        label = "Statistically credible"
        rationale = (
            f"PSR {psr:.0%} and deflated-Sharpe {dsr:.0%} after {num_trials} "
            f"variant(s); the track record clears the minimum needed."
        )
        confidence = round(min(psr, dsr) * 100)
    else:
        verdict = "unproven"
        label = "Unproven"
        bits: list[str] = []
        if psr is not None:
            bits.append(f"PSR {psr:.0%}")
        if dsr is not None and num_trials > 1:
            bits.append(f"DSR {dsr:.0%} after {num_trials} variants")
        if min_trl is not None and min_trl > n_obs:
            bits.append(f"needs ~{min_trl:.0f} obs (have {n_obs})")
        rationale = "There may be an edge but it isn't established yet" + (
            ": " + "; ".join(bits) + "." if bits else "."
        )
        confidence = round((dsr if dsr is not None else (psr or 0.0)) * 100)

    if verdict == "promising" and flags:
        label = "Credible — but watch the risks"

    return {
        "verdict": verdict,
        "label": label,
        "confidence": confidence,   # 0–100, the statistical P(edge is real)
        "rationale": rationale,
        "flags": flags,
    }


__all__ = ["trust_verdict"]
