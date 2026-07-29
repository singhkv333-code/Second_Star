"""
Persist the 3 finalised Market Views (IT trouble / Monsoon / Crude-Geo)
into the View Markets backend — curated MarketView rows + ViewExpression
rows + workflow DRAFT rows (register-not-execute, never activates).

Run with:
  cd /Users/karanveersingh/Downloads/Second_Star/pivot
  .venv/bin/python scripts/strategy_research/persist_views.py

NO Azure/OpenAI LLM is called. NO order is placed. NO migration is run.
Only additive DB writes (curated drafts, published).
"""
from __future__ import annotations
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from datetime import datetime, timezone, timedelta

from backend.database import SessionLocal
from backend.schemas import (
    MarketViewCreate, ViewExpressionInput, ViewTransmissionInput,
    ViewConfidenceInput,
)
from backend.view_markets import curation
from backend.view_markets.deployment.deploy import deploy_expression
from backend.models import (
    ViewExpression, ViewTransmission, ViewConfidence,
    ExpressionTier, ExpressionKind, ConfidenceDimension,
)

# ── constants ─────────────────────────────────────────────────────────────────
USER_ID = 1          # user who owns the workflow drafts (test@pivot.com)
ACTIVATED = False    # NEVER activate — register-not-execute, drafts only

def _add_one(db, obj) -> None:
    """Add a single ORM row and flush immediately (avoids bulk-insert sentinel bug)."""
    db.add(obj)
    db.flush()


def _add_transmission_edges(db, view_id: str, edges: list[ViewTransmissionInput]) -> None:
    """Add transmission edges one-by-one (avoids SQLAlchemy insertmanyvalues UUID bug)."""
    for edge in edges:
        _add_one(db, ViewTransmission(
            view_id=view_id,
            seq=edge.seq,
            from_node=edge.from_node,
            to_node=edge.to_node,
            edge_label=edge.edge_label,
            strength=edge.strength,
            evidence=edge.evidence,
        ))


def _add_expression(db, view_id: str, expr: ViewExpressionInput) -> ViewExpression:
    """Add a single expression row and flush immediately."""
    row = ViewExpression(
        view_id=view_id,
        tier=ExpressionTier(expr.tier),
        expression_kind=ExpressionKind(expr.expression_kind),
        config=dict(expr.config or {}),
        rationale=expr.rationale,
        risk_profile=expr.risk_profile,
        capital_intensity=expr.capital_intensity,
        historical_strength=expr.historical_strength,
        time_horizon=expr.time_horizon,
    )
    _add_one(db, row)
    return row


def _add_confidence(db, view_id: str, conf: ViewConfidenceInput) -> ViewConfidence:
    """Add a single confidence row and flush immediately."""
    row = ViewConfidence(
        view_id=view_id,
        dimension=ConfidenceDimension(conf.dimension),
        score=conf.score,
        evidence=conf.evidence,
    )
    _add_one(db, row)
    return row


def _now_ist_iso() -> str:
    """Approx IST now as a naive ISO string (scheduler expects this format)."""
    return (datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)).strftime(
        "%Y-%m-%dT%H:%M:%S"
    )

def _manual_trigger() -> dict:
    """trigger.manual — user fires manually. Zero config."""
    return {"step_type": "trigger.manual", "config": {}}

def _schedule_trigger(when_iso: str) -> dict:
    """trigger.schedule one-time at a specific IST time."""
    return {
        "step_type": "trigger.schedule",
        "config": {"run_at": when_iso, "timezone": "Asia/Kolkata"},
    }


