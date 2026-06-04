"""Option strategy template engine (F&O P1).

Declarative templates resolved against the LIVE chain — strikes are
picked by delta / ATM-offset rules with liquidity gating, NEVER
hardcoded. Every resolved strategy carries the retail decision quad
(max loss, max profit, POP, capital) plus payoff curve, breakevens,
net Greeks, a margin estimate and a rule-based Copilot critique.

View → candidates mapping implements the researched suggest-flow
(Samco B.R.O. / Sensibull-wizard pattern): 2-3 risk-tagged candidates
per directional view, conservative defaults, Greeks on demand.

Conventions:
  * net_premium: SIGNED per full strategy (all lots). Negative = debit.
  * payoff: expiry P&L in ₹ for the whole position (lots × lot_size).
  * POP: risk-neutral P(P&L > 0 at expiry) under lognormal forward with
    ATM IV — the market-implied number every serious platform shows.
  * max_loss/max_profit: ``None`` means UNLIMITED (naked exposure) —
    consumers must render the word, not a number.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any, Optional

import numpy as np
from scipy.special import ndtr
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

_RISK_FREE = 0.065
_PAYOFF_POINTS = 61
# Index-margin heuristic for undefined-risk short legs (P1 estimate; a
# SPAN approximation replaces this in P2): premium + this fraction of
# underlying notional per short lot.
_SHORT_MARGIN_NOTIONAL_PCT = 0.15

SEBI_DISCLOSURE = (
    "SEBI study: 9 out of 10 individual F&O traders lose money. "
    "Options can lose their entire premium — or more when sold. "
    "Pivot registers intents only; nothing is executed with a broker."
)


# ── Template registry ────────────────────────────────────────────────


@dataclass(frozen=True)
class LegSpec:
    option_type: str            # CE | PE
    side: str                   # BUY | SELL
    strike_rule: str            # atm | delta | atm_offset
    delta: float = 0.0          # target |delta| for strike_rule="delta"
    offset: int = 0             # strikes from ATM for "atm_offset"


@dataclass(frozen=True)
class StrategyTemplate:
    name: str
    label: str
    views: tuple[str, ...]      # bullish | bearish | neutral | volatile
    risk_tag: str               # conservative | moderate | aggressive
    legs: tuple[LegSpec, ...]
    one_liner: str
    needs_holding: bool = False  # covered_call / protective_put


TEMPLATES: dict[str, StrategyTemplate] = {t.name: t for t in [
    # ── Bullish ──
    StrategyTemplate(
        "long_call", "Long Call", ("bullish",), "aggressive",
        (LegSpec("CE", "BUY", "atm"),),
        "Buy an ATM call — defined risk (premium), uncapped upside.",
    ),
    StrategyTemplate(
        "bull_call_spread", "Bull Call Spread", ("bullish",), "moderate",
        (LegSpec("CE", "BUY", "atm"), LegSpec("CE", "SELL", "delta", 0.25)),
        "Buy ATM call, sell a ~25Δ call — cheaper than a naked call, capped profit.",
    ),
    StrategyTemplate(
        "bull_put_spread", "Bull Put Spread (credit)", ("bullish",), "conservative",
        (LegSpec("PE", "SELL", "delta", 0.30), LegSpec("PE", "BUY", "delta", 0.15)),
        "Collect credit if the market stays up — defined risk, theta works for you.",
    ),
    StrategyTemplate(
        "cash_secured_put", "Cash-Secured Put", ("bullish",), "conservative",
        (LegSpec("PE", "SELL", "delta", 0.30),),
        "Sell a ~30Δ put backed by cash — income now, or buy the dip at a discount.",
    ),
    StrategyTemplate(
        "covered_call", "Covered Call", ("bullish", "neutral"), "conservative",
        (LegSpec("CE", "SELL", "delta", 0.30),),
        "Sell a ~30Δ call against shares you hold — income on a sideways view.",
        needs_holding=True,
    ),
    # ── Bearish ──
    StrategyTemplate(
        "long_put", "Long Put", ("bearish",), "aggressive",
        (LegSpec("PE", "BUY", "atm"),),
        "Buy an ATM put — defined risk (premium), profits as the market falls.",
    ),
    StrategyTemplate(
        "bear_put_spread", "Bear Put Spread", ("bearish",), "moderate",
        (LegSpec("PE", "BUY", "atm"), LegSpec("PE", "SELL", "delta", 0.25)),
        "Buy ATM put, sell a ~25Δ put — cheaper bearish bet, capped profit.",
    ),
    StrategyTemplate(
        "bear_call_spread", "Bear Call Spread (credit)", ("bearish",), "conservative",
        (LegSpec("CE", "SELL", "delta", 0.30), LegSpec("CE", "BUY", "delta", 0.15)),
        "Collect credit if the market stays down — defined risk, time decay helps.",
    ),
    StrategyTemplate(
        "protective_put", "Protective Put", ("bearish", "neutral"), "conservative",
        (LegSpec("PE", "BUY", "delta", 0.30),),
        "Buy a ~30Δ put as insurance on shares you hold.",
        needs_holding=True,
    ),
    # ── Neutral (short vol) ──
    StrategyTemplate(
        "iron_condor", "Iron Condor", ("neutral",), "conservative",
        (
            LegSpec("CE", "SELL", "delta", 0.20),
            LegSpec("PE", "SELL", "delta", 0.20),
            LegSpec("CE", "BUY", "delta", 0.10),
            LegSpec("PE", "BUY", "delta", 0.10),
        ),
        "Sell a strangle, buy wings — income if the market stays in a range, defined risk.",
    ),
    StrategyTemplate(
        "iron_butterfly", "Iron Butterfly", ("neutral",), "moderate",
        (
            LegSpec("CE", "SELL", "atm"),
            LegSpec("PE", "SELL", "atm"),
            LegSpec("CE", "BUY", "delta", 0.10),
            LegSpec("PE", "BUY", "delta", 0.10),
        ),
        "Sell the ATM straddle with protective wings — bigger credit, tighter range.",
    ),
    StrategyTemplate(
        "short_strangle", "Short Strangle", ("neutral",), "aggressive",
        (LegSpec("CE", "SELL", "delta", 0.20), LegSpec("PE", "SELL", "delta", 0.20)),
        "Sell OTM call + put — maximum theta, UNLIMITED risk both sides.",
    ),
    StrategyTemplate(
        "short_straddle", "Short Straddle", ("neutral",), "aggressive",
        (LegSpec("CE", "SELL", "atm"), LegSpec("PE", "SELL", "atm")),
        "Sell the ATM straddle — biggest credit, UNLIMITED risk both sides.",
    ),
    # ── Volatile (long vol) ──
    StrategyTemplate(
        "long_strangle", "Long Strangle", ("volatile",), "conservative",
        (LegSpec("CE", "BUY", "delta", 0.25), LegSpec("PE", "BUY", "delta", 0.25)),
        "Buy OTM call + put — cheaper than a straddle, needs a bigger move.",
    ),
    StrategyTemplate(
        "long_straddle", "Long Straddle", ("volatile",), "moderate",
        (LegSpec("CE", "BUY", "atm"), LegSpec("PE", "BUY", "atm")),
        "Buy ATM call + put — profits from a large move in EITHER direction.",
    ),
]}

# Suggest-flow ladder: view → ordered (conservative → aggressive)
# candidate templates. The first entry is the DEFAULT the card opens on.
VIEW_CANDIDATES: dict[str, tuple[str, ...]] = {
    "bullish": ("bull_put_spread", "bull_call_spread", "long_call"),
    "bearish": ("bear_call_spread", "bear_put_spread", "long_put"),
    "neutral": ("iron_condor", "iron_butterfly", "short_strangle"),
    "volatile": ("long_strangle", "long_straddle"),
}


# ── Strike resolution ────────────────────────────────────────────────


def _side_quote(row: dict, option_type: str) -> Optional[dict]:
    return row.get("ce" if option_type == "CE" else "pe")


def _is_quotable(q: Optional[dict]) -> bool:
    return bool(q) and q.get("iv_status") in ("ok", "wide_spread") and q.get("mid", 0) > 0


def _resolve_leg_strike(
    rows: list[dict], atm_strike: float, spec: LegSpec,
) -> Optional[dict]:
    """Pick the chain row for a leg spec, walking to the nearest
    quotable strike when the ideal one is illiquid. Returns the ROW."""
    candidates = [r for r in rows if _is_quotable(_side_quote(r, spec.option_type))]
    if not candidates:
        return None
    if spec.strike_rule == "atm":
        ideal = atm_strike
        return min(candidates, key=lambda r: abs(r["strike"] - ideal))
    if spec.strike_rule == "atm_offset":
        ordered = sorted(rows, key=lambda r: r["strike"])
        strikes = [r["strike"] for r in ordered]
        try:
            atm_idx = strikes.index(min(strikes, key=lambda s: abs(s - atm_strike)))
        except ValueError:
            return None
        # OTM direction: calls offset upward, puts downward.
        direction = 1 if spec.option_type == "CE" else -1
        idx = max(0, min(len(ordered) - 1, atm_idx + direction * spec.offset))
        row = ordered[idx]
        if _is_quotable(_side_quote(row, spec.option_type)):
            return row
        return min(candidates, key=lambda r: abs(r["strike"] - row["strike"]))
    if spec.strike_rule == "delta":
        # Target |delta|; calls have positive delta, puts negative.
        def _dist(r: dict) -> float:
            q = _side_quote(r, spec.option_type)
            d = q.get("delta")
            if d is None:
                return 9e9
            return abs(abs(float(d)) - spec.delta)
        best = min(candidates, key=_dist)
        return best if _dist(best) < 9e8 else None
    return None


# ── Payoff / POP math ────────────────────────────────────────────────


def _leg_expiry_pnl(grid: np.ndarray, leg: dict, lot_value: int) -> np.ndarray:
    """Expiry P&L (₹) of one leg over an underlying-price grid."""
    K = float(leg["strike"])
    intrinsic = (
        np.maximum(grid - K, 0.0) if leg["option_type"] == "CE"
        else np.maximum(K - grid, 0.0)
    )
    premium = float(leg["mid"])
    sign = 1.0 if leg["side"] == "BUY" else -1.0
    return sign * (intrinsic - premium) * lot_value


def _prob_above(x: float, F: float, sigma: float, T: float) -> float:
    """Risk-neutral P(S_T > x) under lognormal forward."""
    if x <= 0:
        return 1.0
    if sigma <= 0 or T <= 0:
        return 1.0 if F > x else 0.0
    d2 = (math.log(F / x) - 0.5 * sigma * sigma * T) / (sigma * math.sqrt(T))
    return float(ndtr(d2))


def _pop_from_payoff(
    grid: np.ndarray, pnl: np.ndarray, F: float, sigma: Optional[float], T: float,
) -> Optional[float]:
    """P(P&L > 0): partition (0, ∞) at the interpolated breakevens, sum
    P(S_T ∈ interval) over the profitable intervals. For interval
    [lo, hi]: P = P(S>lo) − P(S>hi). The payoff is piecewise-linear at
    expiry, so the sign between consecutive breakevens is constant —
    sampled at the interval midpoint via the dense grid."""
    if sigma is None or sigma <= 0 or T <= 0:
        return None
    bounds = [0.0] + _breakevens(grid, pnl) + [float("inf")]
    pop = 0.0
    for lo, hi in zip(bounds[:-1], bounds[1:]):
        # Sign at the interval midpoint (clamped onto the grid).
        mid = (lo + hi) / 2.0 if hi != float("inf") else max(lo * 1.05, lo + 1.0)
        sign = float(np.interp(mid, grid, pnl))
        if sign > 0:
            p_lo = _prob_above(lo, F, sigma, T) if lo > 0 else 1.0
            p_hi = _prob_above(hi, F, sigma, T) if hi != float("inf") else 0.0
            pop += max(0.0, p_lo - p_hi)
    return round(min(max(pop, 0.0), 1.0), 4)


def _breakevens(grid: np.ndarray, pnl: np.ndarray) -> list[float]:
    out: list[float] = []
    for i in range(1, len(grid)):
        a, b = pnl[i - 1], pnl[i]
        if (a < 0 <= b) or (a >= 0 > b):
            # Linear interpolation of the zero crossing.
            if b != a:
                x = grid[i - 1] + (0.0 - a) * (grid[i] - grid[i - 1]) / (b - a)
                out.append(round(float(x), 2))
    return out


def _bounded(edge_slope: float) -> bool:
    """A payoff is bounded on an edge when its slope there is ~zero."""
    return abs(edge_slope) < 1e-9


# ── Resolution → full card payload ───────────────────────────────────


class StrategyResolutionError(Exception):
    """Raised when a template can't be resolved against the live chain."""


