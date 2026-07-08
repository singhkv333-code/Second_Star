"""Deploy refined IT + crude strategies as new ViewExpression rows + workflow drafts.

IT view: adds R1 (Auto vs IT pair), R2 (Defence+Auto basket), R3 (IT bear put + GoldBeES)
Crude view: adds RC1 (refined importer basket, paints-heavy, genuine connectedness)

All new expressions are labeled "v2 / top-gainer-grounded" to distinguish from prior ones.
NO commit/push. NO server. NO activation. Additive only.
Requires user_id to own the workflow drafts — defaults to 1 (the founding curator).
"""
from __future__ import annotations
import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from backend.database import SessionLocal
from backend.models import MarketView, ViewExpression
from backend.schemas import ViewExpressionInput
from backend.view_markets.curation import attach_expressions
from backend.view_markets.deployment.deploy import deploy_expression
from backend import services  # noqa — trigger imports

# ── Target views ─────────────────────────────────────────────────────────────
IT_VIEW_ID   = "4f40f896-0953-4d66-bf6f-1932667b531e"
CRUDE_VIEW_ID = "19f04e99-b704-4166-b99a-697049885d44"

# Workflow owner — founding curator / admin user
OWNER_USER_ID = 1

# ── Config helpers ────────────────────────────────────────────────────────────
def _timing_manual(note: str) -> dict:
    return {
        "mode": "pre_position",
        "note": note,
        "tranches": [{"pct": 100, "trigger": {"step_type": "trigger.manual", "config": {}}}],
        "invalidation": None,
    }


DISCLAIMER = (
    "This is analysis, not financial advice. "
    "Pivot registers the trigger; you confirm and place each order in your broker app."
)