# ═══════════════════════════════════════════════════════════════════════════════
# VIEW 1 — India's IT giants are in trouble
# Best strategy: C (Defensive rotation basket, GRADE A−)
# Also persist: B (FMCG vs IT-SSF pair, GRADE B) + A (INFY bear put, GRADE C)
# ═══════════════════════════════════════════════════════════════════════════════
VIEW1_PAYLOAD = MarketViewCreate(
    view_type="event",
    title="India's IT giants are in trouble (weak guidance cycle)",
    thesis=(
        "TCS-anchored weak-print analogs (8 events, 2021-2025) show a consistent "
        "defensive rotation: FMCG/staples outperform IT on and after weak guidance "
        "quarters (defensive CAAR +0.60%, t=2.00, p=0.046 — the only statistically "
        "clean signal in the study). IT names' own CAARs are insignificant (|t|<1). "
        "Strategy C (defensive basket) is the primary expression; B (FMCG vs IT-SSF "
        "pair) is the market-neutral alternative. All backtests verdict UNPROVEN "
        "(8 events; MinTRL 56–937 obs gap is the binding constraint)."
    ),
    category="equity_rotation",
    time_horizon="4–8 weeks per event (held around weak guidance quarter print)",
    transmission=[],
    confidence=[
        # Best strategy C dials: OUTCOME B (64), EXPRESSION suppressed (below MinTRL)
        # We score the composite view using strategy A's dials (the one that cleared MinTRL)
        ViewConfidenceInput(
            dimension="outcome",
            score=0.64,  # Strategy C OUTCOME B = 64
            evidence=(
                "Outcome dial B/64: hit-rate 75% (Strategy B, 8 events); "
                "defensive CAAR +0.60% t=2.00 p=0.046 (best signal). "
                "All strategies verdict UNPROVEN — 8-event analog sample."
            ),
        ),
        ViewConfidenceInput(
            dimension="expression",
            score=0.49,  # Strategy A expression C = 49 (only one scored)
            evidence=(
                "Expression dial C/49 (Strategy A — only one to clear MinTRL n=72>56). "
                "Strategies B and C suppressed (n<MinTRL). Cost-survival 77% (A). "
                "Primary expression (C) uses long-only delivery — cleanest real-world edge."
            ),
        ),
    ],
    expressions=[
        # ── Strategy C: Defensive rotation basket (GRADE A−, PRIMARY) ─────────────
        ViewExpressionInput(
            tier="conservative",
            expression_kind="basket",
            rationale=(
                "On confirmed weak IT guidance, rotate into equal-weight FMCG staples "
                "(NESTLEIND, HINDUNILVR, ITC, DABUR). Rides the only statistically "
                "significant signal in the study (defensive CAAR +0.60%, t=2.00, p=0.046). "
                "Long-only CNC, no margin/short/lot friction. Hold 4-8 weeks."
            ),
            risk_profile=(
                "GRADE A−. Long-only equity delivery. Max DD −6.26% (backtest). "
                "mc dd_p95 −13.06%, prob_loss 0.213. UNPROVEN verdict (n=160 < MinTRL 937). "
                "Sub-period pos-frac 0.75, concentration 0.32 (lowest of the three)."
            ),
            capital_intensity=(
                "Low-to-medium: 4-stock equal-weight basket, CNC delivery. "
                "No margin, no F&O. Min ~₹50K-1L for meaningful exposure."
            ),
            historical_strength=(
                "Backtest 2021-2025, 8 analog events. Total return +7.01%, NIFTY same-window −4.85% "
                "(excess +11.86%). Max DD −6.26%, win-rate 48.8%, PSR 0.751, DSR 0.431. "
                "MinTRL SUPPRESSED expression dial (n=160 < 937). Real yfinance data, "
                "real trading_costs (36.9 bps round-trip). No fabrication."
            ),
            time_horizon="4–8 weeks (entered on confirmed weak guidance; exit after sector re-rating)",
            config={
                "schema_version": 1,
                "expression_kind": "basket",
                "tier": "conservative",
                "label": "IT-Trouble: Defensive FMCG rotation basket (Strategy C)",
                "instruments": [
                    {"symbol": "NESTLEIND", "exchange": "NSE", "segment": "EQ",
                     "instrument_type": "equity", "role": "long", "tradeable": True},
                    {"symbol": "HINDUNILVR", "exchange": "NSE", "segment": "EQ",
                     "instrument_type": "equity", "role": "long", "tradeable": True},
                    {"symbol": "ITC", "exchange": "NSE", "segment": "EQ",
                     "instrument_type": "equity", "role": "long", "tradeable": True},
                    {"symbol": "DABUR", "exchange": "NSE", "segment": "EQ",
                     "instrument_type": "equity", "role": "long", "tradeable": True},
                ],
                "structure": {
                    "scheme": "equal_weight",
                    "weights": {
                        "NESTLEIND": 0.25,
                        "HINDUNILVR": 0.25,
                        "ITC": 0.25,
                        "DABUR": 0.25,
                    },
                    "n_names": 4,
                    "single_name_cap": 0.30,
                },
                "timing": {
                    "mode": "confirmation",
                    "tranches": [{"pct": 100, "trigger": _manual_trigger()}],
                    "invalidation": None,
                    "note": "Fire manually when weak IT guidance is confirmed at print.",
                },
                "scores": {
                    "construction_alignment": 64.0,
                    "alignment_kind": "event_study",
                    "backtest": {
                        "total_return_pct": 7.01,
                        "nifty_same_window_pct": -4.85,
                        "excess_return_pct": 11.86,
                        "max_dd_pct": -6.26,
                        "win_rate": 0.488,
                        "psr": 0.751,
                        "dsr": 0.431,
                        "min_trl": 937,
                        "n_obs": 160,
                        "min_trl_cleared": False,
                        "mc_dd_p95_pct": -13.06,
                        "mc_prob_loss": 0.213,
                        "sub_period_pos_frac": 0.75,
                        "concentration": 0.32,
                        "trust_verdict": "UNPROVEN",
                        "trust_conf": 43,
                        "outcome_dial": "B",
                        "outcome_score": 64,
                        "expression_dial": "SUPPRESSED",
                        "grade": "A-",
                        "caar_pct": 0.60,
                        "caar_t": 2.00,
                        "caar_p": 0.046,
                        "n_events": 8,
                    },
                },
                "costs": {"round_trip_bps": 36.9, "note": "Real trading_costs, equity delivery"},
                "warnings": [
                    "Expression dial SUPPRESSED: n_obs=160 < MinTRL=937 (near-zero per-bar Sharpe).",
                    "UNPROVEN verdict — 8 analog events. Widen sample before scaling.",
                    "Significant event signal (defensive CAAR p=0.046) but lowest per-bar Sharpe.",
                ],
                "disclaimer": (
                    "This is analysis from a backtest simulation, not financial advice. "
                    "Past event-study results do not guarantee future returns. "
                    "Pivot registers orders; you confirm and place in your broker app."
                ),
            },
        ),
        # ── Strategy B: FMCG vs IT-SSF pair (GRADE B, market-neutral) ────────────
        ViewExpressionInput(
            tier="balanced",
            expression_kind="pair",
            rationale=(
                "Beta-neutral long NESTLE+HUL vs short HCLTECH-SSF (F&O future). "
                "Market-neutral: survives a broad NIFTY move. Best event hit-rate (75%). "
                "Laddered entry around the guidance print. Hold 4-8 weeks."
            ),
            risk_profile=(
                "GRADE B. Pair trade with SSF short. Max DD −7.26%. "
                "mc dd_p95 −10.89%, prob_loss 0.162. UNPROVEN (n=104 < MinTRL 249). "
                "~15-20% SPAN margin required for the SSF leg. Monthly roll."
            ),
            capital_intensity=(
                "Medium: F&O account required. HCLTECH SSF margin + equity long legs. "
                "Operationally heaviest of the three strategies."
            ),
            historical_strength=(
                "Backtest 2021-2025, 8 events. Total return +9.55%, NIFTY +−0.34% "
                "(excess +9.88%). Hit-rate 75% (best). PSR 0.855, DSR 0.582. "
                "Expression dial SUPPRESSED (n=104 < MinTRL 249). "
                "Real trading_costs, real Trust Battery."
            ),
            time_horizon="4–8 weeks, monthly roll of the SSF short leg",
            config={
                "schema_version": 1,
                "expression_kind": "pair",
                "tier": "balanced",
                "label": "IT-Trouble: FMCG vs IT-SSF market-neutral pair (Strategy B)",
                "instruments": [
                    {"symbol": "NESTLEIND", "exchange": "NSE", "segment": "EQ",
                     "instrument_type": "equity", "role": "long", "tradeable": True},
                    {"symbol": "HINDUNILVR", "exchange": "NSE", "segment": "EQ",
                     "instrument_type": "equity", "role": "long", "tradeable": True},
                    {"symbol": "HCLTECH", "exchange": "NFO", "segment": "NFO-FUT",
                     "instrument_type": "ssf_future", "role": "short", "tradeable": True},
                ],
                "structure": {
                    "a": "NESTLEIND_HINDUNILVR_basket",
                    "b": "HCLTECH",
                    "leg_a": {"notional": None, "note": "Equal-weight NESTLE+HUL long"},
                    "short_leg": {
                        "mode": "ssf_future",
                        "instrument": "HCLTECH",
                        "exchange": "NFO",
                        "note": (
                            "Short via HCLTECH SSF (NSE Futures). Monthly physical settlement. "
                            "Beta-hedge: size to match basket beta (~1.0). "
                            "Square off before expiry to avoid STT-on-intrinsic."
                        ),
                    },
                    "beta": 1.0,
                    "rigor_tier": "event_study",
                },
                "timing": {
                    "mode": "confirmation",
                    "tranches": [{"pct": 100, "trigger": _manual_trigger()}],
                    "invalidation": None,
                    "note": "Fire manually; ladder entry around the guidance print.",
                },
                "scores": {
                    "construction_alignment": 73.0,
                    "alignment_kind": "event_study",
                    "backtest": {
                        "total_return_pct": 9.55,
                        "nifty_same_window_pct": -0.34,
                        "excess_return_pct": 9.88,
                        "max_dd_pct": -7.26,
                        "win_rate": 0.75,
                        "psr": 0.855,
                        "dsr": 0.582,
                        "min_trl": 249,
                        "n_obs": 104,
                        "min_trl_cleared": False,
                        "mc_dd_p95_pct": -10.89,
                        "mc_prob_loss": 0.162,
                        "sub_period_pos_frac": 0.5,
                        "concentration": 0.45,
                        "trust_verdict": "UNPROVEN",
                        "trust_conf": 58,
                        "outcome_dial": "B",
                        "outcome_score": 73,
                        "expression_dial": "SUPPRESSED",
                        "grade": "B",
                        "n_events": 8,
                    },
                },
                "costs": {"round_trip_bps": 36.9, "note": "Two equity legs + SSF monthly roll"},
                "warnings": [
                    "SSF short requires F&O account + ~15-20% SPAN margin.",
                    "Expression dial SUPPRESSED: n=104 < MinTRL 249.",
                    "UNPROVEN verdict — 8 analog events.",
                    "Monthly roll of the HCLTECH SSF adds slippage.",
                ],
                "disclaimer": (
                    "This is analysis, not financial advice. "
                    "Pivot registers orders; you confirm and place in your broker app."
                ),
            },
        ),
        # ── Strategy A: INFY bear put spread (GRADE C, tactical pre-print) ────────
        ViewExpressionInput(
            tier="aggressive",
            expression_kind="option_strategy",
            rationale=(
                "Pre-position INFY monthly bear put spread (−0.40 delta proxy, "
                "defined-risk −1.8% net debit floor). Arms T-3 before TCS print. "
                "Skip if guidance pre-flagged positive. Highest Trust Battery PSR "
                "(0.969, the only strategy clearing MinTRL n=72>56), but rests on "
                "an optimistic option proxy (no theta/vega/IV modelled)."
            ),
            risk_profile=(
                "GRADE C. Defined-risk option debit spread — max loss = net premium paid. "
                "PSR 0.969 / DSR 0.845 / MinTRL cleared (72>56). "
                "UNPROVEN verdict (conf 84). mc dd_p95 −7.36%, prob_loss 0.086. "
                "CAVEAT: backtest is a delta proxy only — theta/vega/IV not modelled → optimistic."
            ),
            capital_intensity=(
                "Low: net debit only (no margin). Max loss = net premium (cap ~1.8%). "
                "1-lot INFY monthly option chain. Liquid; F&O account required."
            ),
            historical_strength=(
                "Backtest 2021-2025, 8 events. Total return +11.07%, NIFTY −6.01% "
                "(excess +17.08%). Hit-rate 62%. PSR 0.969, DSR 0.845. "
                "MinTRL CLEARED (72>56). INSTRUMENT CAAR −0.82% t=−0.46 p=0.65 "
                "(not significant). Real delta proxy, real trading_costs."
            ),
            time_horizon="T-3 entry before TCS print; square off pre-expiry (avoid STT-on-intrinsic)",
            config={
                "schema_version": 1,
                "expression_kind": "option_strategy",
                "tier": "aggressive",
                "label": "IT-Trouble: INFY monthly bear put spread (Strategy A)",
                "instruments": [
                    {"symbol": "INFY", "exchange": "NFO", "segment": "NFO-OPT",
                     "instrument_type": "equity_option", "role": "underlying", "tradeable": True},
                ],
                "structure": {
                    "underlying": "INFY",
                    "template": "bear_put_spread",
                    "expiry_rule": "monthly",
                    "qty_lots": 1,
                    "note": (
                        "Bear put spread: buy ATM put, sell lower-strike put. "
                        "Net debit ≈ 1.8% of INFY spot. Max profit at strike − debit. "
                        "Square off T-1 before expiry (physical settlement + STT). "
                        "No theta/vega modelled in backtest — real premium will differ."
                    ),
                },
                "timing": {
                    "mode": "pre_position",
                    "tranches": [{"pct": 100, "trigger": _manual_trigger()}],
                    "invalidation": None,
                    "note": "Arm T-3 before TCS guidance print; skip if positive signals.",
                },
                "scores": {
                    "construction_alignment": 49.0,
                    "alignment_kind": "event_study",
                    "backtest": {
                        "total_return_pct": 11.07,
                        "nifty_same_window_pct": -6.01,
                        "excess_return_pct": 17.08,
                        "max_dd_pct": -3.42,
                        "win_rate": 0.514,
                        "psr": 0.969,
                        "dsr": 0.845,
                        "min_trl": 56.1,
                        "n_obs": 72,
                        "min_trl_cleared": True,
                        "mc_dd_p95_pct": -7.36,
                        "mc_prob_loss": 0.086,
                        "trust_verdict": "UNPROVEN",
                        "trust_conf": 84,
                        "outcome_dial": "B",
                        "outcome_score": 64,
                        "expression_dial": "C",
                        "expression_score": 49,
                        "grade": "C",
                        "caar_pct": -0.82,
                        "caar_t": -0.46,
                        "caar_p": 0.65,
                        "n_events": 8,
                        "proxy_caveat": "Delta proxy only — theta/vega/IV not modelled. Optimistic.",
                    },
                },
                "costs": {
                    "round_trip_bps": 23.3,
                    "note": "Options round-trip. STT-on-intrinsic if held to expiry — always square pre-expiry."
                },
                "warnings": [
                    "Backtest is a delta proxy — no theta/vega/IV. Real option P&L will differ.",
                    "Instrument CAAR is NOT significant (p=0.65). The PSR rests on the proxy.",
                    "UNPROVEN verdict. Arm for 1 lot only until real option chain backtest is done.",
                    "Physical settlement: square off before expiry to avoid STT-on-intrinsic.",
                ],
                "disclaimer": (
                    "This is analysis, not financial advice. "
                    "Pivot registers orders; you confirm and place in your broker app."
                ),
            },
        ),
    ],
)


