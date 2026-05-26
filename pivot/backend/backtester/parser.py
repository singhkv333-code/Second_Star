"""
Natural-language → structured strategy definition parser.

ONE LLM call with native function-calling. Falls back to a rule-based
extractor when the LLM call fails so the parser still
works in tests and dev environments.

Output shape (the "ready" path):
    {
        "status": "ready",
        "strategy": {
            "symbol": str,
            "entry": {"operator": str, "n": int|None,
                       "conditions": [{"signal": str, "params": {...},
                                        "cooldown_days": int|None,
                                        "negate": bool}, ...]},
            "exit":  {"operator": "first_of",
                       "conditions": [{"exit_type": str, "params": {...}}, ...]},
            "position_size_inr": float|None,
            "position_size_pct": float|None,
            "starting_capital": float,
            "max_positions": int,
            "period": str,
            "start_date": str|None,
            "end_date": str|None,
            "strategy_description": str,
        },
    }
"""
from __future__ import annotations

import logging
import re
from typing import Optional

from backend.llm.base import LLMMessage
from backend.llm.factory import get_llm_client
from backend.backtester.exits import EXIT_REGISTRY
from backend.backtester.primitives import SIGNAL_REGISTRY

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Keyword pre-filter — reject obviously non-backtest messages
# ---------------------------------------------------------------------------

KEYWORDS = [
    "backtest", "test", "what if", "if i bought", "if i had",
    "historically", "simulate", "would have", "every time",
    "whenever", "rsi", "macd", "52 week", "moving average",
    "bollinger", "supertrend", "ichimoku", "stochastic",
    "golden cross", "death cross", "squeeze", "breakout",
    "each monday", "each week", "sma cross", "ema cross",
]


# ---------------------------------------------------------------------------
# Valid signal/exit names — sourced dynamically from the registries
# ---------------------------------------------------------------------------

VALID_ENTRY_SIGNALS: set[str] = set(SIGNAL_REGISTRY.keys())
VALID_EXIT_TYPES: set[str] = set(EXIT_REGISTRY.keys())


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

DEFAULT_STARTING_CAPITAL = 500_000.0
DEFAULT_PERIOD = "2y"
DEFAULT_MAX_POSITIONS = 10


def _default_exit() -> dict:
    return {
        "operator": "first_of",
        "conditions": [{"exit_type": "end_of_period", "params": {}}],
    }


# ---------------------------------------------------------------------------
# LLM system prompt (full SIGNAL_REGISTRY namespace)
# ---------------------------------------------------------------------------