def resolve_strategy(
    db: Session,
    underlying: str,
    template_name: str,
    *,
    expiry: Optional[str] = None,
    qty_lots: int = 1,
    explicit_legs: Optional[list[dict]] = None,
    chain: Optional[dict] = None,
) -> dict[str, Any]:
    """Resolve a template (or explicit legs) against the live chain into
    the full ``option_strategy_card`` payload (sans _render_hint)."""
    from backend.market.option_chain import get_chain

    template = TEMPLATES.get(template_name)
    if template is None and not explicit_legs:
        raise StrategyResolutionError(
            f"Unknown strategy template '{template_name}'. "
            f"Known: {', '.join(sorted(TEMPLATES))}."
        )

    chain = chain or get_chain(db, underlying, expiry, width=15)
    if chain is None:
        raise StrategyResolutionError(
            f"No option chain for '{underlying.upper()}' — unknown "
            "underlying/expiry or instrument master not refreshed."
        )
    rows: list[dict] = chain["rows"]
    F = float(chain["forward"])
    T = float(chain["t_years"])
    atm = float(chain["atm_strike"])
    lot_size = int(chain.get("lot_size") or 0)
    if lot_size <= 0:
        raise StrategyResolutionError(
            f"No lot size in the instrument master for {underlying.upper()}."
        )
    qty_lots = max(1, int(qty_lots))
    lot_value = lot_size * qty_lots

    # ── Legs ──
    legs: list[dict] = []
    liquidity_flags: list[str] = []
    if explicit_legs:
        for el in explicit_legs:
            row = next(
                (r for r in rows if abs(r["strike"] - float(el["strike"])) < 1e-9),
                None,
            )
            q = _side_quote(row, el["option_type"]) if row else None
            if not _is_quotable(q):
                raise StrategyResolutionError(
                    f"Strike {el['strike']} {el['option_type']} isn't quotable "
                    f"on {chain['expiry']} (outside the liquid slice?)."
                )
            legs.append({
                "option_type": el["option_type"], "side": el["side"],
                "strike": float(el["strike"]), "mid": float(q["mid"]),
                "iv": q.get("iv"), "delta": q.get("delta"),
                "iv_status": q.get("iv_status"),
                "tradingsymbol": q.get("tradingsymbol"),
                "instrument_token": q.get("instrument_token"),
            })
    else:
        for spec in template.legs:
            row = _resolve_leg_strike(rows, atm, spec)
            q = _side_quote(row, spec.option_type) if row else None
            if not _is_quotable(q):
                raise StrategyResolutionError(
                    f"Couldn't find a liquid {spec.option_type} strike for "
                    f"{template.label} on {underlying.upper()} {chain['expiry']}."
                )
            legs.append({
                "option_type": spec.option_type, "side": spec.side,
                "strike": float(row["strike"]), "mid": float(q["mid"]),
                "iv": q.get("iv"), "delta": q.get("delta"),
                "iv_status": q.get("iv_status"),
                "tradingsymbol": q.get("tradingsymbol"),
                "instrument_token": q.get("instrument_token"),
            })
    # Same-strike-same-type duplicate legs collapse poorly — reject.
    seen = {(l["option_type"], l["side"], l["strike"]) for l in legs}
    if len(seen) != len(legs):
        raise StrategyResolutionError(
            "Resolved duplicate legs (chain too narrow for distinct "
            "strikes) — widen the view or pick strikes explicitly."
        )
    for l in legs:
        if l["iv_status"] == "wide_spread":
            liquidity_flags.append(
                f"{l['tradingsymbol'] or l['strike']}: wide bid-ask spread"
            )

    # ── Economics ──
    # net premium: credit positive (premium received), debit negative.
    net_premium = round(sum(
        (1.0 if l["side"] == "SELL" else -1.0) * l["mid"] for l in legs
    ) * lot_value, 2)

    span = max(0.25 * F, 4.0 * (chain.get("expected_move") or {}).get("abs", 0.05 * F))
    grid = np.linspace(max(F - span, 0.0), F + span, _PAYOFF_POINTS)
    pnl = np.zeros_like(grid)
    for l in legs:
        pnl += _leg_expiry_pnl(grid, l, lot_value)

    lo_slope = float(pnl[1] - pnl[0])
    hi_slope = float(pnl[-1] - pnl[-2])
    max_profit: Optional[float] = round(float(pnl.max()), 2)
    max_loss: Optional[float] = round(float(-pnl.min()), 2)
    if hi_slope > 1e-9 or lo_slope < -1e-9:   # rises toward an open edge
        max_profit = None
    if hi_slope < -1e-9 or lo_slope > 1e-9:   # falls toward an open edge
        max_loss = None

    atm_row = next((r for r in rows if r["strike"] == atm), None)
    atm_ivs = [
        _side_quote(atm_row, s).get("iv")
        for s in ("CE", "PE")
        if atm_row and _is_quotable(_side_quote(atm_row, s))
        and _side_quote(atm_row, s).get("iv")
    ]
    sigma_atm = float(np.mean(atm_ivs)) if atm_ivs else None
    pop = _pop_from_payoff(grid, pnl, F, sigma_atm, T)

    net_greeks = {k: 0.0 for k in ("delta", "gamma", "theta", "vega")}
    for l in legs:
        # Re-read full greeks from the chain row for this side.
        row = next(r for r in rows if r["strike"] == l["strike"])
        q = _side_quote(row, l["option_type"])
        sign = 1.0 if l["side"] == "BUY" else -1.0
        for k in net_greeks:
            v = q.get(k)
            if v is not None:
                net_greeks[k] += sign * float(v) * lot_value
    net_greeks = {k: round(v, 4) for k, v in net_greeks.items()}

    # ── Margin / capital (P1 heuristic; SPAN approximation lands P2) ──
    short_legs = [l for l in legs if l["side"] == "SELL"]
    if max_loss is not None:
        # Defined risk: brokers block ≈ max loss for spreads; a pure
        # debit position needs just the debit.
        margin = max(max_loss, -min(net_premium, 0.0))
        margin_note = "Defined-risk estimate ≈ max loss (broker SPAN may differ)."
    else:
        margin = sum(
            l["mid"] * lot_value + _SHORT_MARGIN_NOTIONAL_PCT * F * lot_value
            for l in short_legs
        )
        margin_note = (
            "Naked-short estimate: premium + "
            f"{int(_SHORT_MARGIN_NOTIONAL_PCT * 100)}% of notional per short "
            "leg. Broker SPAN+exposure is authoritative."
        )
    capital_required = round(float(max(margin, -min(net_premium, 0.0))), 2)

    # ── Validation ──
    research_only = bool(chain.get("research_only"))
    expiry_date = chain["expiry"]
    dte_days = T * 365.0
    validation = {
        "lot_multiple_ok": True,  # qty is in lots by construction
        "min_lots": 1,
        "max_lots": 100,
        "liquidity_ok": not liquidity_flags,
        "liquidity_flags": liquidity_flags,
        "expiry_gamma_warn": bool(short_legs) and dte_days <= 1.0,
        "mcx_execution_blocked": research_only,
        "requires_disclosure": True,
    }

    payload = {
        "locked": {
            "underlying": chain["underlying"],
            "segment": chain["segment"],
            "exchange": chain["exchange"],
            "spot": chain.get("spot"),
            "forward": F,
            "expiry": expiry_date,
            "expiry_kind": next(
                (e["kind"] for e in chain.get("expiries", [])
                 if e["expiry"] == expiry_date), "monthly",
            ),
            "lot_size": lot_size,
            "research_only": research_only,
            "disclosure": SEBI_DISCLOSURE,
        },
        "editable": {
            "template": template_name if template else "custom",
            "book": "paper",
            "qty_lots": qty_lots,
            "legs": legs,
        },
        "computed": {
            "net_premium": net_premium,
            "payoff": [
                {"s": round(float(s), 2), "pnl": round(float(p), 2)}
                for s, p in zip(grid, pnl)
            ],
            "breakevens": _breakevens(grid, pnl),
            "max_loss": max_loss,
            "max_profit": max_profit,
            "pop": pop,
            "net_greeks": net_greeks,
            "capital_required": capital_required,
            "margin_estimate": round(float(margin), 2),
            "margin_note": margin_note,
        },
        "validation": validation,
    }
    payload["critique"] = critique_strategy(db, payload)
    return payload