# ═══════════════════════════════════════════════════════════════════════════════
# VIEW 2 — Monsoon (Kharif season positioning)
# Best strategy: S2 (Tractor/2W vs NIFTY pair, GRADE A−, PROMISING)
# Also persist: S1 (Agri-input basket, GRADE C+) + S3 (M&M call spread, GRADE B)
# ═══════════════════════════════════════════════════════════════════════════════
VIEW2_PAYLOAD = MarketViewCreate(
    view_type="theme",
    title="Monsoon trade — Kharif season rural positioning (India)",
    thesis=(
        "India's Kharif crop cycle creates a repeatable seasonal edge: (1) Forecast "
        "run-up: tractor (M&M) and 2W (TVSMOTOR) outperform in Apr15-Jun15 "
        "regardless of market direction, once beta-stripped vs NIFTY future "
        "(CAAR +7.46%, t=2.83, p=0.005; trust verdict PROMISING, conf=97). "
        "(2) Sowing window: agri-input basket (COROMANDEL/RALLIS/UPL) outperforms "
        "only in IMD-confirmed-normal monsoon years (9/16 yr win-rate 78%). "
        "(3) M&M directional call spread (defined-risk) captures monsoon-beta upside. "
        "S2 is the core; S1 is the IMD-confirmation satellite; S3 the optional overlay."
    ),
    category="seasonal_macro",
    time_horizon="Seasonal (Apr–Sep annually; S2 core Apr15–Jun15, S1 Jun-Aug, S3 Apr-Sep)",
    transmission=[],
    confidence=[
        ViewConfidenceInput(
            dimension="outcome",
            score=0.69,  # S2 OUTCOME B = 69; S1=70 composite ~0.70
            evidence=(
                "Outcome dial B/69 (S2 core, hit-rate 62.5%, N=16). "
                "S1 dial B/70 (hit-rate 78%, N=9 normal years). "
                "S2 trust verdict PROMISING (conf=97, PSR=0.996, DSR=0.966). "
                "S1 verdict unproven (conf=90, DSR 0.896 < 0.95 threshold)."
            ),
        ),
        ViewConfidenceInput(
            dimension="expression",
            score=0.96,  # S2 EXPRESSION A = 96 — the strongest dial in the whole study
            evidence=(
                "Expression dial A/96 (S2 — CAAR alignment 100%, p=0.005, cost-survival 91%). "
                "Only strategy in the full 9-strategy analysis with an A on either dial. "
                "S1 Expression B/63 (alignment 100%, p=0.168). "
                "S3 Expression B/71 (alignment 100%, p=0.271 — M&M individual weak)."
            ),
        ),
    ],
    expressions=[
        # ── S2: Tractor/2W vs NIFTY pair (GRADE A−, PRIMARY, PROMISING) ──────────
        ViewExpressionInput(
            tier="balanced",
            expression_kind="pair",
            rationale=(
                "Beta-neutral long M&M+TVSMOTOR (equal-weight) vs short NIFTY future "
                "sized to the basket's market beta (~0.95). Forecast window Apr15-Jun15 "
                "only, every year (16 trades). Captures the genuine rural-positioning premium "
                "once market beta is stripped. The core placeable strategy — ONLY statistically "
                "PROMISING design in the full 9-strategy study (trust conf=97, no flags)."
            ),
            risk_profile=(
                "GRADE A−. Beta-neutral pair. Max DD −27.3% (backtest, 16yr). "
                "mc dd_p95 −26.4%, prob_loss 0.002. PROMISING verdict (conf=97, PSR=0.996, DSR=0.966). "
                "Needs futures account for NIFTY short + margin (~10-15% of position). "
                "~2-month seasonal hold, liquidate by Jun15."
            ),
            capital_intensity=(
                "Medium: M&M + TVSMOTOR equity (CNC), NIFTY future short (margin ~10-15%). "
                "F&O account required. Seasonal deploy — cash otherwise."
            ),
            historical_strength=(
                "Backtest 2010-2025, 16 seasonal events. Total return +164.1%, CAGR in-market 43.6%. "
                "Max DD −27.3%, win-rate 62.5%, PSR 0.996, DSR 0.966. MinTRL 261 < n_obs 692. "
                "CAAR +7.46% t=2.83 p=0.005 — the strongest significant signal. "
                "Real trading_costs (36.9 bps), real Trust Battery. NO fabrication."
            ),
            time_horizon="Apr 15 – Jun 15 annually (2-month seasonal window)",
            config={
                "schema_version": 1,
                "expression_kind": "pair",
                "tier": "balanced",
                "label": "Monsoon S2: Tractor/2W vs NIFTY beta-neutral pair (core)",
                "instruments": [
                    {"symbol": "M&M", "exchange": "NSE", "segment": "EQ",
                     "instrument_type": "equity", "role": "long", "tradeable": True},
                    {"symbol": "TVSMOTOR", "exchange": "NSE", "segment": "EQ",
                     "instrument_type": "equity", "role": "long", "tradeable": True},
                    {"symbol": "NIFTY", "exchange": "NFO", "segment": "NFO-FUT",
                     "instrument_type": "index_future", "role": "short", "tradeable": True},
                ],
                "structure": {
                    "a": "M&M_TVSMOTOR_basket",
                    "b": "NIFTY",
                    "leg_a": {
                        "notional": None,
                        "note": "Equal-weight M&M + TVSMOTOR long (NSE CNC delivery)",
                    },
                    "short_leg": {
                        "mode": "index_future",
                        "instrument": "NIFTY",
                        "exchange": "NFO",
                        "note": (
                            "Short NIFTY future sized to basket market beta (~0.95). "
                            "Use expanding-window pre-entry beta estimate. "
                            "Monthly contract; roll if hold crosses expiry. "
                            "Square off by Jun15 (before monsoon onset uncertainty)."
                        ),
                    },
                    "beta": 0.95,
                    "rigor_tier": "event_study",
                },
                "timing": {
                    "mode": "pre_position",
                    "tranches": [
                        {"pct": 100, "trigger": _schedule_trigger("2027-04-15T09:30:00")}
                    ],
                    "invalidation": None,
                    "note": "Arm April 15 each year; exit June 15.",
                },
                "scores": {
                    "construction_alignment": 96.0,
                    "alignment_kind": "event_study",
                    "backtest": {
                        "total_return_pct": 164.1,
                        "cagr_in_market_pct": 43.6,
                        "max_dd_pct": -27.3,
                        "win_rate": 0.625,
                        "psr": 0.996,
                        "dsr": 0.966,
                        "min_trl": 261,
                        "n_obs": 692,
                        "min_trl_cleared": True,
                        "mc_dd_p95_pct": -26.4,
                        "mc_prob_loss": 0.002,
                        "concentration": 0.485,
                        "trust_verdict": "PROMISING",
                        "trust_conf": 97,
                        "outcome_dial": "B",
                        "outcome_score": 69,
                        "expression_dial": "A",
                        "expression_score": 96,
                        "grade": "A-",
                        "caar_pct": 7.46,
                        "caar_t": 2.83,
                        "caar_p": 0.005,
                        "n_events": 16,
                        "n_independent_seasons": 16,
                    },
                },
                "costs": {
                    "round_trip_bps": 36.9,
                    "note": "Equity long legs + NIFTY future short (index roll if needed)"
                },
                "warnings": [
                    "Beta strip removes broad market risk but not rural/agri idio risk.",
                    "Max DD -27.3% is real — size accordingly (seasonal satellite position).",
                    "Autocorrelation within season inflates n_obs vs true 16 independent events.",
                ],
                "disclaimer": (
                    "This is analysis, not financial advice. Trust verdict PROMISING reflects "
                    "16 seasonal events; seasonal autocorrelation may inflate confidence. "
                    "Pivot registers orders; you confirm and place in your broker app."
                ),
            },
        ),
        # ── S1: Kharif Agri-Input Basket (GRADE C+, confirmation satellite) ───────
        ViewExpressionInput(
            tier="conservative",
            expression_kind="basket",
            rationale=(
                "Equal-weight agri-input basket [COROMANDEL, CHAMBLFERT, RALLIS, UPL, PIIND] "
                "deployed ONLY in sowing window Jun01-Aug31 AND ONLY in IMD-normal years "
                "(LPA≥96). Confirmation gating removes deficient-year drawdown. "
                "7/9 win-rate in normal years. Satellite position (~10-15% of capital)."
            ),
            risk_profile=(
                "GRADE C+. Long-only mid/large-cap basket. Max DD −20.4% (backtest). "
                "mc dd_p95 −26.9%, prob_loss 0.0025. Trust verdict unproven (conf=90, "
                "DSR=0.896 < 0.95). RALLIS/CHAMBLFERT are cash-only mid-caps — "
                "real slippage worse than modelled. Live IMD trigger carries forecast error."
            ),
            capital_intensity=(
                "Low: long-only CNC delivery. COROMANDEL/UPL/PIIND have F&O. "
                "RALLIS/CHAMBLFERT are mid-cap cash-only — cap individual weights."
            ),
            historical_strength=(
                "Backtest 2010-2025, 9 normal-monsoon events. Total return +95.0%, CAGR 34.2% "
                "(in-market). Max DD −20.4%, win-rate 77.8%. PSR 0.983, DSR 0.896. "
                "Outcome dial B/70, Expression B/63. Real trading_costs. "
                "Confirmation gate reduced mc dd_p95 from −37.9% to −26.9%."
            ),
            time_horizon="Jun 1 – Aug 31 only in IMD LPA≥96 confirmed-normal years",
            config={
                "schema_version": 1,
                "expression_kind": "basket",
                "tier": "conservative",
                "label": "Monsoon S1: Kharif agri-input basket (IMD-confirmation satellite)",
                "instruments": [
                    {"symbol": "COROMANDEL", "exchange": "NSE", "segment": "EQ",
                     "instrument_type": "equity", "role": "long", "tradeable": True},
                    {"symbol": "CHAMBLFERT", "exchange": "NSE", "segment": "EQ",
                     "instrument_type": "equity", "role": "long", "tradeable": True},
                    {"symbol": "RALLIS", "exchange": "NSE", "segment": "EQ",
                     "instrument_type": "equity", "role": "long", "tradeable": True},
                    {"symbol": "UPL", "exchange": "NSE", "segment": "EQ",
                     "instrument_type": "equity", "role": "long", "tradeable": True},
                    {"symbol": "PIIND", "exchange": "NSE", "segment": "EQ",
                     "instrument_type": "equity", "role": "long", "tradeable": True},
                ],
                "structure": {
                    "scheme": "equal_weight",
                    "weights": {
                        "COROMANDEL": 0.20,
                        "CHAMBLFERT": 0.20,
                        "RALLIS": 0.20,
                        "UPL": 0.20,
                        "PIIND": 0.20,
                    },
                    "n_names": 5,
                    "single_name_cap": 0.25,
                },
                "timing": {
                    "mode": "confirmation",
                    "tranches": [{"pct": 100, "trigger": _manual_trigger()}],
                    "invalidation": None,
                    "note": "Fire only after IMD confirms LPA≥96 in June forecast. Skip deficient years.",
                },
                "scores": {
                    "construction_alignment": 63.0,
                    "alignment_kind": "event_study",
                    "backtest": {
                        "total_return_pct": 95.0,
                        "cagr_in_market_pct": 34.2,
                        "max_dd_pct": -20.4,
                        "win_rate": 0.778,
                        "psr": 0.983,
                        "dsr": 0.896,
                        "min_trl": 352,
                        "n_obs": 581,
                        "min_trl_cleared": True,
                        "mc_dd_p95_pct": -26.9,
                        "mc_prob_loss": 0.0025,
                        "concentration": 0.387,
                        "trust_verdict": "unproven",
                        "trust_conf": 90,
                        "outcome_dial": "B",
                        "outcome_score": 70,
                        "expression_dial": "B",
                        "expression_score": 63,
                        "grade": "C+",
                        "n_events": 9,
                        "n_independent_seasons": 9,
                    },
                },
                "costs": {
                    "round_trip_bps": 36.9,
                    "note": "Mid-cap equity delivery — actual slippage likely worse than 36.9 bps"
                },
                "warnings": [
                    "RALLIS and CHAMBLFERT are cash-only mid-caps. Backtest slippage is optimistic.",
                    "IMD forecast (June) carries error vs final seasonal LPA — live edge softer.",
                    "unproven verdict (DSR 0.896 < 0.95). Deploy as a capped satellite only.",
                ],
                "disclaimer": (
                    "This is analysis, not financial advice. "
                    "Pivot registers orders; you confirm and place in your broker app."
                ),
            },
        ),
        # ── S3: M&M monsoon call spread (GRADE B, defined-risk overlay) ──────────
        ViewExpressionInput(
            tier="aggressive",
            expression_kind="option_strategy",
            rationale=(
                "M&M monthly bull call spread (50/30/20 laddered across Apr15-Sep30). "
                "Defined-risk debit spread — max loss = net premium, bounded upside. "
                "Capital-efficient; M&M monthly options liquid. Physical settlement → "
                "always square off before expiry. CAVEAT: backtest is a directional proxy "
                "(no theta/IV/premium-decay modelled). Trust verdict PROMISING on proxy, "
                "but option P&L unverified until real IV backtest."
            ),
            risk_profile=(
                "GRADE B. Defined-risk debit spread. Max loss = net debit ≈ 1-3% of M&M spot. "
                "Proxy backtest: Max DD −19.9%, prob_loss 0.000, PSR 1.0, DSR 0.999. "
                "CAVEAT: these metrics are for the underlying proxy — real option P&L will be "
                "lower due to theta and IV premium. Re-backtest on real chain before sizing up."
            ),
            capital_intensity=(
                "Low: net debit only (no margin). F&O account required. "
                "M&M monthly option chain — liquid. Laddered across 3 months."
            ),
            historical_strength=(
                "Proxy backtest 2010-2025, 16 seasonal events. Total return +405.3% (proxy). "
                "Max DD −19.9%, win-rate 87.5%. PSR 1.000, DSR 0.999. "
                "M&M monsoon-beta t≈1.1 (individual stock weak — p=0.271). "
                "Theta/premium decay NOT modelled → headline metrics are optimistic proxy."
            ),
            time_horizon="Apr 15 – Sep 30 (laddered 50/30/20 across 3 monthly expiries)",
            config={
                "schema_version": 1,
                "expression_kind": "option_strategy",
                "tier": "aggressive",
                "label": "Monsoon S3: M&M bull call spread overlay (defined-risk)",
                "instruments": [
                    {"symbol": "M&M", "exchange": "NFO", "segment": "NFO-OPT",
                     "instrument_type": "equity_option", "role": "underlying", "tradeable": True},
                ],
                "structure": {
                    "underlying": "M&M",
                    "template": "bull_call_spread",
                    "expiry_rule": "monthly",
                    "qty_lots": 1,
                    "note": (
                        "Laddered 50/30/20 across Apr-May-Jun monthly expiries. "
                        "Buy ATM call, sell higher-strike call. Net debit = max loss. "
                        "Always square off T-1 before expiry (physical settlement + STT). "
                        "Theta/IV NOT modelled in backtest — treat proxy metrics as directional only."
                    ),
                },
                "timing": {
                    "mode": "pre_position",
                    "tranches": [
                        {"pct": 50, "trigger": _schedule_trigger("2027-04-15T09:30:00")},
                        {"pct": 30, "trigger": _schedule_trigger("2027-05-15T09:30:00")},
                        {"pct": 20, "trigger": _schedule_trigger("2027-06-15T09:30:00")},
                    ],
                    "invalidation": None,
                    "note": "Laddered entry Apr15/May15/Jun15; all legs exit by Sep30.",
                },
                "scores": {
                    "construction_alignment": 71.0,
                    "alignment_kind": "event_study",
                    "backtest": {
                        "total_return_pct": 405.3,
                        "max_dd_pct": -19.9,
                        "win_rate": 0.875,
                        "psr": 1.000,
                        "dsr": 0.999,
                        "min_trl": 305,
                        "n_obs": 1868,
                        "min_trl_cleared": True,
                        "mc_dd_p95_pct": -26.1,
                        "mc_prob_loss": 0.000,
                        "concentration": 0.412,
                        "trust_verdict": "PROMISING",
                        "trust_conf": 100,
                        "outcome_dial": "A",
                        "outcome_score": 81,
                        "expression_dial": "B",
                        "expression_score": 71,
                        "grade": "B",
                        "caar_p": 0.271,
                        "n_events": 16,
                        "proxy_caveat": "Underlying directional proxy — theta/IV not modelled. Optimistic.",
                    },
                },
                "costs": {
                    "round_trip_bps": 23.3,
                    "note": "Options. STT-on-intrinsic on physical settlement — always square pre-expiry."
                },
                "warnings": [
                    "Proxy backtest — theta/premium decay NOT modelled. Real option P&L will be lower.",
                    "M&M individual monsoon signal is weak (p=0.271). Pair (S2) is the stronger expression.",
                    "Physical settlement: must square off before expiry to avoid STT-on-intrinsic.",
                    "Re-backtest on real IV chain (Kite connected) before sizing up.",
                ],
                "disclaimer": (
                    "This is analysis, not financial advice. Proxy metrics reflect directional payoff, "
                    "not real option P&L. Pivot registers orders; you confirm and place in your broker app."
                ),
            },
        ),
    ],
)