PARSER_SYSTEM_PROMPT = """
You convert a natural language strategy description into a structured backtesting strategy definition for Pivot.

AVAILABLE SIGNAL TYPES (entry and exit):
Moving averages: price_cross_above_sma, price_cross_below_sma, price_above_sma, price_below_sma, golden_cross_sma, death_cross_sma, golden_cross_ema, death_cross_ema, hma_turn_up
RSI: rsi_cross_below, rsi_cross_above, rsi_in_range, rsi_below_level, rsi_divergence_bullish, rsi_divergence_bearish
Stochastic: stoch_cross_above, stoch_cross_below, stochrsi_cross_above
CCI/Williams/MFI: cci_cross_above, cci_cross_below, williams_r_cross_above, mfi_oversold, mfi_cross_below
Momentum: roc_cross_zero_up, momentum_cross_zero_up, ao_cross_zero_up
MACD: macd_cross_above_signal, macd_cross_below_signal, macd_histogram_cross_zero_up, macd_histogram_cross_zero_down, macd_line_cross_zero_up, macd_divergence_bullish
Bollinger Bands: bb_lower_touch, bb_upper_touch, bb_breakout_above, bb_squeeze, bb_squeeze_breakout_up, bb_mean_reversion
Volatility: supertrend_flip_bullish, supertrend_flip_bearish, price_above_supertrend, keltner_breakout_above, donchian_breakout_above, atr_expansion, atr_contraction
Trend Strength: adx_strong_trend, adx_weak_trend, di_cross_bullish, aroon_bullish_cross, vortex_bullish_cross
Volume: volume_spike, volume_price_confirm_up, obv_cross_above_sma, cmf_cross_zero_up, high_volume_breakout, accumulation_day
Price Action: 52wk_high_breakout, 52wk_low_breakdown, n_period_high_breakout, pct_below_52wk_high, gap_up, pct_dip_from_yesterday, psar_flip_bullish, inside_day_breakout, hammer_candle, engulfing_bullish
Ichimoku: ichimoku_tk_cross_bullish, ichimoku_price_above_cloud, ichimoku_cloud_breakout_up, ichimoku_full_bullish
Calendar: monday, tuesday, wednesday, thursday, friday, first_day_of_month, last_day_of_month, first_day_of_quarter
Squeeze: squeeze_fire_up, squeeze_fire_down
Pivots: price_cross_above_pivot, price_at_support
Fundamental: nifty_pe_below, nifty_pe_cross_below

Exit types: after_n_days, stop_loss, trailing_stop, take_profit, stop_and_target, indicator_signal, end_of_period

COMBINATION OPERATORS: and, or, require_n_of

Convert company names to NSE tickers (e.g. "Reliance" → "RELIANCE", "Infosys" → "INFY", "TCS" → "TCS", "HDFC Bank" → "HDFCBANK"). For ETFs, use NIFTYBEES, BANKBEES, etc.

PARAMS PER SIGNAL — emit defaults if user did not specify:
- rsi_cross_below / rsi_cross_above: {"period": 14, "threshold": <user value or 30/70>}
- price_cross_above_sma / price_cross_below_sma / price_above_sma / price_below_sma: {"period": <user value or 50>}
- golden_cross_sma / death_cross_sma / golden_cross_ema / death_cross_ema: {"fast_period": 50, "slow_period": 200}
- macd_*: {"fast": 12, "slow": 26, "signal": 9}
- bb_* (bollinger): {"period": 20, "std": 2.0}
- supertrend_*: {"atr_period": 10, "mult": 3.0}
- atr_expansion / atr_contraction: {"period": 14, "mult": <user value or 1.5>}
- 52wk_high_breakout / 52wk_low_breakdown: {} (no params)
- monday / tuesday / ... / first_day_of_month etc: {} (no params)
- volume_spike: {"period": 20, "mult": 2.0}
- gap_up / gap_down: {"min_pct": <user value or 1.0>}

EXIT PARAMS:
- stop_loss: {"stop_pct": <user value>}     // e.g. 5 for 5%
- take_profit: {"target_pct": <user value>}
- trailing_stop: {"trail_pct": <user value>}
- after_n_days: {"n_days": <user value>}
- indicator_signal: {"signal": "<name>", "params": {...}}

DEFAULTS:
- starting_capital: 500000 (₹5 lakh)
- period: "2y" if not specified
- exit: always include end_of_period as a fallback
- max_positions: 5 unless user says otherwise

POSITION SIZING:
- Extract any rupee amount the user mentions ("with 50000", "₹1 lakh", "2L per trade") into position_size_inr (in rupees).
- "1 lakh" = 100000, "2 lakh" = 200000, "5L" = 500000, "10k" = 10000.

Always call define_strategy with ALL extracted parameters and the FULL params dict for each signal (not empty).
List missing_params only if the symbol or position_size is genuinely unspecified.
"""


# ---------------------------------------------------------------------------
# LLM tool definition — structured entry/exit shape
# ---------------------------------------------------------------------------