# ══════════════════════════════════════════════════════════════════════════════
# IT R1 — Long-Auto vs Short-Nifty-IT market-neutral pair  (GRADE B+)
# Backtest: total +28.73% / excess +30.32% vs NIFTY, maxDD −6.09%,
#           n_obs=184, PSR=0.988, DSR=0.918, MinTRL=99.5 < 184 ✅
#           verdict=UNPROVEN conf=92, hit-rate 75%, CAAR +3.26% p=0.011
#           Outcome B(73) / Expression B(79)
# ══════════════════════════════════════════════════════════════════════════════
IT_R1 = ViewExpressionInput(
    tier="balanced",
    expression_kind="pair",
    rationale=(
        "v2 / top-gainer-grounded: Auto sector (TVSMOTOR b_it=−0.22 t=−5.7, "
        "EICHERMOT b_it=−0.22 t=−6.3) is the strongest genuine negative-IT loader "
        "in the equity universe (OLS, 1,753 obs). Long Nifty-Auto vs Short Nifty-IT "
        "(bear put spread / index future) captures the sector rotation embedded in weak-IT "
        "guidance cycles. 8 TCS-anchored analogs, next-bar fills, one-bar lag, "
        "pre-event beta-hedge, real trading_costs. Prior FMCG long leg (b_it≈−0.05) "
        "replaced by Auto (3× return improvement)."
    ),
    risk_profile=(
        "Two-sided market-neutral: long Nifty-Auto ETF (ITBEES substitute or Auto basket) "
        "+ short Nifty-IT (index future or IT bear put spread). MaxDD −6.09%. "
        "F&O-enabled retail only. Short leg is index-future proxy (basis/roll/SPAN-carry "
        "not modelled). Defined-risk variant: replace short-IT future with IT bear put spread."
    ),
    capital_intensity=(
        "Medium — long-only equity leg (delivery CNC) + margin for short IT index future "
        "OR net debit for IT bear put spread. No auto-sizing; user sets lot/qty at approval."
    ),
    historical_strength=(
        "8 events / 184 obs. PSR=0.988, DSR=0.918, MinTRL=99.5 ✅ (cleared). "
        "UNPROVEN verdict conf=92. Hit-rate 75%, CAAR +3.26% t=2.55 p=0.011 (significant). "
        "Outcome dial B(73) / Expression dial B(79). Sub-periods all positive. MC prob_loss low."
    ),
    time_horizon="5–20 trading days per guidance-cycle event (HOLD=23 bars in backtest).",
    config={
        "schema_version": 1,
        "label": "IT-Trouble R1 v2: Long-Auto vs Short-IT pair (top-gainer-grounded)",
        "tier": "balanced",
        "expression_kind": "pair",
        "instruments": [
            {
                "symbol": "^CNXAUTO",
                "exchange": "NSE",
                "segment": "INDEX",
                "instrument_type": "index",
                "role": "long",
                "tradeable": True,
                "note": "Nifty Auto index proxy — deploy as Nifty Auto ETF or Auto basket (TVSMOTOR/EICHERMOT/MARUTI/BAJAJ-AUTO/HEROMOTOCO)"
            },
            {
                "symbol": "^CNXIT",
                "exchange": "NSE",
                "segment": "INDEX",
                "instrument_type": "index",
                "role": "short",
                "tradeable": True,
                "note": "Nifty IT index — deploy as ITBEES short via index future (NFO) or IT bear put spread"
            },
        ],
        "structure": {
            "a": "^CNXAUTO",
            "b": "^CNXIT",
            "short_leg": {
                "mode": "index_future",
                "instrument": "NIFTY IT",
                "note": (
                    "Short Nifty-IT via NSE index future (honest short, F&O-enabled retail). "
                    "Defined-risk alternative: IT bear put spread (buy ATM put, sell OTM put, "
                    "expiry nearest monthly). Short leg is backtested as index return proxy; "
                    "basis/roll/SPAN-carry not modelled — net backtest is OPTIMISTIC."
                ),
            },
            "leg_a": {"notional": None},  # user sizes at approval
            "residual_beta": 0.0,
            "rigor_tier": "beta_neutral_pair",
        },
        "timing": _timing_manual(
            "Fire manually when TCS/INFY/WIPRO quarterly guidance confirms weak outlook. "
            "Pre-position entry 5–10 days before the print for pre-event staging."
        ),
        "scores": {
            "alignment_kind": "event_study",
            "construction_alignment": 79.0,
            "backtest": {
                "grade": "B+",
                "trust_verdict": "UNPROVEN",
                "trust_conf": 92,
                "n_events": 8,
                "n_obs": 184,
                "win_rate": 0.75,
                "caar_pct": 3.26,
                "caar_t": 2.55,
                "caar_p": 0.011,
                "total_return_pct": 28.73,
                "excess_return_pct": 30.32,
                "max_dd_pct": -6.09,
                "psr": 0.988,
                "dsr": 0.918,
                "min_trl": 99.5,
                "min_trl_cleared": True,
                "outcome_dial": "B",
                "outcome_score": 73,
                "expression_dial": "B",
                "expression_score": 79,
                "mc_prob_loss": None,  # sub-threshold
                "sub_period_pos_frac": 1.0,
                "version": "v2_top_gainer_grounded",
                "prior_version": "B (FMCG vs IT, outcome B/73 expression SUPPRESSED)",
            },
        },
        "warnings": [
            "UNPROVEN verdict — only 8 analog events (small-N); do not scale until N>20.",
            "Short leg is index-future proxy; basis/roll/SPAN-carry not modelled.",
            "F&O-enabled retail only (requires margin/SPAN for the short leg).",
            "v2 top-gainer-grounded: Auto replaces FMCG as the long leg (genuine IT-loading).",
        ],
        "disclaimer": DISCLAIMER,
        "expressability": {
            "symmetric": False,
            "degraded": False,
            "short_mode": "index_future",
            "notes": ["Short leg via NSE Nifty IT index future — honest short, F&O-eligible."],
        },
        "costs": {
            "round_trip_bps": 37.0,
            "note": "Two round-trips (long equity + short index future) per episode",
        },
    },
)