# ═══════════════════════════════════════════════════════════════════════════════
# VIEW 3 — Crude / Geopolitical shock (or peace deal)
# Best strategy: A (Importer-beneficiary basket, GRADE B, crude-DOWN)
# Also persist: B (ONGC vs BPCL pair, GRADE D — documented as no-edge)
#               C (MCX bull call spread, GRADE C — insurance proxy)
# NOTE: B and C are persisted for completeness / honest documentation.
#       They carry NO_EDGE trust verdict. They are NOT recommended for deployment.
# ═══════════════════════════════════════════════════════════════════════════════
VIEW3_PAYLOAD = MarketViewCreate(
    view_type="event",
    title="Crude / Geopolitical shock: de-escalation importer trade (crude-DOWN only)",
    thesis=(
        "2010-2026 backtest (4,272 rows, 67-71 triggered episodes): the de-escalation "
        "/ crude-DOWN leg (Brent 10d ≤ -8%) has a positive, cost-surviving Expression "
        "dial B/78 (CAAR alignment 80%, p=0.012, cost-survival 67%). The importer "
        "basket (ASIANPAINT/BERGEPAINT/INDIGO/HINDPETRO/BPCL/IOC) is the only "
        "placeable expression. Critical out-of-sample finding: the escalation/crude-UP "
        "leg (Strategies B and C) does NOT hold up — B is NO_EDGE (−42%, 78% bootstrap "
        "paths lose, loss_likely flag) and C is NO_EDGE convex insurance with near-zero "
        "standalone edge. ONLY deploy Strategy A (crude-DOWN / de-escalation satellite)."
    ),
    category="macro_commodity",
    time_horizon="~20-day episode hold per Brent-signal trigger (de-escalation / crude-DOWN only)",
    transmission=[],
    confidence=[
        ViewConfidenceInput(
            dimension="outcome",
            score=0.59,  # Strategy A OUTCOME SUPPRESSED (7 analogs); indicative C/59
            evidence=(
                "Outcome dial SUPPRESSED for Strategy A (only 7 independent crude-crash analogs "
                "« MinTRL). Indicative soft-blend C/59, hit-rate 52% (barely above coin-flip). "
                "Strategy B is NO_EDGE (D/39). Strategy C SUPPRESSED (D/39 indicative). "
                "Honest: the de-escalation basket edge is in the expression (macro alignment), "
                "not the event-hit rate."
            ),
        ),
        ViewConfidenceInput(
            dimension="expression",
            score=0.78,  # Strategy A EXPRESSION B/78
            evidence=(
                "Expression dial B/78 (Strategy A): CAAR alignment 80%, p=0.012, cost-survival 67%. "
                "This is the only non-suppressed, non-suppressed expression in the view. "
                "Strategy B Expression D/39 (cost-survival 0%). Strategy C SUPPRESSED."
            ),
        ),
    ],
    expressions=[
        # ── Strategy A: Importer basket (GRADE B, PRIMARY, crude-DOWN only) ───────
        ViewExpressionInput(
            tier="conservative",
            expression_kind="basket",
            rationale=(
                "Long-only NSE delivery basket: ASIANPAINT 22% / BERGEPAINT 18% / "
                "INDIGO 18% / HINDPETRO 16% / BPCL 14% / IOC 12%. "
                "Triggered by Brent 10-day return ≤ -8% (de-escalation / demand-slowdown). "
                "20-day hold. Liquid large-caps, no shorting, no F&O, no margin. "
                "Deploy as a CONFIRMATION satellite only (~10-15% sleeve)."
            ),
            risk_profile=(
                "GRADE B. Long-only delivery. Total return +131.39% trade-time (2010-2026). "
                "Max DD −24.98%, mc dd_p95 −41.5%, prob_loss 0.02. "
                "UNPROVEN verdict (conf=89, DSR 0.885 < 0.95, drawdown_risk flag). "
                "Calendar CAGR 5.17% vs NIFTY 10.32% (cash drag). Beats NIFTY only 52% "
                "of crude-down episodes. Deployable satellite; not a core holding."
            ),
            capital_intensity=(
                "Low: long-only CNC equity delivery. All names large/liquid NSE. "
                "Min ~₹1L for a 6-stock basket with meaningful exposure."
            ),
            historical_strength=(
                "Backtest 2010-2026, 67 crude-down episodes. Trade-time return +131.39%, "
                "Max DD −24.98%, win-rate 62.7%, PSR 0.980, DSR 0.885. "
                "MinTRL cleared (1328 > 851). Expression B/78 (CAAR p=0.012). "
                "Real trading_costs (36.9 bps). Real Trust Battery."
            ),
            time_horizon="~20 days per crude-down episode; flat otherwise",
            config={
                "schema_version": 1,
                "expression_kind": "basket",
                "tier": "conservative",
                "label": "Crude-Geo: Importer-beneficiary basket (crude-DOWN / de-escalation)",
                "instruments": [
                    {"symbol": "ASIANPAINT", "exchange": "NSE", "segment": "EQ",
                     "instrument_type": "equity", "role": "long", "tradeable": True},
                    {"symbol": "BERGEPAINT", "exchange": "NSE", "segment": "EQ",
                     "instrument_type": "equity", "role": "long", "tradeable": True},
                    {"symbol": "INDIGO", "exchange": "NSE", "segment": "EQ",
                     "instrument_type": "equity", "role": "long", "tradeable": True},
                    {"symbol": "HINDPETRO", "exchange": "NSE", "segment": "EQ",
                     "instrument_type": "equity", "role": "long", "tradeable": True},
                    {"symbol": "BPCL", "exchange": "NSE", "segment": "EQ",
                     "instrument_type": "equity", "role": "long", "tradeable": True},
                    {"symbol": "IOC", "exchange": "NSE", "segment": "EQ",
                     "instrument_type": "equity", "role": "long", "tradeable": True},
                ],
                "structure": {
                    "scheme": "custom_weight",
                    "weights": {
                        "ASIANPAINT": 0.22,
                        "BERGEPAINT": 0.18,
                        "INDIGO": 0.18,
                        "HINDPETRO": 0.16,
                        "BPCL": 0.14,
                        "IOC": 0.12,
                    },
                    "n_names": 6,
                    "single_name_cap": 0.25,
                },
                "timing": {
                    "mode": "confirmation",
                    "tranches": [{"pct": 100, "trigger": _manual_trigger()}],
                    "invalidation": None,
                    "note": (
                        "Fire when Brent 10-day return crosses ≤ -8%. "
                        "Hold 20 trading days. Exit and go flat. "
                        "Do NOT deploy on crude-UP / escalation signals."
                    ),
                },
                "scores": {
                    "construction_alignment": 78.0,
                    "alignment_kind": "event_study",
                    "backtest": {
                        "total_return_trade_time_pct": 131.39,
                        "total_return_calendar_pct": 127.59,
                        "calendar_cagr_pct": 5.17,
                        "nifty_cagr_pct": 10.32,
                        "max_dd_pct": -24.98,
                        "win_rate": 0.627,
                        "nifty_beat_rate": 0.52,
                        "psr": 0.980,
                        "dsr": 0.885,
                        "min_trl": 851,
                        "n_obs_trade_time": 1328,
                        "min_trl_cleared": True,
                        "mc_dd_p95_pct": -41.5,
                        "mc_prob_loss": 0.02,
                        "trust_verdict": "UNPROVEN",
                        "trust_flags": ["drawdown_risk"],
                        "trust_conf": 89,
                        "outcome_dial": "SUPPRESSED",
                        "outcome_score_indicative": 59,
                        "expression_dial": "B",
                        "expression_score": 78,
                        "grade": "B",
                        "caar_alignment_pct": 80,
                        "caar_p": 0.012,
                        "cost_survival": 0.67,
                        "n_episodes": 67,
                        "trigger": "Brent 10d return <= -8%",
                        "hold_days": 20,
                    },
                },
                "costs": {
                    "round_trip_bps": 36.9,
                    "note": "Equity delivery, 6-leg basket. Cost-survival 67% at 36.9 bps."
                },
                "warnings": [
                    "Calendar CAGR 5.17% < NIFTY 10.32% — cash drag when out of position.",
                    "Beats NIFTY only 52% of crude-down episodes (barely above coin-flip on hit-rate).",
                    "Outcome dial SUPPRESSED (only 7 independent analog shocks « MinTRL).",
                    "UNPROVEN verdict + drawdown_risk flag. Cap at 10-15% sleeve max.",
                    "DO NOT use for crude-UP / escalation signals — that leg is NO_EDGE.",
                ],
                "disclaimer": (
                    "This is analysis, not financial advice. "
                    "Crude-UP escalation strategies (B and C) are NO_EDGE — do not deploy. "
                    "Pivot registers orders; you confirm and place in your broker app."
                ),
            },
        ),
        # ── Strategy B: ONGC vs BPCL pair (GRADE D — NO_EDGE, documented only) ───
        ViewExpressionInput(
            tier="aggressive",
            expression_kind="pair",
            rationale=(
                "DOCUMENTED AS NO_EDGE — persisted for honest completeness. "
                "Long ONGC vs short BPCL-SSF on Brent 10d ≥ +8% (crude-UP / escalation). "
                "Analyst design report showed +37% on 2016 window; "
                "real 2010-2026 out-of-sample backtest returns −41.01%, 78% bootstrap "
                "paths end below water (loss_likely flag). The spread does not hold "
                "outside the 2022 Ukraine spike regime. DO NOT DEPLOY as alpha."
            ),
            risk_profile=(
                "GRADE D — NO_EDGE. DO NOT DEPLOY. "
                "Total return −41.01% (2010-2026). Max DD −78.25%. "
                "PSR 0.411, DSR 0.140, MinTRL undefined (negative Sharpe). "
                "mc dd_p95 −88.5%, prob_loss 0.784. Outcome D/39, Expression D/39. "
                "Critical out-of-sample divergence: analyst +37% → real −42%."
            ),
            capital_intensity=(
                "High: SSF short margin + equity long. NOT recommended — no demonstrable edge."
            ),
            historical_strength=(
                "Out-of-sample backtest 2010-2026, 71 episodes. Total return −41.01%. "
                "Win-rate 39.4%. PSR 0.411 < 0.5. NO_EDGE verdict. "
                "Edge was regime-clustered (2022 Ukraine spike only); bleeds in all other regimes."
            ),
            time_horizon="~20 days per crude-UP episode (NOT RECOMMENDED — NO_EDGE)",
            config={
                "schema_version": 1,
                "expression_kind": "pair",
                "tier": "aggressive",
                "label": "Crude-Geo: ONGC vs BPCL-SSF pair [GRADE D — NO_EDGE, do not deploy]",
                "instruments": [
                    {"symbol": "ONGC", "exchange": "NSE", "segment": "EQ",
                     "instrument_type": "equity", "role": "long", "tradeable": True},
                    {"symbol": "BPCL", "exchange": "NFO", "segment": "NFO-FUT",
                     "instrument_type": "ssf_future", "role": "short", "tradeable": True},
                ],
                "structure": {
                    "a": "ONGC",
                    "b": "BPCL",
                    "leg_a": {"notional": None, "note": "Long ONGC (NSE delivery)"},
                    "short_leg": {
                        "mode": "ssf_future",
                        "instrument": "BPCL",
                        "exchange": "NFO",
                        "note": (
                            "Short BPCL via SSF future (NFO). Beta = 0.854 (market-betas 0.915/1.072). "
                            "DOCUMENTED NO_EDGE — backtest −41%, 78% bootstrap paths lose. "
                            "DO NOT deploy this as an alpha bet."
                        ),
                    },
                    "beta": 0.854,
                    "no_edge_flag": True,
                    "rigor_tier": "event_study",
                },
                "timing": {
                    "mode": "confirmation",
                    "tranches": [{"pct": 100, "trigger": _manual_trigger()}],
                    "invalidation": None,
                    "note": "NO_EDGE — do not arm. Workflow draft exists for documentation only.",
                },
                "scores": {
                    "construction_alignment": 39.0,
                    "alignment_kind": "event_study",
                    "backtest": {
                        "total_return_pct": -41.01,
                        "max_dd_pct": -78.25,
                        "win_rate": 0.394,
                        "psr": 0.411,
                        "dsr": 0.140,
                        "min_trl": None,
                        "n_obs": 1404,
                        "mc_dd_p95_pct": -88.5,
                        "mc_prob_loss": 0.784,
                        "trust_verdict": "NO_EDGE",
                        "trust_flags": ["drawdown_risk", "loss_likely"],
                        "trust_conf": 41,
                        "outcome_dial": "D",
                        "outcome_score": 39,
                        "expression_dial": "D",
                        "expression_score": 39,
                        "grade": "D",
                        "n_episodes": 71,
                        "out_of_sample_divergence": "analyst +37% → real -41% (2022 regime cluster)",
                    },
                },
                "costs": {"round_trip_bps": 36.9, "note": "Not applicable — NO_EDGE, do not deploy"},
                "warnings": [
                    "NO_EDGE — loss_likely. PSR 0.411, prob_loss 0.784. DO NOT DEPLOY.",
                    "Out-of-sample divergence: analyst +37% → real -41.01%.",
                    "Edge was regime-clustered (2022 Ukraine spike only).",
                    "This draft is stored for honest documentation, NOT for trading.",
                ],
                "disclaimer": (
                    "GRADE D — NO_EDGE. This strategy loses money out-of-sample. "
                    "Pivot registers this as a DRAFT ONLY for documentation. Do not arm."
                ),
            },
        ),
        # ── Strategy C: MCX crude call spread (GRADE C — convex insurance proxy) ─
        ViewExpressionInput(
            tier="balanced",
            expression_kind="option_strategy",
            rationale=(
                "MCX CRUDEOIL bull call spread (ATM+higher strike, 1:1.5 debit). "
                "Small convex sleeve (1-3%) as escalation insurance. "
                "CAVEAT: no historical MCX CRUDEOIL option chain on yfinance — "
                "backtest is a Brent-underlying defined-risk PAYOFF PROXY only. "
                "Standalone edge is NO_EDGE on the proxy. Worth it as tiny insurance "
                "only, NOT as alpha. Re-validate on live MCX chain before any sizing."
            ),
            risk_profile=(
                "GRADE C. Proxy backtest NO_EDGE (total return −0.99%, Max DD −58.03%). "
                "Strongly negative skew (−1.3 to −2.2) confirms tail-insurance nature. "
                "PSR 0.593, DSR 0.268, MinTRL 69,615 (astronomically large). "
                "MCX CRUDEOIL now tradeable; lot 100 bbl; needs live chain for real fills. "
                "Treat as insurance premium — expect most spreads to expire worthless."
            ),
            capital_intensity=(
                "Low: net debit only (1-3% of portfolio as insurance). "
                "MCX CRUDEOIL option: lot 100 bbl. Kite connection required for live fills."
            ),
            historical_strength=(
                "Proxy backtest 2010-2026, 71 escalation episodes. Total return (proxy) −0.99%. "
                "Win-rate 54.9%. PSR 0.593. NO_EDGE verdict. "
                "Real MCX CRUDEOIL option chain backtest UNAVAILABLE on yfinance. "
                "Only the directional defined-risk payoff shape is real — theta/IV not modelled."
            ),
            time_horizon="~20 days per crude-UP episode; size as insurance (1-3% max)",
            config={
                "schema_version": 1,
                "expression_kind": "option_strategy",
                "tier": "balanced",
                "label": "Crude-Geo: MCX CRUDEOIL bull call spread [GRADE C — proxy insurance]",
                "instruments": [
                    {"symbol": "CRUDEOIL", "exchange": "MCX", "segment": "MCX-OPT",
                     "instrument_type": "commodity_option", "role": "underlying", "tradeable": True},
                ],
                "structure": {
                    "underlying": "CRUDEOIL",
                    "template": "bull_call_spread",
                    "expiry_rule": "nearest",
                    "qty_lots": 1,
                    "leverage_note": (
                        "MCX CRUDEOIL option: lot size 100 bbl; leveraged. "
                        "Size as 1-3% insurance sleeve only. Never auto-sized."
                    ),
                    "note": (
                        "ATM call + short higher-strike call. Net debit = max loss. "
                        "MCX tradeable (2026). Kite connection required for real fills. "
                        "Proxy backtest only — real P&L depends on IV/theta. "
                        "Treat as convex tail insurance, not alpha."
                    ),
                },
                "timing": {
                    "mode": "confirmation",
                    "tranches": [{"pct": 100, "trigger": _manual_trigger()}],
                    "invalidation": None,
                    "note": "Open on rising geopolitical risk / crude-UP signals. Size 1-3% max.",
                },
                "scores": {
                    "construction_alignment": 66.0,
                    "alignment_kind": "event_study",
                    "backtest": {
                        "total_return_proxy_pct": -0.99,
                        "max_dd_pct": -58.03,
                        "win_rate": 0.549,
                        "psr": 0.593,
                        "dsr": 0.268,
                        "min_trl": 69615,
                        "n_obs": 1419,
                        "min_trl_cleared": False,
                        "mc_dd_p95_pct": -71.1,
                        "mc_prob_loss": 0.494,
                        "trust_verdict": "NO_EDGE",
                        "trust_flags": ["drawdown_risk"],
                        "trust_conf": 59,
                        "outcome_dial": "SUPPRESSED",
                        "expression_dial": "SUPPRESSED",
                        "grade": "C",
                        "n_episodes": 71,
                        "skew": -1.30,
                        "proxy_caveat": "No MCX CRUDEOIL history on yfinance — payoff proxy only.",
                    },
                },
                "costs": {
                    "round_trip_bps": 23.3,
                    "note": "MCX option round-trip. Leverage note mandatory. Kite required for live fills."
                },
                "warnings": [
                    "Proxy backtest only — MCX CRUDEOIL historical option chain unavailable.",
                    "NO_EDGE on proxy. Cost-survival 0% (both dials SUPPRESSED).",
                    "Strongly negative skew (−1.3 to −2.2): mostly small losses, rare large gains.",
                    "Re-validate on live MCX chain (Kite connected) before any sizing.",
                    "Treat as tiny convex insurance (1-3%), not alpha.",
                ],
                "disclaimer": (
                    "MCX commodity option: leveraged instrument. "
                    "This is analysis, not financial advice. "
                    "Proxy metrics reflect payoff shape only — real P&L will differ. "
                    "Pivot registers orders; you confirm and place in your broker app."
                ),
            },
        ),
    ],
)


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN: persist all three views + deploy placeable strategies
# ═══════════════════════════════════════════════════════════════════════════════