PARSE_TOOL = {
    "type": "function",
    "function": {
        "name": "define_strategy",
        "description": (
            "Extract a backtest strategy definition from a natural-language "
            "request. Build a structured entry block (operator + conditions) "
            "and exit block (first_of + conditions)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "symbol": {
                    "type": "string",
                    "description": "NSE ticker e.g. RELIANCE, INFY, NIFTYBEES",
                },
                "entry": {
                    "type": "object",
                    "description": (
                        "Entry block. operator is 'single' for one condition, "
                        "or 'and' / 'or' / 'require_n_of' for combined logic. "
                        "n is required for require_n_of."
                    ),
                    "properties": {
                        "operator": {
                            "type": "string",
                            "enum": ["single", "and", "or", "require_n_of"],
                        },
                        "n": {"type": "integer"},
                        "conditions": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "signal": {
                                        "type": "string",
                                        "description": (
                                            "Signal name from SIGNAL_REGISTRY"
                                        ),
                                    },
                                    "params": {
                                        "type": "object",
                                        "description": (
                                            "Only the params relevant to "
                                            "this signal: period, threshold, "
                                            "fast, slow, std, weekday, mult, "
                                            "pct, n_days, n, etc."
                                        ),
                                    },
                                    "cooldown_days": {"type": "integer"},
                                    "negate": {"type": "boolean"},
                                },
                                "required": ["signal"],
                            },
                        },
                    },
                    "required": ["operator", "conditions"],
                },
                "exit": {
                    "type": "object",
                    "description": (
                        "Exit block. operator is 'first_of' (any condition "
                        "fires → exit). Each condition has an exit_type and "
                        "type-specific params."
                    ),
                    "properties": {
                        "operator": {"type": "string", "enum": ["first_of"]},
                        "conditions": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "exit_type": {
                                        "type": "string",
                                        "description": (
                                            "Exit name from EXIT_REGISTRY: "
                                            "after_n_days | stop_loss | "
                                            "trailing_stop | take_profit | "
                                            "stop_and_target | "
                                            "indicator_signal | end_of_period"
                                        ),
                                    },
                                    "params": {
                                        "type": "object",
                                        "description": (
                                            "Only the params relevant to "
                                            "this exit: n_days, stop_pct, "
                                            "target_pct, trail_pct, signal, "
                                            "signal_params"
                                        ),
                                    },
                                },
                                "required": ["exit_type"],
                            },
                        },
                    },
                    "required": ["operator", "conditions"],
                },
                "position_size_inr": {"type": "number"},
                "position_size_pct": {"type": "number"},
                "starting_capital": {"type": "number"},
                "max_positions": {"type": "integer"},
                "period": {
                    "type": "string",
                    "enum": [
                        "1mo", "3mo", "6mo", "1y", "2y", "3y", "5y",
                        "ytd", "max",
                    ],
                },
                "start_date": {"type": "string"},
                "end_date": {"type": "string"},
                "missing_params": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "strategy_description": {"type": "string"},
            },
            "required": ["symbol", "entry"],
        },
    },
}


# ---------------------------------------------------------------------------
# Public entry
# ---------------------------------------------------------------------------

async def parse_strategy(message: str) -> Optional[dict]:
    if not message:
        return None
    lower = message.lower()
    if not any(k in lower for k in KEYWORDS):
        return None

    try:
        client = get_llm_client()
        resp = await client.complete(
            messages=[
                LLMMessage(role="system", content=PARSER_SYSTEM_PROMPT),
                LLMMessage(role="user", content=message),
            ],
            tools=[PARSE_TOOL],
            tool_choice="auto",
            temperature=0.1,
            max_output_tokens=1200,
        )
    except Exception as e:
        logger.warning(f"LLM parse_strategy failed, falling back to rules: {e}")
        return _rule_based_parse(message)

    tc = (resp.tool_calls or [None])[0] if resp.tool_calls else None
    if not tc or tc.get("name") != "define_strategy":
        return _rule_based_parse(message)

    args = tc.get("arguments") or {}
    return _finalise_strategy(args, message)


# ---------------------------------------------------------------------------
# Finalise & defaults
# ---------------------------------------------------------------------------

def _validate_entry_block(entry: dict) -> Optional[str]:
    """Returns a reason string if invalid, else None."""
    if not isinstance(entry, dict):
        return "entry block missing"
    operator = entry.get("operator")
    if operator not in {"single", "and", "or", "require_n_of"}:
        return "entry.operator invalid"
    conditions = entry.get("conditions")
    if not isinstance(conditions, list) or not conditions:
        return "entry.conditions empty"
    for c in conditions:
        sig = (c or {}).get("signal")
        if not sig or sig not in VALID_ENTRY_SIGNALS:
            return f"unknown signal '{sig}'"
    if operator == "require_n_of" and not isinstance(entry.get("n"), int):
        return "require_n_of needs an integer n"
    return None