# ══════════════════════════════════════════════════════════════════════════════
# IT R2 — Defence+Auto domestic-demand long-only basket  (GRADE A — best pick)
# Backtest: total +46.03% / excess +47.62% vs NIFTY, maxDD −3.33%,
#           n_obs=184, PSR=0.9998, DSR=0.9964, MinTRL=40.5 < 184 ✅
#           verdict=PROMISING conf=100, hit-rate 100%, CAAR +4.90% t=3.84 p≈0.000
#           Outcome A(91) / Expression A(97) — both dials A
# ══════════════════════════════════════════════════════════════════════════════
IT_R2 = ViewExpressionInput(
    tier="conservative",
    expression_kind="basket",
    rationale=(
        "v2 / top-gainer-grounded: concentrates capital in the two sectors that GENUINELY "
        "load against the IT factor (Auto 40%: TVSMOTOR b_it=−0.22 t=−5.7, EICHERMOT b_it=−0.22 t=−6.3; "
        "Defence/BEL 35%: b_it=−0.20 t=−4.2) with MARICO (genuine abn-CAAR +3.89%, b_it negative) "
        "as 25% ballast. Excludes all spurious top-15 names (ONGC/VEDL/COCHINSHIP had no significant "
        "IT loading). Prior FMCG-only design had b_it≈−0.05 = weak IT loading = market-beta drift. "
        "8 TCS-anchored analogs, next-bar fills, real trading_costs."
    ),
    risk_profile=(
        "Long-only CNC delivery basket — no short/margin/lot-size/SEBI friction. "
        "Most capital-light and retail-placeable of the three refined designs. "
        "MaxDD −3.33% (lowest of all three refined strategies). MC prob_loss = 0.0."
    ),
    capital_intensity=(
        "Low-medium — equity CNC delivery only, no F&O required. "
        "Minimum 5–6 stocks across 3 sectors. User sizes via total_inr at approval."
    ),
    historical_strength=(
        "8 events / 184 obs. PSR=0.9998, DSR=0.9964, MinTRL=40.5 ✅ (cleared comfortably). "
        "PROMISING verdict conf=100 (only refined design to reach PROMISING class). "
        "8/8 hit-rate (100%), CAAR +4.90% t=3.84 p≈0.000. All four sub-periods positive "
        "(12.5% / 8.35% / 7.6% / 11.3%). MC prob_loss=0.0. "
        "Both dials A: Outcome A(91) / Expression A(97). GRADE A."
    ),
    time_horizon="15–25 trading days per guidance-cycle event (23-bar HOLD in backtest).",
    config={
        "schema_version": 1,
        "label": "IT-Trouble R2 v2: Defence+Auto domestic-demand basket (top-gainer-grounded)",
        "tier": "conservative",
        "expression_kind": "basket",
        "instruments": [
            # Auto (40%)
            {"symbol": "TVSMOTOR.NS", "exchange": "NSE", "segment": "EQ",
             "instrument_type": "equity", "role": "long", "tradeable": True,
             "note": "b_it=−0.22 t=−5.7, genuine IT-negative loader"},
            {"symbol": "EICHERMOT.NS", "exchange": "NSE", "segment": "EQ",
             "instrument_type": "equity", "role": "long", "tradeable": True,
             "note": "b_it=−0.22 t=−6.3, genuine IT-negative loader"},
            {"symbol": "MARUTI.NS", "exchange": "NSE", "segment": "EQ",
             "instrument_type": "equity", "role": "long", "tradeable": True,
             "note": "Nifty Auto constituent, IT-negative sector"},
            {"symbol": "BAJAJ-AUTO.NS", "exchange": "NSE", "segment": "EQ",
             "instrument_type": "equity", "role": "long", "tradeable": True,
             "note": "Nifty Auto constituent, IT-negative sector"},
            # Defence (35%)
            {"symbol": "BEL.NS", "exchange": "NSE", "segment": "EQ",
             "instrument_type": "equity", "role": "long", "tradeable": True,
             "note": "b_it=−0.20 t=−4.2, genuine IT-negative loader, domestic defence"},
            {"symbol": "HAL.NS", "exchange": "NSE", "segment": "EQ",
             "instrument_type": "equity", "role": "long", "tradeable": True,
             "note": "Defence sector, domestic demand — IT-uncorrelated"},
            # FMCG ballast (25%)
            {"symbol": "MARICO.NS", "exchange": "NSE", "segment": "EQ",
             "instrument_type": "equity", "role": "long", "tradeable": True,
             "note": "Genuine abn-CAAR +3.89% on IT weak-print analogs; b_it negative; 25% ballast"},
        ],
        "structure": {
            "scheme": "conviction_weight",
            "weights": {
                # Auto 40% split 4 ways = 10% each
                "TVSMOTOR.NS": 0.12,
                "EICHERMOT.NS": 0.12,
                "MARUTI.NS": 0.08,
                "BAJAJ-AUTO.NS": 0.08,
                # Defence 35% split 2 ways
                "BEL.NS": 0.20,
                "HAL.NS": 0.15,
                # FMCG ballast 25%
                "MARICO.NS": 0.25,
            },
            "n_names": 7,
            "single_name_cap": 0.25,
            "basket_purity": 0.91,
            "min_names": 5,
        },
        "timing": _timing_manual(
            "Fire manually when TCS/INFY/WIPRO quarterly guidance confirms weak outlook. "
            "Long-only delivery: execute as a single batch across the 7 names at CNC."
        ),
        "scores": {
            "alignment_kind": "event_study",
            "construction_alignment": 97.0,
            "backtest": {
                "grade": "A",
                "trust_verdict": "PROMISING",
                "trust_conf": 100,
                "n_events": 8,
                "n_obs": 184,
                "win_rate": 1.0,
                "caar_pct": 4.90,
                "caar_t": 3.84,
                "caar_p": 0.0001,
                "total_return_pct": 46.03,
                "excess_return_pct": 47.62,
                "max_dd_pct": -3.33,
                "psr": 0.9998,
                "dsr": 0.9964,
                "min_trl": 40.5,
                "min_trl_cleared": True,
                "outcome_dial": "A",
                "outcome_score": 91,
                "expression_dial": "A",
                "expression_score": 97,
                "mc_prob_loss": 0.0,
                "sub_period_pos_frac": 1.0,
                "sub_period_returns_pct": [12.5, 8.35, 7.6, 11.3],
                "version": "v2_top_gainer_grounded",
                "prior_version": "C (FMCG-only basket, UNPROVEN conf=43, both dials suppressed/B)",
            },
        },
        "warnings": [
            "PROMISING but only 8 analog events — do not over-size; treat as pilot position.",
            "Excludes ONGC/VEDL/COCHINSHIP (spurious IT loading — no significant OLS t-stat).",
            "v2 top-gainer-grounded: Auto+Defence replace pure-FMCG (genuine IT-negative loading).",
        ],
        "disclaimer": DISCLAIMER,
        "expressability": {
            "symmetric": False,
            "degraded": False,
            "short_mode": None,
            "notes": ["Long-only CNC delivery. No short/margin required."],
        },
        "costs": {
            "round_trip_bps": 36.9,
            "note": "Equity delivery round-trip per episode across 7 names",
        },
    },
)


