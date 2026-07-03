"""Chain-level option metrics for the workflow DSL (F&O P3).

Every metric reads through the 5s-cached chain service — the watcher
ticks every 60s, so a full tree evaluation costs at most one chain fetch
per (underlying, expiry) regardless of how many option nodes reference
it. NEVER call Kite directly from here.

Metric catalogue (v1 — honest subset; ivp/ivr need the IV-history store
that lands with the backtest phase and are deliberately NOT offered):

  iv_atm             ATM IV (mean of CE/PE solves), vol fraction
  straddle_price     ATM CE mid + PE mid (₹ per unit)
  expected_move_pct  1σ-to-expiry move as % of forward
  pcr_oi             Σ put OI / Σ call OI over the slice
  pcr_volume         Σ put volume / Σ call volume
  max_pain           strike minimizing Σ intrinsic·OI across both sides
  rr_25d             IV(25Δ put) − IV(25Δ call)  (positive = put skew)
  fly_25d            ½(IV25P + IV25C) − IV_ATM   (smile curvature)
  term_slope         IV_ATM(next expiry) − IV_ATM(nearest), vol points
  vrp                IV_ATM − 20d realized vol (premium richness)

``dte``/Greeks have their own entry points (different shapes).
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

OPTION_METRICS = frozenset({
    "iv_atm", "straddle_price", "expected_move_pct", "pcr_oi",
    "pcr_volume", "max_pain", "rr_25d", "fly_25d", "term_slope", "vrp",
})

_EXPIRY_RULES = ("nearest", "next", "monthly")


def _resolve_rule_expiry(
    db: Session, underlying: str, expiry_rule: str,
) -> Optional[str]:
    from backend.market.instrument_master import list_expiries

    expiries = list_expiries(db, underlying)
    if not expiries:
        return None
    rule = (expiry_rule or "nearest").lower()
    if rule == "next":
        return expiries[1]["expiry"] if len(expiries) > 1 else expiries[0]["expiry"]
    if rule == "monthly":
        for e in expiries:
            if e["kind"] == "monthly":
                return e["expiry"]
        return expiries[-1]["expiry"]
    return expiries[0]["expiry"]


def _chain_for(
    db: Session, underlying: str, expiry_rule: str = "nearest",
) -> Optional[dict]:
    from backend.market.option_chain import get_chain

    expiry = _resolve_rule_expiry(db, underlying, expiry_rule)
    if expiry is None:
        return None
    return get_chain(db, underlying, expiry, width=25)


def _atm_iv(chain: dict) -> Optional[float]:
    atm = chain.get("atm_strike")
    row = next((r for r in chain["rows"] if r["strike"] == atm), None)
    if not row:
        return None
    ivs = [
        row[s]["iv"] for s in ("ce", "pe")
        if row.get(s) and row[s].get("iv") is not None
        and row[s].get("iv_status") in ("ok", "wide_spread")
    ]
    return float(sum(ivs) / len(ivs)) if ivs else None


def _delta_strike_iv(chain: dict, side: str, target: float) -> Optional[float]:
    """IV at the strike whose |delta| is nearest ``target`` on ``side``."""
    best_iv, best_dist = None, 9e9
    for row in chain["rows"]:
        q = row.get(side)
        if not q or q.get("delta") is None or q.get("iv") is None:
            continue
        dist = abs(abs(float(q["delta"])) - target)
        if dist < best_dist:
            best_dist, best_iv = dist, float(q["iv"])
    return best_iv


def compute_option_metric(
    db: Session, underlying: str, metric: str, *, expiry_rule: str = "nearest",
) -> Optional[float]:
    """One metric value, or None (→ Kleene UNKNOWN) when unavailable.
    None is honest absence — never a fabricated number."""
    metric = (metric or "").lower()
    if metric not in OPTION_METRICS:
        return None
    underlying = (underlying or "").strip().upper()
    try:
        chain = _chain_for(db, underlying, expiry_rule)
        if chain is None:
            return None

        if metric == "iv_atm":
            return _atm_iv(chain)

        if metric == "straddle_price":
            atm = chain.get("atm_strike")
            row = next((r for r in chain["rows"] if r["strike"] == atm), None)
            if not row or not row.get("ce") or not row.get("pe"):
                return None
            ce, pe = row["ce"].get("mid"), row["pe"].get("mid")
            return float(ce + pe) if ce and pe else None

        if metric == "expected_move_pct":
            em = chain.get("expected_move")
            return float(em["pct"]) if em else None

        if metric in ("pcr_oi", "pcr_volume"):
            key = "oi" if metric == "pcr_oi" else "volume"
            put_total = sum(
                float((r.get("pe") or {}).get(key) or 0) for r in chain["rows"]
            )
            call_total = sum(
                float((r.get("ce") or {}).get(key) or 0) for r in chain["rows"]
            )
            return put_total / call_total if call_total > 0 else None

        if metric == "max_pain":
            strikes = [r["strike"] for r in chain["rows"]]
            best_strike, best_pain = None, None
            for s in strikes:
                pain = 0.0
                for r in chain["rows"]:
                    k = r["strike"]
                    ce_oi = float((r.get("ce") or {}).get("oi") or 0)
                    pe_oi = float((r.get("pe") or {}).get("oi") or 0)
                    pain += max(0.0, s - k) * ce_oi + max(0.0, k - s) * pe_oi
                if best_pain is None or pain < best_pain:
                    best_pain, best_strike = pain, s
            return best_strike

        if metric == "rr_25d":
            put_iv = _delta_strike_iv(chain, "pe", 0.25)
            call_iv = _delta_strike_iv(chain, "ce", 0.25)
            if put_iv is None or call_iv is None:
                return None
            return put_iv - call_iv

        if metric == "fly_25d":
            put_iv = _delta_strike_iv(chain, "pe", 0.25)
            call_iv = _delta_strike_iv(chain, "ce", 0.25)
            atm_iv = _atm_iv(chain)
            if None in (put_iv, call_iv, atm_iv):
                return None
            return 0.5 * (put_iv + call_iv) - atm_iv

        if metric == "term_slope":
            near_iv = _atm_iv(chain)
            far_chain = _chain_for(db, underlying, "next")
            if far_chain is None or far_chain.get("expiry") == chain.get("expiry"):
                return None
            far_iv = _atm_iv(far_chain)
            if near_iv is None or far_iv is None:
                return None
            return far_iv - near_iv

        if metric == "vrp":
            iv = _atm_iv(chain)
            if iv is None:
                return None
            from backend.services.option_strategies import _realized_vol_20d

            rv = _realized_vol_20d(underlying, chain.get("segment") or "")
            return iv - rv if rv else None
    except Exception as exc:
        logger.info("[option-metric] %s %s failed: %s", underlying, metric, exc)
    return None


def compute_option_greek(
    db: Session,
    underlying: str,
    greek: str,
    *,
    option_type: str = "CE",
    strike: Optional[float] = None,
    expiry_rule: str = "nearest",
) -> Optional[float]:
    """A single contract's greek off the live chain (ATM when ``strike``
    is None). Per-UNIT greek, unscaled by lots."""
    greek = (greek or "").lower()
    if greek not in ("delta", "gamma", "theta", "vega"):
        return None
    try:
        chain = _chain_for(db, (underlying or "").strip().upper(), expiry_rule)
        if chain is None:
            return None
        target = float(strike) if strike else float(chain["atm_strike"])
        row = min(chain["rows"], key=lambda r: abs(r["strike"] - target))
        q = row.get("ce" if option_type.upper() == "CE" else "pe")
        if not q or q.get(greek) is None:
            return None
        return float(q[greek])
    except Exception as exc:
        logger.info("[option-greek] %s failed: %s", underlying, exc)
        return None


def compute_dte(
    db: Session, underlying: str, *, expiry_rule: str = "nearest",
) -> Optional[float]:
    """Calendar days to expiry (fractional, intraday clock)."""
    try:
        chain = _chain_for(db, (underlying or "").strip().upper(), expiry_rule)
        if chain is None:
            return None
        return round(float(chain["t_years"]) * 365.0, 4)
    except Exception as exc:
        logger.info("[option-dte] %s failed: %s", underlying, exc)
        return None