def _normalise_exit_block(exit_block) -> dict:
    """Coerce a raw exit block to the canonical first_of shape; default if invalid."""
    if not isinstance(exit_block, dict):
        return _default_exit()
    conditions = exit_block.get("conditions") or []
    if not isinstance(conditions, list) or not conditions:
        return _default_exit()
    cleaned: list[dict] = []
    for c in conditions:
        if not isinstance(c, dict):
            continue
        et = c.get("exit_type")
        if et not in VALID_EXIT_TYPES:
            continue
        cleaned.append({"exit_type": et, "params": c.get("params") or {}})
    if not cleaned:
        return _default_exit()
    # Always include an end_of_period fallback so the simulation closes out.
    if not any(c["exit_type"] == "end_of_period" for c in cleaned):
        cleaned.append({"exit_type": "end_of_period", "params": {}})
    return {"operator": "first_of", "conditions": cleaned}


def _finalise_strategy(args: dict, original_message: str) -> dict:
    symbol = (args.get("symbol") or "").strip().upper()
    entry = args.get("entry") or {}

    # Symbol & entry validation
    if not symbol:
        return {
            "status": "needs_clarification",
            "missing": ["symbol"],
            "question": "Which stock would you like to backtest?",
        }
    err = _validate_entry_block(entry)
    if err:
        # LLM couldn't structure a valid entry — fall back to rule-based.
        rb = _rule_based_parse(original_message)
        if rb and rb.get("status") in {"ready", "needs_clarification"}:
            return rb
        return {
            "status": "needs_clarification",
            "missing": ["entry_signal"],
            "question": (
                "Which entry signal should I use? "
                "(e.g. RSI cross below 30, MACD crossover, 52-week high)"
            ),
        }

    # Position sizing — try the LLM first, then fall back to extracting from message.
    missing: list[str] = [
        m for m in (args.get("missing_params") or [])
        if m in {"position_size_inr", "symbol", "entry_signal"}
    ]
    pos_inr = args.get("position_size_inr")
    pos_pct = args.get("position_size_pct")
    if not pos_inr and not pos_pct:
        extracted = _extract_position_size(original_message)
        if extracted is not None:
            pos_inr = extracted
            missing = [m for m in missing if m != "position_size_inr"]
        elif "position_size_inr" not in missing:
            missing.append("position_size_inr")

    if "position_size_inr" in missing and not pos_pct:
        return {
            "status": "needs_clarification",
            "missing": missing,
            "question": "How much would you like to invest per trade? (e.g. ₹50,000)",
        }

    strategy = {
        "symbol": symbol,
        "entry": {
            "operator": entry["operator"],
            "n": entry.get("n"),
            "conditions": [
                {
                    "signal": c["signal"],
                    "params": c.get("params") or {},
                    "cooldown_days": c.get("cooldown_days"),
                    "negate": bool(c.get("negate")),
                }
                for c in entry["conditions"]
            ],
        },
        "exit": _normalise_exit_block(args.get("exit")),
        "position_size_inr": (float(pos_inr) if pos_inr else None),
        "position_size_pct": (float(pos_pct) if pos_pct else None),
        "starting_capital": float(args.get("starting_capital") or DEFAULT_STARTING_CAPITAL),
        "max_positions": int(args.get("max_positions") or DEFAULT_MAX_POSITIONS),
        "period": args.get("period") or DEFAULT_PERIOD,
        "start_date": args.get("start_date"),
        "end_date": args.get("end_date"),
        "strategy_description": args.get("strategy_description") or original_message,
    }
    return {"status": "ready", "strategy": strategy}