# ══════════════════════════════════════════════════════════════════════════════
# IT R3 — Nifty-IT bear put spread + GoldBeES risk-off hedge  (GRADE B)
# Backtest: total +13.16% / excess +19.17% vs NIFTY, maxDD −2.70%,
#           n_obs=72 (option windows), PSR=0.989, DSR=0.924, MinTRL=37.8 < 72 ✅
#           verdict=UNPROVEN conf=92, hit-rate 75%, CAAR +1.58% t=2.11 p=0.035
#           Outcome B(73) / Expression B(71)
# ══════════════════════════════════════════════════════════════════════════════
IT_R3 = ViewExpressionInput(
    tier="aggressive",
    expression_kind="option_strategy",
    rationale=(
        "v2 / top-gainer-grounded: moves the bearish leg from single-stock INFY "
        "(react CAAR −0.82% t=−0.46 p=0.646, insignificant) to NIFTY IT INDEX "
        "(reliably bottom-ranked across all analogs, mean −1.13%) and pairs it with "
        "GoldBeES — the ONLY equity-listed instrument with top-6 hit-freq=0.62 "
        "that has ZERO IT-factor or INR/FX loading (b_it=+0.03 t=0.08 = independent "
        "risk-off carry, 1,234 obs). Together: defined-risk bearish sleeve on IT + "
        "independent risk-off hedge. 8 analogs, 72 option-window obs."
    ),
    risk_profile=(
        "Net debit = maximum loss is the premium paid (defined-risk). "
        "Bear put spread on NIFTY IT: buy ATM put, sell OTM put (e.g. 5% below ATM), "
        "nearest monthly expiry. GoldBeES sleeve = plain ETF CNC (no leverage). "
        "MaxDD −2.70%. Capital-light: net debit + ETF, no naked short / margin required."
    ),
    capital_intensity=(
        "Low — bear put spread debit (NFO-OPT, 1+ lots) + GoldBeES ETF allocation. "
        "User sets lot size and ETF allocation at approval. No auto-sizing."
    ),
    historical_strength=(
        "8 events / 72 option-window obs. PSR=0.989, DSR=0.924, MinTRL=37.8 ✅ (cleared). "
        "UNPROVEN verdict conf=92. Hit-rate 75%, CAAR +1.58% t=2.11 p=0.035 (significant, "
        "prior INFY strategy: CAAR −0.82% t=−0.46 p=0.646 insignificant). "
        "Outcome B(73) / Expression B(71). Option leg is delta-proxy — theta/vega/STT "
        "NOT modelled; treat as directional skeleton (optimistic). GRADE B."
    ),
    time_horizon="10–20 trading days per event (option expiry nearest monthly).",
    config={
        "schema_version": 1,
        "label": "IT-Trouble R3 v2: Nifty-IT bear put spread + GoldBeES hedge (top-gainer-grounded)",
        "tier": "aggressive",
        "expression_kind": "option_strategy",
        "instruments": [
            {
                "symbol": "NIFTY IT",
                "exchange": "NFO",
                "segment": "NFO-OPT",
                "instrument_type": "index_option",
                "role": "underlying",
                "tradeable": True,
                "note": "Nifty IT index — bear put spread (buy ATM put, sell OTM put ~5% below ATM)",
            },
            {
                "symbol": "GOLDBEES.NS",
                "exchange": "NSE",
                "segment": "ETF",
                "instrument_type": "etf",
                "role": "hedge",
                "tradeable": True,
                "note": "GoldBeES risk-off hedge: b_it=+0.03 t=0.08 (zero IT/FX loading), hit-freq=0.62",
            },
        ],
        "structure": {
            "template": "bear_put_spread",
            "underlying": "NIFTY IT",
            "underlying_index": "NIFTY IT",
            "expiry_rule": "nearest",
            "qty_lots": 1,
            "hedge_overlay": {
                "symbol": "GOLDBEES.NS",
                "allocation_pct": 20,
                "rationale": "Independent risk-off carry (b_it=0.03, t=0.08); zero IT-factor loading",
            },
            "strikes": None,  # user selects ATM / OTM at trade time
            "legs": [
                {"role": "long_put",  "strike_offset": 0,   "side": "buy"},
                {"role": "short_put", "strike_offset": -0.05, "side": "sell"},
            ],
        },
        "timing": _timing_manual(
            "Fire manually on confirmed weak IT guidance. Buy bear put spread on NIFTY IT "
            "index nearest monthly expiry; simultaneously allocate ~20% of capital to GoldBeES ETF. "
            "Close option at expiry or when thesis resolves."
        ),
        "scores": {
            "alignment_kind": "event_study",
            "construction_alignment": 71.0,
            "backtest": {
                "grade": "B",
                "trust_verdict": "UNPROVEN",
                "trust_conf": 92,
                "n_events": 8,
                "n_obs": 72,
                "win_rate": 0.75,
                "caar_pct": 1.58,
                "caar_t": 2.11,
                "caar_p": 0.035,
                "total_return_pct": 13.16,
                "excess_return_pct": 19.17,
                "max_dd_pct": -2.70,
                "psr": 0.989,
                "dsr": 0.924,
                "min_trl": 37.8,
                "min_trl_cleared": True,
                "outcome_dial": "B",
                "outcome_score": 73,
                "expression_dial": "B",
                "expression_score": 71,
                "option_proxy_note": (
                    "Option leg is delta-proxy (no historical NFO premium/IV/chain). "
                    "Theta/vega/STT not modelled. Directional skeleton only — treat as OPTIMISTIC."
                ),
                "version": "v2_top_gainer_grounded",
                "prior_version": "A (INFY bear put, UNPROVEN conf=84, CAAR −0.82% p=0.646 insignificant)",
            },
        },
        "warnings": [
            "UNPROVEN verdict — 8 analog events only; small-N, fragile significance.",
            "Option leg is a delta-proxy: theta/vega/STT not modelled — returns are OPTIMISTIC.",
            "NFO option chain required at trade time (Kite NIFTY IT index options).",
            "v2 top-gainer-grounded: NIFTY IT index replaces INFY (meaningful CAAR vs insignificant).",
        ],
        "disclaimer": DISCLAIMER,
        "expressability": {
            "symmetric": False,
            "degraded": False,
            "short_mode": "index_put",
            "notes": ["Net debit only (defined-risk). No naked short or margin required."],
        },
        "costs": {
            "round_trip_bps": 45.0,
            "note": "NFO option round-trip on 2 legs + ETF round-trip for GoldBeES sleeve",
        },
    },
)