# ── Suggest-flow ─────────────────────────────────────────────────────


_VIEW_ALIASES = {
    "bullish": "bullish", "bull": "bullish", "up": "bullish",
    "bearish": "bearish", "bear": "bearish", "down": "bearish",
    "neutral": "neutral", "sideways": "neutral", "rangebound": "neutral",
    "range-bound": "neutral", "income": "neutral",
    "volatile": "volatile", "big move": "volatile", "bigmove": "volatile",
    "event": "volatile", "breakout": "volatile",
}


def suggest_strategies(
    db: Session,
    underlying: str,
    view: str,
    *,
    expiry: Optional[str] = None,
    risk: Optional[str] = None,
    qty_lots: int = 1,
) -> dict[str, Any]:
    """The 3-question suggest-flow: resolve the view's candidate ladder,
    open the card on the requested risk tier (default: conservative)."""
    from backend.market.option_chain import get_chain

    norm_view = _VIEW_ALIASES.get((view or "").strip().lower())
    if norm_view is None:
        raise StrategyResolutionError(
            f"Unknown view '{view}'. Use bullish, bearish, neutral, or volatile."
        )
    chain = get_chain(db, underlying, expiry, width=15)
    if chain is None:
        raise StrategyResolutionError(
            f"No option chain for '{underlying.upper()}'."
        )

    candidates_meta: list[dict] = []
    resolved: dict[str, dict] = {}
    for name in VIEW_CANDIDATES[norm_view]:
        t = TEMPLATES[name]
        if t.needs_holding:
            continue  # suggest-flow never assumes a holding
        try:
            payload = resolve_strategy(
                db, underlying, name,
                expiry=expiry, qty_lots=qty_lots, chain=chain,
            )
        except StrategyResolutionError as exc:
            logger.info("[suggest] %s skipped: %s", name, exc)
            continue
        resolved[name] = payload
        candidates_meta.append({
            "template": name,
            "label": t.label,
            "risk_tag": t.risk_tag,
            "pop": payload["computed"]["pop"],
            "max_loss": payload["computed"]["max_loss"],
            "max_profit": payload["computed"]["max_profit"],
            "net_premium": payload["computed"]["net_premium"],
            "one_liner": t.one_liner,
            "legs": [
                {"option_type": l["option_type"], "side": l["side"],
                 "strike": l["strike"]}
                for l in payload["editable"]["legs"]
            ],
        })
    if not resolved:
        raise StrategyResolutionError(
            f"No {norm_view} strategy could be resolved on "
            f"{underlying.upper()} {chain['expiry']} — chain too illiquid."
        )

    wanted_tag = (risk or "conservative").strip().lower()
    primary_name = next(
        (c["template"] for c in candidates_meta if c["risk_tag"] == wanted_tag),
        candidates_meta[0]["template"],
    )
    primary = resolved[primary_name]
    primary["candidates"] = [
        c for c in candidates_meta if c["template"] != primary_name
    ]
    primary["view"] = norm_view
    return primary