# ---------------------------------------------------------------------------
# Rule-based fallback (used when the LLM call fails)
#
# TODO: enrich fallback for full registry. Currently maps the most common
# patterns: RSI thresholds, MACD crossovers, golden/death cross, simple SMA
# crosses, 52-week breakouts, Bollinger lower-touch, calendar+SMA combos,
# stop-loss/take-profit/trailing exits.
# ---------------------------------------------------------------------------

# Order matters — longer/more specific names first
_NAME_TO_TICKER = [
    ("hdfc bank", "HDFCBANK"), ("hdfcbank", "HDFCBANK"),
    ("icici bank", "ICICIBANK"), ("icicibank", "ICICIBANK"),
    ("axis bank", "AXISBANK"), ("axisbank", "AXISBANK"),
    ("kotak bank", "KOTAKBANK"), ("kotakbank", "KOTAKBANK"),
    ("state bank", "SBIN"), ("sbin", "SBIN"),
    ("tata consultancy", "TCS"), ("tcs", "TCS"),
    ("tata motors", "TATAMOTORS"), ("tatamotors", "TATAMOTORS"),
    ("tech mahindra", "TECHM"), ("techm", "TECHM"),
    ("hcltech", "HCLTECH"), ("hcl", "HCLTECH"),
    ("infosys", "INFY"), ("infy", "INFY"),
    ("reliance", "RELIANCE"),
    ("wipro", "WIPRO"),
    ("itc", "ITC"),
    ("hul", "HINDUNILVR"), ("hindustan unilever", "HINDUNILVR"),
    ("nifty bees", "NIFTYBEES"), ("niftybees", "NIFTYBEES"),
    ("nifty 50", "NIFTYBEES"), ("nifty50", "NIFTYBEES"),
    ("maruti", "MARUTI"),
    ("ongc", "ONGC"),
    ("ntpc", "NTPC"),
    ("nestle", "NESTLEIND"),
    ("asian paints", "ASIANPAINT"),
    ("bajaj finance", "BAJFINANCE"), ("bajfinance", "BAJFINANCE"),
    ("adani enterprises", "ADANIENT"), ("adanient", "ADANIENT"),
]

_WEEKDAYS = {
    "monday": 0, "tuesday": 1, "wednesday": 2,
    "thursday": 3, "friday": 4,
}


def _extract_symbol(message: str) -> Optional[str]:
    lower = message.lower()
    for name, ticker in _NAME_TO_TICKER:
        if name in lower:
            return ticker
    for tok in re.findall(r"\b([A-Z]{3,12})\b", message):
        if tok.upper() in {"RSI", "MACD", "SMA", "EMA", "ETF", "INR", "NSE",
                           "BSE", "ATM", "OTM", "ITM", "SIP", "STT", "YTD",
                           "ADX", "MFI", "OBV", "CMF", "ATR", "PSAR"}:
            continue
        return tok.upper()
    return None


def _extract_period(message: str) -> str:
    lower = message.lower()
    m = re.search(r"(\d+)\s*(?:y|yr|yrs|year|years)\b", lower)
    if m:
        n = int(m.group(1))
        if n in (1, 2, 3, 5):
            return f"{n}y"
        if n >= 10:
            return "max"
        return f"{n}y"
    if "ytd" in lower or "year to date" in lower:
        return "ytd"
    if "month" in lower:
        m = re.search(r"(\d+)\s*month", lower)
        if m:
            n = int(m.group(1))
            if n <= 1:
                return "1mo"
            if n <= 3:
                return "3mo"
            return "6mo"
    return DEFAULT_PERIOD


def _extract_position_size(message: str) -> Optional[float]:
    lower = message.lower()
    m = re.search(r"₹\s*([\d,]+(?:\.\d+)?)", message)
    if m:
        return float(m.group(1).replace(",", ""))
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:lakh|lac)", lower)
    if m:
        return float(m.group(1)) * 100_000
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:k|thousand)", lower)
    if m:
        return float(m.group(1)) * 1_000
    m = re.search(r"\bwith\s+([\d,]+)\b", lower)
    if m:
        try:
            v = float(m.group(1).replace(",", ""))
            if v >= 1000:
                return v
        except ValueError:
            pass
    m = re.search(r"\b([\d,]{4,})\s*(?:per trade|each time|each|per\b)", lower)
    if m:
        try:
            return float(m.group(1).replace(",", ""))
        except ValueError:
            pass
    return None