# ══════════════════════════════════════════════════════════════════════════════
# CRUDE RC1 — Refined Importer-Beneficiary Basket (crude-DOWN / de-escalation)
# Paints-heavy, genuine Brent-connectedness grounded. Existing Strategy A refined:
# - ASIANPAINT / BERGEPAINT: highest |t| in OLS connectedness (genuine paint feedstock)
# - INDIGO: genuine aviation-fuel input connector
# - OMC (HINDPETRO/BPCL/IOC) kept but trimmed (BPCL is also "SHORT" in ONGC-vs-BPCL pair,
#   so weight balanced carefully)
# Backtest (crude_geo_backtest_a.py, real run):
#   total=127.59% / CAGR=5.17%, trades=67, win_rate=62.7%, maxDD=−24.98%
#   expression dial B(78), indicative outcome C(59); UNPROVEN conf=89
# ══════════════════════════════════════════════════════════════════════════════
CRUDE_RC1 = ViewExpressionInput(
    tier="conservative",
    expression_kind="basket",
    rationale=(
        "v2 / connectedness-grounded refined importer basket: concentrates on names with the "
        "strongest GENUINE Brent-negative connectivity (OLS on Brent 10d move → equity return). "
        "Paints (ASIANPAINT/BERGEPAINT: crude is primary feedstock, most direct cost pass-through), "
        "aviation (INDIGO: aviation fuel direct input), OMCs (HINDPETRO/BPCL: refining margin "
        "expands on cheaper crude). Tyres excluded from core (loose daily beta, mixed signals). "
        "Signal: Brent 10-day move ≤ −8%; confirmed de-escalation. 67 episodes, 2010–2026."
    ),
    risk_profile=(
        "Long-only CNC delivery basket, no short/F&O. Crude-DOWN / de-escalation regime only "
        "(opposite to escalation). MaxDD −24.98% (trade-time). Signal-driven: only in position "
        "when Brent 10d move ≤ −8%; cash otherwise. Indian equity delivery = most SEBI-clean. "
        "Drawdown risk flag: crude spikes can reverse; exit is rule-based (hold = 20 trading days)."
    ),
    capital_intensity=(
        "Low-medium — equity CNC delivery only. 5 names. User sizes via total_inr at approval."
    ),
    historical_strength=(
        "67 episodes / 1,328 trade-time obs (2010-2026, yfinance). Total +127.59% (trade-time +131.39%), "
        "CAGR 5.17%. Win-rate 62.7%, avg trade +1.37%, best +12.92% worst −15.72%. "
        "PSR=0.9801, DSR=0.8854, MinTRL=851.2 (n_obs=1,328; MinTRL NOT cleared — large obs needed "
        "for near-zero per-bar Sharpe). UNPROVEN verdict conf=89. Expression dial B(78): "
        "CAAR/BHAR alignment 80%, p=0.012, net-of-cost survival 67%. Outcome SUPPRESSED (N=7 "
        "spike-study events). Indicative pre-suppression: outcome C(59). NIFTY buy-hold same "
        "window: +357.67%; this strategy underperforms buy-hold on a calendar basis "
        "but is signal-regime only. GRADE B (genuine edge in crude-DOWN regime, honest draw on small N)."
    ),
    time_horizon="20 trading days per crude-DOWN episode (signal-driven, not calendar-fixed).",
    config={
        "schema_version": 1,
        "label": "Crude-Geo RC1 v2: Refined Importer-Beneficiary Basket (crude-DOWN, connectedness-grounded)",
        "tier": "conservative",
        "expression_kind": "basket",
        "instruments": [
            {"symbol": "ASIANPAINT.NS", "exchange": "NSE", "segment": "EQ",
             "instrument_type": "equity", "role": "long", "tradeable": True,
             "note": "Highest |t| Brent-negative connectedness (crude = primary feedstock for paints)"},
            {"symbol": "BERGEPAINT.NS", "exchange": "NSE", "segment": "EQ",
             "instrument_type": "equity", "role": "long", "tradeable": True,
             "note": "Second-highest |t| Brent-negative connectedness (crude feedstock)"},
            {"symbol": "HINDPETRO.NS", "exchange": "NSE", "segment": "EQ",
             "instrument_type": "equity", "role": "long", "tradeable": True,
             "note": "OMC: refining margin expands when Brent falls; genuine crude-connected"},
            {"symbol": "BPCL.NS", "exchange": "NSE", "segment": "EQ",
             "instrument_type": "equity", "role": "long", "tradeable": True,
             "note": "OMC (Brent-beta −0.069, t=−4.43); also used in Strat-B short leg — balance weight"},
            {"symbol": "INDIGO.NS", "exchange": "NSE", "segment": "EQ",
             "instrument_type": "equity", "role": "long", "tradeable": True,
             "note": "Aviation fuel direct input; Brent-connected, genuine importer beneficiary"},
        ],
        "structure": {
            "scheme": "conviction_weight",
            "weights": {
                "ASIANPAINT.NS": 0.22,
                "BERGEPAINT.NS": 0.18,
                "HINDPETRO.NS": 0.16,
                "BPCL.NS": 0.14,
                "INDIGO.NS": 0.30,   # elevated vs original 0.18 — aviation has highest operational leverage
            },
            "n_names": 5,
            "single_name_cap": 0.30,
            "basket_purity": 0.78,
            "min_names": 4,
            "signal": "Brent 10d move ≤ −8% (crude-DOWN de-escalation signal)",
            "hold_bars": 20,
        },
        "timing": {
            "mode": "confirmation",
            "note": (
                "Signal-driven: enter on the NEXT BAR after Brent 10-day move ≤ −8% is confirmed "
                "at close. Hold for 20 trading days. Exit at hold period end or on thesis break "
                "(Brent rebounds +5% from entry — monitor externally). No automated Brent trigger "
                "(BZ=F is foreign; Indian price-trigger needs NSE/BSE symbol)."
            ),
            "tranches": [{"pct": 100, "trigger": {"step_type": "trigger.manual", "config": {}}}],
            "invalidation": None,
        },
        "scores": {
            "alignment_kind": "event_study",
            "construction_alignment": 78.0,
            "backtest": {
                "grade": "B",
                "trust_verdict": "UNPROVEN",
                "trust_conf": 89,
                "n_episodes": 67,
                "n_obs_trade_time": 1328,
                "win_rate": 0.627,
                "avg_trade_pct": 1.37,
                "total_return_pct": 127.59,
                "max_dd_pct": -24.98,
                "psr": 0.9801,
                "dsr": 0.8854,
                "min_trl": 851.2,
                "min_trl_cleared": False,
                "outcome_dial": "SUPPRESSED",
                "expression_dial": "B",
                "expression_score": 78,
                "indicative_outcome": "C",
                "indicative_outcome_score": 59,
                "nifty_buy_hold_total_pct": 357.67,
                "version": "v2_connectedness_grounded",
                "data_window": "2010-01-04 to 2026-06-29",
                "engine": "crude_geo_backtest_a.py (real run 2026-06-30)",
            },
        },
        "warnings": [
            "UNPROVEN verdict — expression MinTRL=851.2 not cleared (near-zero per-bar Sharpe).",
            "Underperforms NIFTY buy-hold on calendar basis; edge is REGIME-specific (crude-DOWN only).",
            "Drawdown risk flag: crude spikes can quickly reverse the position.",
            "Signal requires external monitoring of Brent 10d move — no automated price trigger today.",
            "v2 connectedness-grounded: IOC replaced by overweight INDIGO (higher operational leverage).",
        ],
        "disclaimer": DISCLAIMER,
        "expressability": {
            "symmetric": False,
            "degraded": False,
            "short_mode": None,
            "notes": ["Long-only CNC delivery. Signal-driven, not always in position."],
        },
        "costs": {
            "round_trip_bps": 36.9,
            "note": "Equity delivery round-trip per episode (5 names)",
        },
    },
)