# ── Copilot critique (rule-based, P1) ────────────────────────────────


def _realized_vol_20d(underlying: str, segment: str) -> Optional[float]:
    """Annualized 20d close-to-close realized vol of the underlying —
    feeds the IV-vs-RV (VRP) critique. Best-effort; None on any miss.
    NSE underlyings only — yfinance has no MCX/BSE-index symbols worth
    guessing at, and a wrong-symbol 404 costs latency per critique."""
    if not segment.startswith("NFO"):
        return None
    try:
        from backend.core.data.historical import get_ohlcv

        symbol = {"NIFTY": "NIFTY50", "BANKNIFTY": "NIFTYBANK"}.get(
            underlying, underlying,
        )
        df = get_ohlcv(symbol, period="3mo", interval="1d")
        closes = df["Close"].dropna().to_numpy()[-21:]
        if len(closes) < 15:
            return None
        rets = np.diff(np.log(closes))
        return float(np.std(rets, ddof=1) * np.sqrt(252.0))
    except Exception:
        return None


def critique_strategy(db: Session, payload: dict[str, Any]) -> dict[str, Any]:
    """Pre-trade critique — the 'should I even do this?' pass. Pure
    rules over the resolved payload + paper-account context. Returns
    {verdict, flags[], summary}."""
    flags: list[dict] = []
    computed = payload["computed"]
    locked = payload["locked"]
    legs = payload["editable"]["legs"]
    short_legs = [l for l in legs if l["side"] == "SELL"]

    # Undefined risk is THE account-killer — always the loudest flag.
    if computed["max_loss"] is None:
        flags.append({
            "severity": "risk",
            "text": (
                "Unlimited loss potential — naked short leg(s). A defined-"
                "risk spread (add a protective wing) caps the downside."
            ),
        })

    # Max loss vs paper-account size.
    try:
        from backend.models import PaperAccount

        acct = db.query(PaperAccount).first()
        equity = float(acct.cash_settled) if acct else None
    except Exception:
        equity = None
    if equity and computed["max_loss"]:
        ratio = computed["max_loss"] / equity
        if ratio > 0.10:
            flags.append({
                "severity": "risk",
                "text": (
                    f"Max loss ₹{computed['max_loss']:,.0f} is "
                    f"{ratio * 100:.0f}% of the paper account — oversized. "
                    "Consider fewer lots."
                ),
            })
        elif ratio > 0.05:
            flags.append({
                "severity": "warn",
                "text": (
                    f"Max loss is {ratio * 100:.0f}% of the paper account "
                    "(guideline: keep single-trade risk under 5%)."
                ),
            })

    # Liquidity.
    for note in payload["validation"]["liquidity_flags"]:
        flags.append({"severity": "warn", "text": f"Liquidity: {note}"})

    # IV regime vs realized (VRP). IVP needs an IV history store (P4);
    # IV-vs-RV is computable today and catches the worst mistakes.
    atm_ivs = [l["iv"] for l in legs if l.get("iv")]
    iv_mean = float(np.mean(atm_ivs)) if atm_ivs else None
    rv = _realized_vol_20d(locked["underlying"], locked["segment"])
    if iv_mean and rv and rv > 0:
        vrp = iv_mean - rv
        if short_legs and vrp < -0.02:
            flags.append({
                "severity": "warn",
                "text": (
                    f"Selling options below realized vol (IV {iv_mean:.0%} < "
                    f"20d RV {rv:.0%}) — premium looks cheap, not rich."
                ),
            })
        if not short_legs and vrp > 0.05:
            flags.append({
                "severity": "warn",
                "text": (
                    f"Buying options at a rich premium (IV {iv_mean:.0%} vs "
                    f"20d RV {rv:.0%}) — a vol drop hurts even if direction "
                    "is right."
                ),
            })

    # Expiry-day gamma.
    if payload["validation"]["expiry_gamma_warn"]:
        flags.append({
            "severity": "risk",
            "text": (
                "Short option(s) on expiry day — gamma risk is extreme; "
                "small moves cause outsized losses."
            ),
        })

    # Theta direction (informational).
    theta = computed["net_greeks"].get("theta", 0.0)
    if theta < 0:
        flags.append({
            "severity": "info",
            "text": f"Time decay costs ≈₹{abs(theta):,.0f}/day at current IV.",
        })
    elif theta > 0:
        flags.append({
            "severity": "info",
            "text": f"Time decay earns ≈₹{theta:,.0f}/day while the range holds.",
        })

    # Stretch debit positions: a low market-implied POP on a debit
    # structure means the breakeven sits beyond the priced move.
    if (
        computed["net_premium"] < 0
        and computed["pop"] is not None
        and computed["pop"] < 0.30
    ):
        flags.append({
            "severity": "warn",
            "text": (
                f"Probability of profit is {computed['pop']:.0%} — the "
                "move needed is larger than the market is pricing."
            ),
        })

    # MCX research-only.
    if payload["validation"]["mcx_execution_blocked"]:
        flags.append({
            "severity": "info",
            "text": "Commodity (MCX) options are research-only on Pivot — no execution.",
        })

    risk_count = sum(1 for f in flags if f["severity"] == "risk")
    warn_count = sum(1 for f in flags if f["severity"] == "warn")
    verdict = "risky" if risk_count else ("caution" if warn_count else "ok")
    if verdict == "ok":
        summary = "Structure looks reasonable: defined risk, liquid strikes, sane sizing."
    elif verdict == "caution":
        summary = "Workable, but mind the flagged items before registering."
    else:
        summary = "High-risk structure — fix the red flags (or downsize) before going ahead."
    return {"verdict": verdict, "flags": flags, "summary": summary}