def _extract_entry_conditions(message: str) -> tuple[Optional[str], list[dict]]:
    """Returns (operator, conditions). operator is 'single' or 'and'."""
    lower = message.lower()
    conditions: list[dict] = []

    # Calendar (weekday)
    weekday = next(
        (v for k, v in _WEEKDAYS.items()
         if f"every {k}" in lower or f"each {k}" in lower or f"on {k}" in lower),
        None,
    )

    # RSI
    if "rsi" in lower:
        m = re.search(r"rsi.*?(?:below|under|<)\s*(\d+)", lower)
        if m:
            conditions.append({
                "signal": "rsi_cross_below",
                "params": {"period": 14, "threshold": float(m.group(1))},
            })
        else:
            m2 = re.search(r"rsi.*?(?:above|over|>)\s*(\d+)", lower)
            if m2:
                conditions.append({
                    "signal": "rsi_cross_above",
                    "params": {"period": 14, "threshold": float(m2.group(1))},
                })
            else:
                # Generic "RSI strategy" with no threshold — default oversold entry
                conditions.append({
                    "signal": "rsi_cross_below",
                    "params": {"period": 14, "threshold": 30.0},
                })

    # MACD
    if "macd" in lower and not any(c["signal"].startswith("macd") for c in conditions):
        if "bearish" in lower or "below signal" in lower:
            conditions.append({
                "signal": "macd_cross_below_signal",
                "params": {"fast": 12, "slow": 26, "signal": 9},
            })
        else:
            conditions.append({
                "signal": "macd_cross_above_signal",
                "params": {"fast": 12, "slow": 26, "signal": 9},
            })

    # Golden / death cross
    if "golden cross" in lower:
        conditions.append({
            "signal": "golden_cross_sma",
            "params": {"fast_period": 50, "slow_period": 200},
        })
    elif "death cross" in lower:
        conditions.append({
            "signal": "death_cross_sma",
            "params": {"fast_period": 50, "slow_period": 200},
        })

    # 52-week breakout
    if "52" in lower and ("high" in lower or "wk" in lower or "week" in lower):
        if "low" in lower:
            conditions.append({"signal": "52wk_low_breakdown", "params": {}})
        else:
            conditions.append({"signal": "52wk_high_breakout", "params": {}})

    # Bollinger lower touch
    if "bollinger" in lower and "lower" in lower:
        conditions.append({
            "signal": "bb_lower_touch",
            "params": {"period": 20, "std": 2.0},
        })

    # Calendar combo with optional price-vs-SMA filter
    if weekday is not None:
        weekday_name = next(k for k, v in _WEEKDAYS.items() if v == weekday)
        conditions.append({"signal": weekday_name, "params": {}})
        sma_match = (re.search(r"sma\s*(\d+)", lower)
                     or re.search(r"(\d+)[-\s]day", lower)
                     or re.search(r"moving average\s*(\d+)", lower))
        if sma_match:
            sma_period = int(next(g for g in sma_match.groups() if g))
            if "above" in lower:
                conditions.append({
                    "signal": "price_above_sma",
                    "params": {"period": sma_period},
                })
            elif "below" in lower:
                conditions.append({
                    "signal": "price_below_sma",
                    "params": {"period": sma_period},
                })

    # Plain SMA cross (no calendar)
    if not conditions:
        sma_match = (re.search(r"(\d+)[-\s]day (?:sma|moving average)", lower)
                     or re.search(r"sma\s*(\d+)", lower)
                     or re.search(r"moving average\s*(\d+)", lower))
        if sma_match:
            period = int(sma_match.group(1))
            if "below" in lower or "cross below" in lower:
                conditions.append({
                    "signal": "price_cross_below_sma",
                    "params": {"period": period},
                })
            else:
                conditions.append({
                    "signal": "price_cross_above_sma",
                    "params": {"period": period},
                })

    # Monthly SIP
    if not conditions and ("monthly" in lower or "every month" in lower
                            or "sip" in lower):
        conditions.append({"signal": "first_day_of_month", "params": {}})

    if not conditions:
        return None, []

    operator = "single" if len(conditions) == 1 else "and"
    return operator, conditions