def main() -> None:
    db = SessionLocal()
    try:
        results = []

        # ── VIEW 1: IT trouble ────────────────────────────────────────────────
        print("Creating View 1 (IT trouble)...")
        # Save and clear child lists to avoid bulk-insert sentinel bug
        v1_exprs_input = VIEW1_PAYLOAD.expressions
        v1_conf_input = VIEW1_PAYLOAD.confidence
        VIEW1_PAYLOAD.expressions = []
        VIEW1_PAYLOAD.confidence = []
        v1 = curation.create_view(db, VIEW1_PAYLOAD, user_id=None)
        db.flush()
        _add_transmission_edges(db, v1.id, [
            ViewTransmissionInput(
                seq=0, from_node="IT_weak_guidance",
                to_node="defensive_outperformance",
                edge_label="margin_compression → defensive_rotation",
                strength=0.6,
                evidence="CAAR event-study: defensive +0.60% t=2.00 p=0.046 on 8 analogs"
            ),
            ViewTransmissionInput(
                seq=1, from_node="IT_weak_guidance",
                to_node="INFY_downside",
                edge_label="IT_sector_drag",
                strength=0.4,
                evidence="INFY instrument CAAR -0.82% but t=-0.46 p=0.65 (not significant)"
            ),
        ])
        # Add expressions and confidence one-by-one
        for c in v1_conf_input:
            _add_confidence(db, v1.id, c)
        expr_rows_v1 = [_add_expression(db, v1.id, e) for e in v1_exprs_input]
        curation.publish_view(db, v1.id, force=True)  # published_at set
        db.commit()
        db.refresh(v1)
        print(f"  view_id = {v1.id}")

        # Map by tier
        v1_expr_c = next(e for e in expr_rows_v1 if str(getattr(e.tier, 'value', e.tier)) == "conservative")
        v1_expr_b = next(e for e in expr_rows_v1 if str(getattr(e.tier, 'value', e.tier)) == "balanced")
        v1_expr_a = next(e for e in expr_rows_v1 if str(getattr(e.tier, 'value', e.tier)) == "aggressive")

        # Deploy placeable strategies (A, B, C all placeable; B needs F&O)
        print("  Deploying IT Strategy C (defensive basket)...")
        d1c = deploy_expression(db, v1_expr_c, activate=ACTIVATED, user_id=USER_ID)
        db.commit()
        print(f"  IT-C workflow_id = {d1c['workflow_id']}")

        print("  Deploying IT Strategy B (FMCG vs IT-SSF pair)...")
        d1b = deploy_expression(db, v1_expr_b, activate=ACTIVATED, user_id=USER_ID)
        db.commit()
        print(f"  IT-B workflow_id = {d1b['workflow_id']}")

        print("  Deploying IT Strategy A (INFY bear put spread)...")
        d1a = deploy_expression(db, v1_expr_a, activate=ACTIVATED, user_id=USER_ID)
        db.commit()
        print(f"  IT-A workflow_id = {d1a['workflow_id']}")

        results.append(("IT trouble",   "C: Defensive FMCG basket", str(v1.id), str(v1_expr_c.id), d1c["workflow_id"], "A-", "Long-only CNC NESTLEIND+HUL+ITC+DABUR. Arm manually on confirmed weak guidance."))
        results.append(("IT trouble",   "B: FMCG vs IT-SSF pair",   str(v1.id), str(v1_expr_b.id), d1b["workflow_id"], "B",  "Long NESTLE+HUL, short HCLTECH-SSF. F&O acct. Arm manually."))
        results.append(("IT trouble",   "A: INFY bear put spread",   str(v1.id), str(v1_expr_a.id), d1a["workflow_id"], "C",  "1-lot INFY monthly bear put spread. T-3 before TCS print. Square pre-expiry."))

        # ── VIEW 2: Monsoon ───────────────────────────────────────────────────
        print("Creating View 2 (Monsoon)...")
        v2_exprs_input = VIEW2_PAYLOAD.expressions
        v2_conf_input = VIEW2_PAYLOAD.confidence
        VIEW2_PAYLOAD.expressions = []
        VIEW2_PAYLOAD.confidence = []
        v2 = curation.create_view(db, VIEW2_PAYLOAD, user_id=None)
        db.flush()
        _add_transmission_edges(db, v2.id, [
            ViewTransmissionInput(
                seq=0, from_node="IMD_monsoon_forecast",
                to_node="rural_discretionary_demand",
                edge_label="sowing_intent -> tractor+2W demand",
                strength=0.75,
                evidence="Forecast run-up: CAAR +7.46% t=2.83 p=0.005 (Apr15-Jun15, 16yr)"
            ),
            ViewTransmissionInput(
                seq=1, from_node="normal_monsoon_LPA",
                to_node="agri_input_demand",
                edge_label="LPA>=96 -> fertiliser+agrochemical spend",
                strength=0.60,
                evidence="Agri-input basket: 7/9 wins in confirmed-normal years (IMD LPA>=96)"
            ),
            ViewTransmissionInput(
                seq=2, from_node="rural_discretionary_demand",
                to_node="M&M_stock_performance",
                edge_label="rural_income -> M&M tractor segment",
                strength=0.55,
                evidence="M&M monsoon-beta t=1.1 (weak individual stock signal; pair strips it)"
            ),
        ])
        for c in v2_conf_input:
            _add_confidence(db, v2.id, c)
        expr_rows_v2 = [_add_expression(db, v2.id, e) for e in v2_exprs_input]
        curation.publish_view(db, v2.id, force=True)
        db.commit()
        db.refresh(v2)
        print(f"  view_id = {v2.id}")

        v2_expr_s2 = next(e for e in expr_rows_v2 if str(getattr(e.tier, 'value', e.tier)) == "balanced")
        v2_expr_s1 = next(e for e in expr_rows_v2 if str(getattr(e.tier, 'value', e.tier)) == "conservative")
        v2_expr_s3 = next(e for e in expr_rows_v2 if str(getattr(e.tier, 'value', e.tier)) == "aggressive")

        print("  Deploying Monsoon S2 (Tractor/2W pair)...")
        d2s2 = deploy_expression(db, v2_expr_s2, activate=ACTIVATED, user_id=USER_ID)
        db.commit()
        print(f"  Monsoon-S2 workflow_id = {d2s2['workflow_id']}")

        print("  Deploying Monsoon S1 (agri-input basket)...")
        d2s1 = deploy_expression(db, v2_expr_s1, activate=ACTIVATED, user_id=USER_ID)
        db.commit()
        print(f"  Monsoon-S1 workflow_id = {d2s1['workflow_id']}")

        print("  Deploying Monsoon S3 (M&M call spread)...")
        d2s3 = deploy_expression(db, v2_expr_s3, activate=ACTIVATED, user_id=USER_ID)
        db.commit()
        print(f"  Monsoon-S3 workflow_id = {d2s3['workflow_id']}")

        results.append(("Monsoon", "S2: Tractor/2W vs NIFTY pair (core)", str(v2.id), str(v2_expr_s2.id), d2s2["workflow_id"], "A-", "Long M&M+TVSMOTOR, short NIFTY future (beta-neutral). Apr15-Jun15 annually."))
        results.append(("Monsoon", "S1: Agri-input basket (IMD-gate)",    str(v2.id), str(v2_expr_s1.id), d2s1["workflow_id"], "C+", "EW COROMANDEL+UPL+RALLIS+PIIND+CHAMBLFERT. Jun-Aug, only on IMD LPA>=96."))
        results.append(("Monsoon", "S3: M&M bull call spread (overlay)",  str(v2.id), str(v2_expr_s3.id), d2s3["workflow_id"], "B",  "M&M monthly bull call spread, 50/30/20 laddered Apr-Jun. Proxy only."))

        # ── VIEW 3: Crude / Geo ───────────────────────────────────────────────
        print("Creating View 3 (Crude/Geo)...")
        v3_exprs_input = VIEW3_PAYLOAD.expressions
        v3_conf_input = VIEW3_PAYLOAD.confidence
        VIEW3_PAYLOAD.expressions = []
        VIEW3_PAYLOAD.confidence = []
        v3 = curation.create_view(db, VIEW3_PAYLOAD, user_id=None)
        db.flush()
        _add_transmission_edges(db, v3.id, [
            ViewTransmissionInput(
                seq=0, from_node="Brent_crude_decline_10d_minus8pct",
                to_node="importer_margin_expansion",
                edge_label="lower_input_costs -> margin",
                strength=0.65,
                evidence="Expression B/78: CAAR alignment 80%, p=0.012, cost-survival 67%. 67 episodes 2010-2026."
            ),
            ViewTransmissionInput(
                seq=1, from_node="geopolitical_de_escalation",
                to_node="Brent_crude_decline_10d_minus8pct",
                edge_label="risk_premium_unwind",
                strength=0.50,
                evidence="Geopolitical triggers are one pathway; demand slowdown / supply glut also fire the signal."
            ),
        ])
        for c in v3_conf_input:
            _add_confidence(db, v3.id, c)
        expr_rows_v3 = [_add_expression(db, v3.id, e) for e in v3_exprs_input]
        curation.publish_view(db, v3.id, force=True)
        db.commit()
        db.refresh(v3)
        print(f"  view_id = {v3.id}")

        v3_expr_a = next(e for e in expr_rows_v3 if str(getattr(e.tier, 'value', e.tier)) == "conservative")
        v3_expr_b = next(e for e in expr_rows_v3 if str(getattr(e.tier, 'value', e.tier)) == "aggressive")
        v3_expr_c = next(e for e in expr_rows_v3 if str(getattr(e.tier, 'value', e.tier)) == "balanced")

        print("  Deploying Crude A (importer basket)...")
        d3a = deploy_expression(db, v3_expr_a, activate=ACTIVATED, user_id=USER_ID)
        db.commit()
        print(f"  Crude-A workflow_id = {d3a['workflow_id']}")

        print("  Deploying Crude B (ONGC vs BPCL — NO_EDGE, documented)...")
        d3b = deploy_expression(db, v3_expr_b, activate=ACTIVATED, user_id=USER_ID)
        db.commit()
        print(f"  Crude-B workflow_id = {d3b['workflow_id']}")

        print("  Deploying Crude C (MCX call spread proxy)...")
        d3c = deploy_expression(db, v3_expr_c, activate=ACTIVATED, user_id=USER_ID)
        db.commit()
        print(f"  Crude-C workflow_id = {d3c['workflow_id']}")

        results.append(("Crude/Geo", "A: Importer basket (crude-DOWN)",         str(v3.id), str(v3_expr_a.id), d3a["workflow_id"], "B",  "Long ASIANPAINT+BERGEPAINT+INDIGO+HINDPETRO+BPCL+IOC. Fire on Brent 10d<=-8%."))
        results.append(("Crude/Geo", "B: ONGC vs BPCL pair [NO_EDGE, docs only]", str(v3.id), str(v3_expr_b.id), d3b["workflow_id"], "D",  "NO_EDGE. DO NOT ARM. Stored for honest documentation only."))
        results.append(("Crude/Geo", "C: MCX crude call spread [proxy insurance]", str(v3.id), str(v3_expr_c.id), d3c["workflow_id"], "C",  "MCX CRUDEOIL bull call spread. 1-3% insurance sleeve. Validate on live chain first."))

        # ── Final summary ─────────────────────────────────────────────────────
        print("\n" + "="*100)
        print("PERSISTENCE COMPLETE — register-not-execute (NO order placed, NO activation)")
        print("="*100)
        print(f"{'View':<15} {'Strategy':<42} {'view_id':<38} {'expr_id':<38} {'wf_draft_id':<38} {'Grade':<6} {'How to place'}")
        print("-"*260)
        for view, strat, vid, eid, wfid, grade, how in results:
            print(f"{view:<15} {strat:<42} {vid:<38} {eid:<38} {wfid:<38} {grade:<6} {how}")

        print("\nAll workflow drafts are status=draft (NOT active, NOT executed).")
        print("All order steps carry requires_approval=True.")
        print("register_not_execute=True on every deploy result.")
        print(f"\nViews published at: {datetime.now(timezone.utc).isoformat()}")
        print("\nACTION NEEDED TO PLACE:")
        print("  1. Connect Kite (re-login via scripts/kite_connect.py or FE button)")
        print("  2. Open the chat or views panel, find the view draft")
        print("  3. Confirm + place each order leg in your Zerodha broker app")
        print("  4. Monsoon S2: Set Apr15 schedule trigger when next season approaches")

    except Exception as exc:
        db.rollback()
        print(f"\nFAILED: {exc}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