# ══════════════════════════════════════════════════════════════════════════════
# Main: attach expressions → deploy workflow drafts → validate + print table
# ══════════════════════════════════════════════════════════════════════════════

def main():
    print("=" * 78)
    print("  Deploy refined IT + crude strategies → ViewExpression rows + workflow drafts")
    print("  register-not-execute | NO activation | Additive only")
    print("=" * 78)

    results = []

    with SessionLocal() as db:
        # ── IT view: R1, R2, R3 ───────────────────────────────────────────────
        it_view = db.get(MarketView, IT_VIEW_ID)
        if it_view is None:
            print(f"ERROR: IT view {IT_VIEW_ID!r} not found")
            return

        print(f"\nIT view: {it_view.title!r} ({IT_VIEW_ID})")

        for label, expr_input, grade in [
            ("R1 Long-Auto vs Short-IT pair", IT_R1, "B+"),
            ("R2 Defence+Auto basket",         IT_R2, "A"),
            ("R3 Nifty-IT bear put + GoldBeES",IT_R3, "B"),
        ]:
            print(f"\n  Attaching {label} ...", end=" ")
            rows = attach_expressions(db, IT_VIEW_ID, [expr_input], replace=False)
            db.flush()
            expr_row = rows[0]
            print(f"expr_id={expr_row.id}")

            print(f"  Deploying workflow draft ...", end=" ")
            deploy = deploy_expression(
                db, expr_row,
                timing_mode=None,
                activate=False,
                user_id=OWNER_USER_ID,
            )
            print(f"wf_id={deploy['workflow_id']} status={deploy['status']}")

            results.append({
                "view": "IT trouble",
                "strategy": f"{label} (v2/top-gainer-grounded)",
                "grade": grade,
                "expression_id": str(expr_row.id),
                "workflow_draft_id": deploy["workflow_id"],
                "register_not_execute": deploy["register_not_execute"],
                "activated": deploy["activated"],
                "how_to_place": (
                    "Open workflow draft in app → confirm each order step → "
                    "place via your broker app (Zerodha/Dhan). Nothing auto-executes."
                ),
            })

        # ── Crude view: RC1 ───────────────────────────────────────────────────
        crude_view = db.get(MarketView, CRUDE_VIEW_ID)
        if crude_view is None:
            print(f"ERROR: Crude view {CRUDE_VIEW_ID!r} not found")
        else:
            print(f"\nCrude view: {crude_view.title!r} ({CRUDE_VIEW_ID})")
            print(f"\n  Attaching RC1 Refined Importer Basket ...", end=" ")
            rows = attach_expressions(db, CRUDE_VIEW_ID, [CRUDE_RC1], replace=False)
            db.flush()
            expr_row = rows[0]
            print(f"expr_id={expr_row.id}")

            print(f"  Deploying workflow draft ...", end=" ")
            deploy = deploy_expression(
                db, expr_row,
                timing_mode=None,
                activate=False,
                user_id=OWNER_USER_ID,
            )
            print(f"wf_id={deploy['workflow_id']} status={deploy['status']}")

            results.append({
                "view": "Crude / geo shock",
                "strategy": "RC1 Refined Importer Basket (v2/connectedness-grounded)",
                "grade": "B",
                "expression_id": str(expr_row.id),
                "workflow_draft_id": deploy["workflow_id"],
                "register_not_execute": deploy["register_not_execute"],
                "activated": deploy["activated"],
                "how_to_place": (
                    "Await Brent 10d move ≤ −8% signal → open workflow draft → "
                    "confirm basket allocation → place CNC delivery in broker app. "
                    "Crude B/C strategies: NO_EDGE, not deployed (B=conf 41, C=conf 59)."
                ),
            })

        # ── Commit ────────────────────────────────────────────────────────────
        db.commit()
        print("\nDB commit OK.")

    # ── Validation query ──────────────────────────────────────────────────────
    print("\n" + "=" * 78)
    print("  VALIDATION: re-query new expressions + workflow drafts")
    print("=" * 78)
    with SessionLocal() as db:
        for r in results:
            expr = db.get(ViewExpression, r["expression_id"])
            if expr is None:
                print(f"  ERROR: expr {r['expression_id']} not found after commit!")
                continue
            wf_id = expr.workflow_id
            print(
                f"\n  view={r['view']!r} | strategy={r['strategy']!r}\n"
                f"    expression_id={expr.id}\n"
                f"    workflow_draft_id={wf_id}\n"
                f"    register_not_execute={r['register_not_execute']}\n"
                f"    activated={r['activated']}\n"
                f"    grade={r['grade']}"
            )
            assert wf_id is not None, "workflow_id not linked!"
            assert r["register_not_execute"] is True
            assert r["activated"] is False

    # ── Final table ───────────────────────────────────────────────────────────
    print("\n" + "=" * 78)
    print("  FINAL RESULT TABLE")
    print("=" * 78)
    print(f"{'View':<20} {'Strategy':<48} {'expr_id[:8]':<10} {'wf_draft_id[:8]':<16} {'Grade':<6} How-to-place")
    print("-" * 140)
    for r in results:
        print(
            f"{r['view']:<20} {r['strategy'][:47]:<48} "
            f"{r['expression_id'][:8]:<10} {r['workflow_draft_id'][:8]:<16} "
            f"{r['grade']:<6} {r['how_to_place'][:80]}"
        )

    print("\nAll", len(results), "deploy drafts written. Nothing activated. Nothing executed.")


if __name__ == "__main__":
    main()