def _extract_exit_conditions(message: str) -> list[dict]:
    """Returns a list of exit condition dicts for the first_of operator."""
    lower = message.lower()
    out: list[dict] = []

    # Trailing stop
    m = re.search(r"trail(?:ing)?\s*stop[^0-9]*([\d.]+)\s*%?", lower)
    if m:
        out.append({
            "exit_type": "trailing_stop",
            "params": {"trail_pct": float(m.group(1))},
        })

    # Stop loss + target combo
    m_stop = re.search(r"stop\s*loss[^0-9]*([\d.]+)\s*%?", lower)
    m_target = re.search(r"(?:take\s*profit|target)[^0-9]*([\d.]+)\s*%?", lower)
    if m_stop and m_target:
        out.append({
            "exit_type": "stop_and_target",
            "params": {
                "stop_pct": float(m_stop.group(1)),
                "target_pct": float(m_target.group(1)),
            },
        })
    else:
        if m_stop:
            out.append({
                "exit_type": "stop_loss",
                "params": {"stop_pct": float(m_stop.group(1))},
            })
        if m_target:
            out.append({
                "exit_type": "take_profit",
                "params": {"target_pct": float(m_target.group(1))},
            })

    # After N days
    m = re.search(r"(?:after|hold(?:ing)? for|exit after)\s*(\d+)\s*days?", lower)
    if m:
        out.append({
            "exit_type": "after_n_days",
            "params": {"n_days": int(m.group(1))},
        })

    # RSI cross above used as exit
    m = re.search(r"(?:sell|exit).*?rsi.*?(?:above|over|>)\s*(\d+)", lower)
    if m:
        out.append({
            "exit_type": "indicator_signal",
            "params": {
                "signal": "rsi_cross_above",
                "signal_params": {"period": 14, "threshold": float(m.group(1))},
            },
        })

    # Always end with end_of_period as a fallback close
    if not any(c["exit_type"] == "end_of_period" for c in out):
        out.append({"exit_type": "end_of_period", "params": {}})

    return out


def _rule_based_parse(message: str) -> dict:
    symbol = _extract_symbol(message)
    operator, conditions = _extract_entry_conditions(message)

    if not symbol or not conditions:
        missing = []
        if not symbol:
            missing.append("symbol")
        if not conditions:
            missing.append("entry_signal")
        return {
            "status": "needs_clarification",
            "missing": missing,
            "question": (
                "Which stock and signal should I backtest? "
                "(e.g. 'RELIANCE on RSI cross below 30')"
            ),
        }

    position_size = _extract_position_size(message)
    if position_size is None:
        return {
            "status": "needs_clarification",
            "missing": ["position_size_inr"],
            "question": "How much would you like to invest per trade? (e.g. ₹50,000)",
        }

    period = _extract_period(message)
    exit_conditions = _extract_exit_conditions(message)

    strategy = {
        "symbol": symbol,
        "entry": {
            "operator": operator,
            "n": None,
            "conditions": [
                {
                    "signal": c["signal"],
                    "params": c.get("params") or {},
                    "cooldown_days": None,
                    "negate": False,
                }
                for c in conditions
            ],
        },
        "exit": {"operator": "first_of", "conditions": exit_conditions},
        "position_size_inr": float(position_size),
        "position_size_pct": None,
        "starting_capital": DEFAULT_STARTING_CAPITAL,
        "max_positions": DEFAULT_MAX_POSITIONS,
        "period": period,
        "start_date": None,
        "end_date": None,
        "strategy_description": message,
    }
    return {"status": "ready", "strategy": strategy}
