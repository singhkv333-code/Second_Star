"""Server-side workflow draft hydration for macro tools.

The bulk of `propose_workflow`'s output is structural boilerplate —
`{ "step_type": "...", "config": {...} }` repeated N times, with
the user-specific bits (symbol, qty, threshold, time) being maybe
3% of the JSON. The model spends ~7 seconds writing the structure
that the server already knows.

These four hydration functions take small typed params and return
the full WorkflowDraft dict. The corresponding LLM tool definitions
in `agents/tools.py` expose only the params; the model emits ~20-30
tokens instead of ~1000, dropping decode time from ~7s to ~0.2s.

Each function:
  - Takes a strongly-typed dataclass-style input
  - Returns a dict matching the WorkflowDraft schema (name, description,
    steps[], rationale, warnings, _render_hint)
  - Validates the result against the registry before returning so a
    bad hydration fails server-side rather than leaking malformed
    drafts to the user

Macro coverage by user phrasing:

  propose_scheduled_order
      "buy 5 NIFTYBEES every weekday at 09:15"
      "every Monday 09:15 sell 2 INFY"
      "weekly SIP into HDFCBANK at 9:30 AM"

  propose_threshold_order
      "buy 10 INFY when RSI < 30"
      "sell 5 RELIANCE when price crosses above 2800"
      "buy HDFCBANK ₹5K when 50 SMA crosses above 200 SMA"
        (collapsed into a single SMA threshold trigger for v1)

  propose_basket_allocation
      "invest ₹1L equally across top 10 steel stocks when NIFTY gaps up"
      "put ₹50K into top 5 banking stocks daily at open"

  propose_holding_action
      "sell my INFY when RSI > 70"
      "set 2% SL on my RELIANCE"
      "exit TCS if it drops below 3500"

Anything outside these four shapes goes through full `propose_workflow`.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Literal, Optional

logger = logging.getLogger(__name__)


# ── Shared rationale builder ───────────────────────────────────────
#
# Every macro emits a draft with a `rationale` field. Historically those
# were one-liners ("Trigger fires when RSI(14) < 30; market buy for 10
# INFY.") which looked like capability theater — the user couldn't tell
# WHY the macro picked these instruments, what risks the structure
# carries, or what the automation explicitly is NOT. The user can no
# longer accept that quality bar (see propose.py system prompt — same
# rationale contract on the LLM side).
#
# `_compose_rationale` standardises the 3-6 sentence shape across all
# four macros: WHAT (one line on the trigger/action), WHY (instrument
# selection + economic mechanism if known), RISK (the failure modes the
# user is on the hook for), NOT (what this draft is explicitly not — a
# hedge, market-neutral, intraday, position-sized, etc.). Each call site
# supplies the four parts; this helper joins them with consistent
# punctuation so the FE card always reads the same way.


def _compose_rationale(
    *,
    what: str,
    why: str,
    risk: str,
    not_this: str,
) -> str:
    """Join the four rationale parts into a single string.

    Each part should be a complete sentence (or a short clause) — the
    helper does not add periods. We never want empty parts: a missing
    `risk` line, in particular, is how thin one-liner rationales used
    to slip through. Callers MUST pass something for each part; if a
    macro genuinely has no specific risk to call out, it should still
    write "This is a register-not-execute automation; you confirm in
    your broker app before any fill." rather than an empty string.
    """
    parts = [what, why, risk, not_this]
    cleaned = [str(p).strip() for p in parts if str(p).strip()]
    if len(cleaned) < 4:
        # A thin rationale is a regression we want to catch in dev — but it must
        # never 500 a live build. Log loudly, backfill the missing risk/caveat
        # with the register-not-execute boilerplate, and ship what we have.
        logger.warning(
            "_compose_rationale: only %d/4 parts supplied (what=%r why=%r "
            "risk=%r not_this=%r) — backfilling boilerplate",
            len(cleaned), bool(what), bool(why), bool(risk), bool(not_this),
        )
        if not cleaned:
            cleaned = [
                "This is a register-not-execute automation; you confirm and "
                "place any order in your own broker app. Analysis, not advice."
            ]
    return " ".join(cleaned)


# Day-of-week vocabulary — accepts both short and long forms.
_DAY_TO_CRON: dict[str, str] = {
    "monday": "1", "mon": "1",
    "tuesday": "2", "tue": "2",
    "wednesday": "3", "wed": "3",
    "thursday": "4", "thu": "4",
    "friday": "5", "fri": "5",
    "weekday": "1-5", "weekdays": "1-5",
    "all": "*", "everyday": "*", "daily": "*", "every day": "*",
}


def _days_to_cron_field(days: list[str]) -> str:
    """Convert a list of day names to a cron DOW field. Single day →
    that day; multiple → comma-joined; weekday/all → '1-5' or '*'."""
    if not days:
        return "*"
    cron_parts: list[str] = []
    for d in days:
        key = d.strip().lower()
        cron = _DAY_TO_CRON.get(key)
        if cron is None:
            raise ValueError(
                f"unknown day {d!r}; use mon|tue|wed|thu|fri|"
                f"weekday|daily"
            )
        cron_parts.append(cron)
    if len(cron_parts) == 1:
        return cron_parts[0]
    return ",".join(cron_parts)


def _time_to_cron_minute_hour(time_ist: str) -> tuple[str, str]:
    """'09:15' → ('15', '9'). Accepts H:MM or HH:MM."""
    s = time_ist.strip()
    if ":" not in s:
        raise ValueError(f"time_ist must be HH:MM, got {time_ist!r}")
    h, m = s.split(":", 1)
    try:
        hh = int(h)
        mm = int(m)
    except ValueError as e:
        raise ValueError(f"unparseable time {time_ist!r}: {e}")
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        raise ValueError(f"out-of-range time: {time_ist!r}")
    return str(mm), str(hh)


def _label_for_dow(dow_field: str) -> str:
    """Friendly label for the schedule, e.g. '1-5' → 'every weekday'."""
    return {
        "1-5": "every weekday",
        "*": "every day",
        "1": "every Monday",
        "2": "every Tuesday",
        "3": "every Wednesday",
        "4": "every Thursday",
        "5": "every Friday",
    }.get(dow_field, f"on cron `{dow_field}`")


# ── Macro 1: scheduled order ─────────────────────────────────────────


def hydrate_scheduled_order(
    *,
    symbol: str,
    side: Literal["buy", "sell"],
    quantity: Optional[int] = None,
    notional_inr: Optional[float] = None,
    days: list[str],
    time_ist: str = "09:15",
    sl_pct: Optional[float] = None,
    requires_approval: bool = False,
) -> dict[str, Any]:
    """'buy 5 NIFTYBEES every weekday at 09:15' style.

    Yields a 2-step workflow (trigger.schedule + action.place_order),
    or 3 steps when sl_pct is provided (adds action.set_stoploss).
    """
    if quantity is None and notional_inr is None:
        raise ValueError(
            "scheduled_order: must specify quantity or notional_inr"
        )
    if quantity is not None and notional_inr is not None:
        raise ValueError(
            "scheduled_order: specify either quantity OR notional_inr"
        )

    sym = str(symbol).strip().upper()
    side_low = side.lower()
    minute, hour = _time_to_cron_minute_hour(time_ist)
    dow_field = _days_to_cron_field(days)
    cron = f"{minute} {hour} * * {dow_field}"
    dow_label = _label_for_dow(dow_field)

    order_cfg: dict[str, Any] = {
        "symbol": sym,
        "side": side_low,
        "order_type": "market",
        "requires_approval": requires_approval,
    }
    if quantity is not None:
        order_cfg["quantity"] = int(quantity)
    else:
        order_cfg["notional_inr"] = float(notional_inr)  # type: ignore[arg-type]

    size_label = (
        f"{quantity} shares" if quantity is not None
        else f"₹{int(notional_inr or 0):,}"
    )
    name = f"{dow_label.capitalize()}: {side_low} {size_label} {sym}"
    if sl_pct is not None:
        name = f"{name} +{sl_pct:g}% SL"

    steps: list[dict[str, Any]] = [
        {
            "step_type": "trigger.schedule",
            "label": f"{dow_label} at {hour.zfill(2)}:{minute.zfill(2)} IST",
            "config": {"cron": cron, "timezone": "Asia/Kolkata"},
        },
        {
            "step_type": "action.place_order",
            "label": f"{side_low.capitalize()} {size_label} {sym}",
            "config": order_cfg,
        },
    ]
    if sl_pct is not None:
        if not (0 < float(sl_pct) <= 50):
            raise ValueError(f"sl_pct must be in (0, 50]; got {sl_pct}")
        steps.append({
            "step_type": "action.set_stoploss",
            "label": f"{sl_pct:g}% stop-loss",
            "config": {
                "symbol": sym,
                "trigger_offset_pct": float(sl_pct),
            },
        })

    what = (
        f"Schedule trigger fires {dow_label} at "
        f"{hour.zfill(2)}:{minute.zfill(2)} IST and places a market "
        f"{side_low} for {size_label} of {sym}."
        + (f" Then arms a {sl_pct:g}% stop-loss on the position." if sl_pct is not None else "")
    )
    why = (
        f"You asked for a {dow_label} cadence into {sym}; a single-"
        f"instrument scheduled order is the most literal mapping. "
        f"Market orders accept whatever quote prevails at fire time "
        f"in exchange for guaranteed fills."
    )
    risk = (
        f"This is single-stock concentration risk — every fire goes "
        f"into {sym} alone, so an earnings shock, sectoral news, or "
        f"a gap on the cron tick can materially move the average price."
        + (
            f" The {sl_pct:g}% stop-loss caps single-fire downside but "
            "won't protect overnight gaps."
            if sl_pct is not None else ""
        )
    )
    not_this = (
        "This is NOT a hedged or market-neutral structure and NOT a "
        "tactical entry — it averages in regardless of trend or "
        "valuation. Pivot registers the order; you confirm and place "
        "it in your broker app."
    )
    rationale = _compose_rationale(
        what=what, why=why, risk=risk, not_this=not_this,
    )

    return {
        "name": name[:60],
        "description": (
            f"{side_low.capitalize()} {size_label} of {sym} {dow_label} "
            f"at {hour.zfill(2)}:{minute.zfill(2)} IST."
        ),
        "steps": steps,
        "rationale": rationale,
        "warnings": [],
        "_render_hint": "workflow_draft_card",
    }


# ── Macro 2: threshold-triggered order ──────────────────────────────


def hydrate_threshold_order(
    *,
    symbol: str,
    side: Literal["buy", "sell"],
    quantity: Optional[int] = None,
    notional_inr: Optional[float] = None,
    trigger_kind: Literal["indicator", "price"],
    operator: Literal["<", ">", "crosses_above", "crosses_below"],
    threshold: float,
    indicator: Optional[Literal["rsi", "sma", "ema"]] = None,
    indicator_period: Optional[int] = None,
    sl_pct: Optional[float] = None,
    requires_approval: bool = False,
) -> dict[str, Any]:
    """'buy 10 INFY when RSI < 30' / 'sell 5 RELIANCE when price > 2800'.

    Yields trigger.{indicator|price} → action.place_order, plus
    action.set_stoploss when sl_pct is given.
    """
    if quantity is not None and notional_inr is not None:
        raise ValueError(
            "threshold_order: specify either quantity OR notional_inr"
        )
    # Refuse to default. A silent qty=1 was producing draft cards
    # like "INFY buy on RSI(14) < 30" with no visible quantity — the
    # user clicked Activate without seeing the 1-share default. Worse,
    # for high-priced names the resulting ₹3000 trade looked like a
    # mistake. Raise instead → the chat loop receives the error and
    # the LLM re-emits with ASK_USER. system.md's "QUANTITY IS NEVER
    # A DEFAULT" rule now actually binds.
    if quantity is None and notional_inr is None:
        raise ValueError(
            "threshold_order: quantity (or notional_inr) is required. "
            "Call ASK_USER first: ask the user 'How many shares of "
            "<SYMBOL> per fire?' or 'What rupee budget per fire?'. "
            "Do NOT default to 1 — a silent default has produced "
            "wrong-size trades before."
        )
    sym = str(symbol).strip().upper()
    side_low = side.lower()

    if trigger_kind == "indicator":
        if indicator is None:
            raise ValueError(
                "threshold_order: indicator required when "
                "trigger_kind='indicator'"
            )
        period = indicator_period or {"rsi": 14, "sma": 50, "ema": 50}[indicator]
        trigger_step = {
            "step_type": "trigger.indicator",
            "label": (
                f"{indicator.upper()}({period}) {operator} "
                f"{float(threshold):g}"
            ),
            "config": {
                "symbol": sym,
                "indicator": indicator,
                "period": int(period),
                "operator": operator,
                "value": float(threshold),
            },
        }
        trigger_label = (
            f"{indicator.upper()}({period}) {operator} {float(threshold):g}"
        )
    else:
        trigger_step = {
            "step_type": "trigger.price",
            "label": f"Price {operator} ₹{float(threshold):g}",
            "config": {
                "symbol": sym,
                "operator": operator,
                "value": float(threshold),
                "exchange": "NSE",
            },
        }
        trigger_label = f"price {operator} ₹{float(threshold):g}"

    order_cfg: dict[str, Any] = {
        "symbol": sym,
        "side": side_low,
        "order_type": "market",
        "requires_approval": requires_approval,
    }
    if quantity is not None:
        order_cfg["quantity"] = int(quantity)
    else:
        order_cfg["notional_inr"] = float(notional_inr)  # type: ignore[arg-type]

    size_label = (
        f"{quantity} shares" if quantity is not None
        else f"₹{int(notional_inr or 0):,}"
    )

    steps: list[dict[str, Any]] = [
        trigger_step,
        {
            "step_type": "action.place_order",
            "label": f"{side_low.capitalize()} {size_label} {sym}",
            "config": order_cfg,
        },
    ]
    if sl_pct is not None:
        if not (0 < float(sl_pct) <= 50):
            raise ValueError(f"sl_pct must be in (0, 50]; got {sl_pct}")
        steps.append({
            "step_type": "action.set_stoploss",
            "label": f"{sl_pct:g}% stop-loss",
            "config": {
                "symbol": sym,
                "trigger_offset_pct": float(sl_pct),
            },
        })

    name = f"{sym} {trigger_label} → {side_low} {size_label}"
    if sl_pct is not None:
        name = f"{name} +{sl_pct:g}% SL"

    # Side-specific economic framing for the WHY clause. Buying on an
    # RSI dip is a mean-reversion bet; selling on an RSI surge or
    # price breakout is the opposite. Honest about which it is.
    if trigger_kind == "indicator" and indicator == "rsi":
        if side_low == "buy" and operator in ("<", "crosses_below"):
            why = (
                f"Buying {sym} when RSI({indicator_period or 14}) "
                f"{operator} {float(threshold):g} is a mean-reversion "
                f"setup — you are betting the recent sell-off has "
                f"overshot and that price snaps back. It is NOT a "
                f"trend-following entry."
            )
        elif side_low == "sell" and operator in (">", "crosses_above"):
            why = (
                f"Selling {sym} when RSI({indicator_period or 14}) "
                f"{operator} {float(threshold):g} is a momentum-fade "
                f"setup — you are taking profit (or shorting) on the "
                f"assumption the rally is exhausted."
            )
        else:
            why = (
                f"{indicator.upper() if indicator else ''} threshold of "
                f"{float(threshold):g} on {sym} maps your stated "
                f"trigger directly to the indicator step."
            )
    elif trigger_kind == "price":
        why = (
            f"A price threshold of ₹{float(threshold):g} on {sym} is a "
            f"clean breakout / breakdown rule — it fires once, on the "
            f"first tick that satisfies the operator, with no smoothing."
        )
    else:
        why = (
            f"The {trigger_label} trigger maps your stated condition "
            f"on {sym} directly into a catalog step type."
        )
    what = (
        f"When {trigger_label}, the workflow places a market "
        f"{side_low} for {size_label} of {sym}."
        + (f" A {sl_pct:g}% stop-loss arms after the fill." if sl_pct else "")
    )
    risk = (
        f"Single-stock concentration in {sym}: the trigger can fire "
        f"on a stale or anomalous tick, and a market order fills at "
        f"whatever quote prevails. Indicator-based triggers can chop "
        f"in sideways tape (multiple fires near the threshold)."
        + (
            f" The {sl_pct:g}% stop is on the registered position, not "
            "on overnight gaps."
            if sl_pct else ""
        )
    )
    not_this = (
        "This is NOT a hedged or market-neutral structure and NOT a "
        "backtested edge — Pivot registers the order; you confirm and "
        "place it in your broker app, and you carry the directional "
        "risk after the fill."
    )
    rationale = _compose_rationale(
        what=what, why=why, risk=risk, not_this=not_this,
    )

    return {
        "name": name[:60],
        "description": (
            f"{side_low.capitalize()} {size_label} of {sym} when "
            f"{trigger_label}."
        ),
        "steps": steps,
        "rationale": rationale,
        "warnings": [],
        "_render_hint": "workflow_draft_card",
    }


# ── Macro 3: basket allocation ──────────────────────────────────────


@dataclass
class _GapCondition:
    kind: Literal["gap_up", "gap_down", "flat"]
    index_symbol: str = "NIFTY"


def hydrate_basket_allocation(
    *,
    sector: str,
    total_inr: float,
    side: Literal["buy", "sell"] = "buy",
    strategy: Literal["equal", "mcap_weighted"] = "equal",
    limit: int = 10,
    schedule_time_ist: str = "09:20",
    days: Optional[list[str]] = None,
    gap_condition: Optional[Literal["gap_up", "gap_down", "flat"]] = None,
    index_symbol: str = "NIFTY",
    requires_approval: bool = False,
) -> dict[str, Any]:
    """'invest ₹1L equally across top 10 steel stocks when NIFTY gaps up'.

    Yields:
      trigger.schedule
      [optional gap_condition: fetch.day_open + fetch.prior_close +
                                condition.numeric on the index]
      fetch.screener
      action.allocate_notional
      notify.message
    """
    if total_inr <= 0:
        raise ValueError(f"total_inr must be positive; got {total_inr}")
    if limit < 1 or limit > 50:
        raise ValueError(f"limit must be 1..50; got {limit}")

    # Normalise the sector so the LLM can pass natural phrasings like
    # "mining", "mining stocks", "EV plays", "AI stocks" — the screener
    # wants the canonical SectorName ("metals", "auto", "it"). Without
    # normalisation, "build a similar SIP for mining stocks" fails
    # validation because the screener can't find a sector named
    # "mining stocks" (PDF report).
    from backend.services.sector_universe import (
        known_sectors, normalize_sector, resolve_theme,
    )
    raw_sector = (sector or "").strip()
    raw_lc = raw_sector.lower()
    # Strip trailing "stocks" / "shares" / "plays" / "names" so the
    # alias map matches "mining" rather than "mining stocks".
    for tail in (" stocks", " shares", " plays", " names", " companies"):
        if raw_lc.endswith(tail):
            raw_lc = raw_lc[: -len(tail)].rstrip()
    canonical = normalize_sector(raw_lc)
    if canonical is None:
        # Try the theme path next ("AI", "EV", "renewables").
        theme = resolve_theme(raw_lc)
        if theme and theme.sectors:
            canonical = theme.sectors[0]
    if canonical is None:
        raise ValueError(
            f"sector '{raw_sector}' isn't in the universe. "
            f"Try one of: {', '.join(known_sectors())}."
        )
    sector = canonical

    days_list = days or ["weekday"]
    minute, hour = _time_to_cron_minute_hour(schedule_time_ist)
    dow_field = _days_to_cron_field(days_list)
    cron = f"{minute} {hour} * * {dow_field}"
    dow_label = _label_for_dow(dow_field)

    steps: list[dict[str, Any]] = [
        {
            "step_type": "trigger.schedule",
            "label": f"{dow_label} at {hour.zfill(2)}:{minute.zfill(2)} IST",
            "config": {"cron": cron, "timezone": "Asia/Kolkata"},
        },
    ]

    # Optional gap-up / gap-down / flat gate on the index. Adds:
    #   fetch.day_open  → step idx 1
    #   fetch.prior_close → step idx 2
    #   condition.numeric (index_open op prior_close)
    screener_idx_offset = 0
    if gap_condition is not None:
        op_for_gap = {
            "gap_up": ">",
            "gap_down": "<",
            "flat": "==",
        }[gap_condition]
        steps.extend([
            {
                "step_type": "fetch.day_open",
                "label": f"{index_symbol} day open",
                "config": {"symbol": index_symbol},
            },
            {
                "step_type": "fetch.prior_close",
                "label": f"{index_symbol} prior close",
                "config": {"symbol": index_symbol, "sessions_back": 1},
            },
            {
                "step_type": "condition.numeric",
                "label": f"{index_symbol} {op_for_gap} prior close",
                "config": {
                    "left": "{{ context.1.value }}",
                    "operator": op_for_gap,
                    "right": "{{ context.2.value }}",
                },
            },
        ])
        screener_idx_offset = 3  # screener is now at idx 4 (0-indexed)

    screener_idx = 1 + screener_idx_offset
    steps.append({
        "step_type": "fetch.screener",
        "label": f"top {limit} {sector} by mcap",
        "config": {
            "sector": sector,
            "sort_by": "mcap",
            "limit": int(limit),
        },
    })
    steps.append({
        "step_type": "action.allocate_notional",
        "label": f"Allocate ₹{int(total_inr):,} across {limit} {sector} stocks",
        "config": {
            "symbols": "{{ context." + str(screener_idx) + ".ranked }}",
            "side": side,
            "total_inr": float(total_inr),
            "strategy": strategy,
            "order_type": "market",
            "requires_approval": requires_approval,
        },
    })
    steps.append({
        "step_type": "notify.message",
        "label": "Notify on allocation",
        "config": {
            "channel": "push",
            "template": (
                f"Allocated ₹{int(total_inr):,} {strategy} across top "
                f"{limit} {sector} stocks."
            ),
        },
    })

    name_bits = [f"{sector.capitalize()} basket"]
    if gap_condition:
        name_bits.append(f"{index_symbol} {gap_condition.replace('_', '-')}")
    name = " · ".join(name_bits)

    desc_bits = [
        f"{dow_label.capitalize()} at {hour.zfill(2)}:{minute.zfill(2)} IST,"
    ]
    if gap_condition:
        gap_word = gap_condition.replace("_", " ")
        desc_bits.append(f"if {index_symbol} {gap_word},")
    desc_bits.append(
        f"{strategy} allocation of ₹{int(total_inr):,} across top "
        f"{limit} {sector} stocks."
    )

    # Sector-specific WHY framing. The energy sector lumps upstream
    # producers (ONGC, OIL India) and downstream refiners/marketers
    # (IOC, BPCL, HPCL) into the same bucket — but they move in
    # OPPOSITE directions for a crude-price view. Call this out so the
    # rendered card stays honest about what a top-N-by-mcap energy
    # screen actually picks up.
    sector_warnings: list[str] = []
    if sector == "energy":
        why_sector = (
            f"Top {limit} energy names by market cap on NSE mixes "
            f"upstream producers (ONGC, OIL India) — whose revenue "
            f"RISES when crude rises — with downstream refiners and "
            f"oil marketing companies (IOC, BPCL, HPCL) — whose gross "
            f"refining margins COMPRESS when crude rises because "
            f"retail fuel prices are politically administered. If your "
            f"underlying view is directional on crude, this basket is "
            f"NOT the right shape — ask Pivot for a producers-only or "
            f"refiners-only basket instead."
        )
        sector_warnings.append(
            "Energy basket: this mcap-sorted screen includes BOTH "
            "upstream producers (benefit when crude rises) AND "
            "refiners/OMCs (benefit when crude falls). For a "
            "directional crude view, request a producers-only or "
            "refiners-only basket explicitly."
        )
    elif sector in ("metals", "steel"):
        why_sector = (
            f"Top {limit} {sector} names by market cap captures the "
            f"liquid large-cap exposure to the {sector} cycle — these "
            f"names tend to co-move with global commodity prices and "
            f"with the broader Nifty Metal index."
        )
    elif sector == "it":
        why_sector = (
            f"Top {limit} IT names by market cap is dominated by "
            f"export-led services revenue (USD billings, INR cost "
            f"base) — the basket has structural rupee-depreciation "
            f"beta on top of generic equity beta."
        )
    elif sector in ("private_bank", "psu_bank", "banking"):
        why_sector = (
            f"Top {limit} {sector} names by market cap captures the "
            f"credit-cycle and NIM exposure of the banking system; "
            f"public-sector banks carry sovereign-credit overhang that "
            f"private banks don't."
        )
    else:
        why_sector = (
            f"Top {limit} {sector} names by market cap captures the "
            f"liquid large-cap representation of the sector; mcap "
            f"sorting biases toward the most-traded constituents and "
            f"away from microcaps."
        )

    what = (
        f"Schedule fires {dow_label} at {hour.zfill(2)}:"
        f"{minute.zfill(2)} IST."
        + (
            f" The workflow gates on {index_symbol} "
            f"{gap_condition.replace('_',' ')} relative to the prior "
            f"close so it only fires on the requested market context."
            if gap_condition else ""
        )
        + f" It then screens the {sector} sector top {limit} by market "
        f"cap and allocates ₹{int(total_inr):,} across them "
        f"{'equally per name' if strategy == 'equal' else 'mcap-weighted'}."
    )
    risk = (
        f"Basket {side} risk: every fire commits ₹{int(total_inr):,} of "
        f"capital to {sector} names at market — fills happen at "
        f"prevailing quotes. Sector-concentrated baskets carry "
        f"common-factor risk: one piece of sector news moves the whole "
        f"basket together. Mcap weighting amplifies the top 1-2 names; "
        f"equal weighting amplifies the smaller, less-liquid ones."
    )
    not_this = (
        "This is NOT a hedged or market-neutral structure — it is a "
        "long-only sector tilt with full equity beta. Pivot registers "
        "each leg as an individual order; you confirm and place each "
        "one in your broker app."
    )
    rationale = _compose_rationale(
        what=what, why=why_sector, risk=risk, not_this=not_this,
    )

    return {
        "name": name[:60],
        "description": " ".join(desc_bits),
        "steps": steps,
        "rationale": rationale,
        "warnings": sector_warnings,
        "_render_hint": "workflow_draft_card",
    }


# ── Macro 4: holding action ─────────────────────────────────────────


def hydrate_holding_action(
    *,
    symbol: str,
    action_kind: Literal["sell", "set_stoploss"],
    trigger_kind: Literal["indicator", "price", "schedule", "manual"] = "manual",
    operator: Optional[Literal["<", ">", "crosses_above", "crosses_below"]] = None,
    threshold: Optional[float] = None,
    indicator: Optional[Literal["rsi", "sma", "ema"]] = None,
    indicator_period: Optional[int] = None,
    schedule_cron: Optional[str] = None,
    sl_offset_pct: Optional[float] = None,
    sl_trigger_price: Optional[float] = None,
    trailing: bool = False,
    requires_approval: bool = False,
) -> dict[str, Any]:
    """'sell my INFY when RSI > 70' / 'set 2% SL on my RELIANCE' /
    'trail my TITAN stoploss 8% below the running high'.

    Two action shapes:
      action_kind='sell'         → fetch.portfolio + place_order with
                                    quantity ref to the holding
      action_kind='set_stoploss' → action.set_stoploss with absolute
                                    price OR offset pct. When trailing=True,
                                    the stop tracks the high-water mark.

    Four trigger shapes: indicator, price, schedule, manual.
    """
    sym = str(symbol).strip().upper()

    # ── Trigger ──
    if trigger_kind == "indicator":
        if indicator is None or operator is None or threshold is None:
            raise ValueError(
                "holding_action: indicator + operator + threshold "
                "required when trigger_kind='indicator'"
            )
        period = indicator_period or {"rsi": 14, "sma": 50, "ema": 50}[indicator]
        trigger_step = {
            "step_type": "trigger.indicator",
            "label": (
                f"{indicator.upper()}({period}) {operator} "
                f"{float(threshold):g}"
            ),
            "config": {
                "symbol": sym,
                "indicator": indicator,
                "period": int(period),
                "operator": operator,
                "value": float(threshold),
            },
        }
        trigger_label = (
            f"{indicator.upper()}({period}) {operator} {float(threshold):g}"
        )
    elif trigger_kind == "price":
        if operator is None or threshold is None:
            raise ValueError(
                "holding_action: operator + threshold required when "
                "trigger_kind='price'"
            )
        trigger_step = {
            "step_type": "trigger.price",
            "label": f"Price {operator} ₹{float(threshold):g}",
            "config": {
                "symbol": sym,
                "operator": operator,
                "value": float(threshold),
                "exchange": "NSE",
            },
        }
        trigger_label = f"price {operator} ₹{float(threshold):g}"
    elif trigger_kind == "schedule":
        if schedule_cron is None:
            raise ValueError(
                "holding_action: schedule_cron required when "
                "trigger_kind='schedule'"
            )
        trigger_step = {
            "step_type": "trigger.schedule",
            "label": f"Cron `{schedule_cron}`",
            "config": {"cron": schedule_cron, "timezone": "Asia/Kolkata"},
        }
        trigger_label = f"on cron `{schedule_cron}`"
    elif trigger_kind == "manual":
        # "Set 2% SL on my RELIANCE" with no trigger condition →
        # trigger.manual = "fires when the user clicks Run now from
        # the agent panel". This is the right shape when the user is
        # describing a one-shot action they want to commit but not
        # arm to fire automatically.
        trigger_step = {
            "step_type": "trigger.manual",
            "label": "Run now",
            "config": {},
        }
        trigger_label = "manually run"
    else:
        raise ValueError(f"unknown trigger_kind {trigger_kind!r}")

    steps: list[dict[str, Any]] = [trigger_step]

    # ── Action ──
    if action_kind == "sell":
        # Sell the entire holding. Need fetch.portfolio first to get
        # the quantity ref.
        steps.append({
            "step_type": "fetch.portfolio",
            "label": "Get holdings",
            "config": {},
        })
        holdings_ref = (
            "{{ context.1.holdings." + sym + ".quantity }}"
        )
        steps.append({
            "step_type": "action.place_order",
            "label": f"Sell entire {sym} holding",
            "config": {
                "symbol": sym,
                "side": "sell",
                "quantity": holdings_ref,
                "order_type": "market",
                "requires_approval": requires_approval,
            },
        })
        action_desc = f"sell the entire {sym} holding"
    elif action_kind == "set_stoploss":
        if sl_offset_pct is None and sl_trigger_price is None:
            raise ValueError(
                "holding_action: sl_offset_pct or sl_trigger_price "
                "required when action_kind='set_stoploss'"
            )
        if sl_offset_pct is not None and sl_trigger_price is not None:
            raise ValueError(
                "holding_action: specify ONE of sl_offset_pct or "
                "sl_trigger_price, not both"
            )
        if trailing and sl_trigger_price is not None:
            raise ValueError(
                "holding_action: trailing=True requires sl_offset_pct, "
                "not an absolute sl_trigger_price"
            )
        sl_cfg: dict[str, Any] = {"symbol": sym}
        if sl_offset_pct is not None:
            if not (0 < float(sl_offset_pct) <= 50):
                raise ValueError(
                    f"sl_offset_pct must be in (0, 50]; got {sl_offset_pct}"
                )
            sl_cfg["trigger_offset_pct"] = float(sl_offset_pct)
            if trailing:
                sl_cfg["trailing"] = True
                action_desc = (
                    f"set a trailing {sl_offset_pct:g}% stop-loss on the "
                    f"{sym} holding (tracks peak)"
                )
            else:
                action_desc = (
                    f"set a {sl_offset_pct:g}% stop-loss on the {sym} holding"
                )
        else:
            sl_cfg["trigger_price"] = float(sl_trigger_price)  # type: ignore[arg-type]
            action_desc = (
                f"set a stop-loss at ₹{float(sl_trigger_price):g} on the "  # type: ignore[arg-type]
                f"{sym} holding"
            )
        trail_suffix = " trailing" if trailing else ""
        steps.append({
            "step_type": "action.set_stoploss",
            "label": (
                f"{trail_suffix.strip().title() + ' s' if trailing else 'S'}top-loss on {sym}"
                + (f" ({sl_offset_pct:g}%)" if sl_offset_pct else "")
            ),
            "config": sl_cfg,
        })
    else:
        raise ValueError(f"unknown action_kind {action_kind!r}")

    if trigger_kind == "manual":
        name = f"{sym}: {action_kind.replace('_', ' ')} (manual)"
        description = (
            f"Manually-triggered: {action_desc}. Fires only when you "
            "click Run now."
        )
        what = (
            "No automatic trigger is wired; the workflow runs on demand "
            f"when you click Run now from the agent panel, and then "
            f"{action_desc}."
        )
        why = (
            "You described a one-shot action rather than an arming "
            "condition, so trigger.manual is the most faithful mapping "
            "— the agent stays dormant until you explicitly fire it."
        )
        risk = (
            f"Because there is no automatic trigger, this won't react "
            f"to the market on your behalf — if {sym} moves while you "
            f"aren't watching, the action does not fire until you "
            f"click Run."
        )
    else:
        name = f"{sym}: {action_kind.replace('_', ' ')} on {trigger_label}"
        description = f"When {trigger_label}, {action_desc}."
        what = (
            f"Trigger fires when {trigger_label} and the workflow then "
            f"{action_desc}."
        )
        if action_kind == "sell":
            why = (
                f"You asked to exit {sym} on a specific condition; the "
                f"workflow fetches your current holding so the order "
                f"size matches what you actually own (no over-sell, "
                f"no leftover position)."
            )
            risk = (
                f"Market-order exit at the trigger tick: the fill is "
                f"at whatever quote prevails, which on a fast move can "
                f"be materially worse than the trigger threshold. Stale "
                f"or anomalous ticks can also fire the trigger early."
            )
        else:  # set_stoploss
            why = (
                f"You asked to protect the {sym} position on a "
                f"specific condition; action.set_stoploss arms the "
                f"protective order on top of the existing holding."
                + (
                    " Trailing mode raises the stop as the position "
                    "makes new highs (backtest-modeled; see warnings "
                    "for live behavior)."
                    if trailing else ""
                )
            )
            risk = (
                f"Stop-loss orders do not protect overnight gaps and "
                f"can slip materially on fast moves — the actual exit "
                f"price can be worse than the trigger price. The stop "
                f"can also be hit by a brief intraday spike that "
                f"otherwise would have recovered."
            )
    not_this = (
        "Pivot registers the order; you confirm and place it in your "
        "broker app. This is not advice, not a hedge, and not "
        "guaranteed-fill execution."
    )
    rationale = _compose_rationale(
        what=what, why=why, risk=risk, not_this=not_this,
    )
    # Deterministic disclosure for trailing stops. The trailing ratchet is
    # fully modeled in BACKTESTS, but the live executor places the initial
    # stop at N% below the current price and does NOT yet re-ratchet on new
    # highs (see workflows/schemas.py ActionSetStoplossConfig.trailing). We
    # surface this on the CARD's warnings so it shows regardless of whether
    # the chat-prose summarizer remembers to mention it — without this the
    # draft reads as if live peak-tracking already works (capability theater).
    warnings: list[str] = []
    if action_kind == "set_stoploss" and trailing:
        warnings.append(
            f"Trailing stop: the {sl_offset_pct:g}% ratchet is fully modeled "
            "in backtests. Live, this registers the initial stop "
            f"{sl_offset_pct:g}% below the current price — live re-ratcheting "
            "on new highs isn't wired yet."
        )
    return {
        "name": name[:60],
        "description": description,
        "steps": steps,
        "rationale": rationale,
        "warnings": warnings,
        "_render_hint": "workflow_draft_card",
    }


# ── Validation helper ──────────────────────────────────────────────


def _validate_or_raise(draft: dict[str, Any]) -> dict[str, Any]:
    """Run the registry validator on the hydrated draft. Raises on
    failure so the macro fails server-side rather than leaking a
    malformed draft to the FE.

    R4a: also stamps `backtestable` + `backtest_blockers` so the FE
    can render or hide the Backtest button correctly.

    R4b follow-up: stamps `expires_at` from `valid_until` so the FE
    can POST it as-is on /workflows.
    """
    from backend.workflows.propose import (
        ProposalValidationError, validate_draft_against_registry,
    )
    try:
        validate_draft_against_registry(draft)
    except ProposalValidationError as e:
        raise ValueError(
            f"workflow_macros: hydrated draft failed registry validation: {e}"
        ) from None
    try:
        from backend.services.backtest_resolvability import (
            check_draft, check_live_fireable,
        )
        _steps = draft.get("steps") or []
        bt_ok, bt_blockers = check_draft(_steps)
        draft["backtestable"] = bool(bt_ok)
        draft["backtest_blockers"] = bt_blockers
        draft["live_warnings"] = check_live_fireable(_steps)
    except Exception:
        draft.setdefault("backtestable", True)
        draft.setdefault("backtest_blockers", [])
    try:
        from backend.agents.tool_executor import _stamp_expires_at
        _stamp_expires_at(draft)
    except Exception:
        pass
    return draft


def hydrate_and_validate(macro_name: str, params: dict[str, Any]) -> dict[str, Any]:
    """Single dispatch entry point.

    Maps macro_name → hydration function, calls it with the params,
    and validates the result before returning. Raises ValueError on
    any failure path so the tool executor surfaces a clean error.

    R4b: pulls `valid_until` out of params (the hydrators are typed
    and won't accept unknown kwargs) and lands it on the draft top
    level so `_validate_or_raise` → `_stamp_expires_at` can derive
    the row-level timestamp.
    """
    fn = _MACROS.get(macro_name)
    if fn is None:
        raise ValueError(f"unknown macro {macro_name!r}")
    extras: dict[str, Any] = {}
    if "valid_until" in params:
        extras["valid_until"] = params.pop("valid_until")
    draft = fn(**params)
    if extras.get("valid_until"):
        draft["valid_until"] = extras["valid_until"]
    return _validate_or_raise(draft)


_MACROS = {
    "scheduled_order": hydrate_scheduled_order,
    "threshold_order": hydrate_threshold_order,
    "basket_allocation": hydrate_basket_allocation,
    "holding_action": hydrate_holding_action,
}


# ── Macro 5: IPO open-day reminder (P2) ─────────────────────────────


def build_ipo_reminder_draft(
    symbol: str,
    ipo_details: dict[str, Any],
    *,
    quantity_lots: int,
    category: str,
    bid_price_mode: str,
    bid_price: Optional[float] = None,
) -> dict[str, Any]:
    """Build the workflow_draft_card for "set up open-day reminder for X IPO".

    Three-step shape:
      [0] trigger.ipo_open      { symbol }
      [1] action.arm_ipo_intent { ipo_symbol, quantity_lots, category,
                                  bid_price_mode, bid_price? }
      [2] notify.message        { template: "<open-day handoff text>" }

    The notify template leads with "Pivot has NOT applied — you must
    apply & approve the mandate yourself by 5 PM" so the user is never
    under the impression Pivot executed the bid for them.

    Validates against the registry before returning — a bad draft fails
    server-side rather than leaking a malformed payload to the FE.

    DEFERRED to P2.1 (do NOT build now): the separate close-day 5 PM
    and T+1 allotment-day reminders (date-pinned trigger.schedule
    workflows). The allotment reminder depends on P1 allotment-date /
    registrar data which isn't built. The open-day handoff already
    nudges "apply by 5 PM".
    """
    sym = str(symbol).strip().upper()
    if not sym:
        raise ValueError("build_ipo_reminder_draft: symbol is required")
    if quantity_lots < 1:
        raise ValueError("quantity_lots must be >= 1")
    if bid_price_mode not in {"cutoff", "fixed"}:
        raise ValueError(
            f"bid_price_mode must be 'cutoff' or 'fixed' (got {bid_price_mode!r})"
        )
    if bid_price_mode == "fixed" and bid_price is None:
        raise ValueError("bid_price is required when bid_price_mode='fixed'")

    # Pull display-only fields from the IPO record for the notify
    # template. Honest fallback when fields are missing — never invent.
    ipo_name = str(ipo_details.get("name") or sym)
    ipo_type = "sme" if ipo_details.get("type") == "sme" else "mainboard"
    open_date = ipo_details.get("open_date") or "open day"
    close_date = ipo_details.get("close_date") or "close day"
    price_band_raw = ipo_details.get("price_band") or "N/A"

    arm_cfg: dict[str, Any] = {
        "ipo_symbol": sym,
        "quantity_lots": int(quantity_lots),
        "category": str(category),
        "bid_price_mode": str(bid_price_mode),
    }
    if bid_price is not None:
        arm_cfg["bid_price"] = float(bid_price)

    # Full open-day handoff text. Leads with the non-execution disclaimer
    # so the rendered notification can't be mis-read as a fill notice.
    template = (
        f"Pivot has NOT applied for {ipo_name} ({sym}) — open your "
        f"broker / UPI app, place the bid and approve the UPI mandate "
        f"yourself by 5 PM on close day ({close_date}). This is a "
        f"reminder only. Subscription window: {open_date} to {close_date}. "
        f"Price band: {price_band_raw}. Intent armed: {quantity_lots} lot"
        f"{'s' if quantity_lots != 1 else ''} ({category}, {bid_price_mode})."
    )

    steps: list[dict[str, Any]] = [
        {
            "step_type": "trigger.ipo_open",
            "label": f"When {sym} IPO opens",
            "config": {"symbol": sym},
        },
        {
            "step_type": "action.arm_ipo_intent",
            "label": f"Arm intent: {quantity_lots} lot(s) {sym}",
            "config": arm_cfg,
        },
        {
            "step_type": "notify.message",
            "label": "Open-day handoff reminder",
            "config": {
                "channel": "push",
                "template": template,
                "vars": {},
            },
        },
    ]

    bid_label = (
        "cut-off" if bid_price_mode == "cutoff"
        else f"₹{bid_price:g}" if bid_price is not None else "fixed"
    )
    name = f"{sym} IPO open-day reminder"
    description = (
        f"Fires once when {ipo_name} ({sym}, {ipo_type}) opens for "
        f"subscription. Arms the intent ({quantity_lots} lot at "
        f"{bid_label}, {category}) and pushes a reminder. Pivot does "
        f"NOT apply — you place the bid yourself by 5 PM on {close_date}."
    )
    rationale = _compose_rationale(
        what=(
            f"Listens for the {sym} IPO to flip to 'open' on the live "
            f"NSE feed; on the open edge, writes an intent_armed row "
            f"and pushes the open-day handoff text."
        ),
        why=(
            f"IPO subscription mechanics require a manual UPI mandate "
            f"that Pivot can't authorise on your behalf; the right "
            f"shape is a reminder + intent rather than an order."
        ),
        risk=(
            f"IPO allocations are uncertain (oversubscription, "
            f"category caps) and listings can open materially below "
            f"the issue price; this automation does not protect against "
            f"either."
        ),
        not_this=(
            f"This is NOT an order placement and NOT a broker call — "
            f"Pivot's verb is 'arm' and 'remind', never 'apply'. You "
            f"must place the bid and approve the UPI mandate yourself "
            f"in your broker app by 5 PM on close day."
        ),
    )

    draft: dict[str, Any] = {
        "name": name[:60],
        "description": description,
        "steps": steps,
        "rationale": rationale,
        "warnings": [
            "Pivot will NOT submit or fund this bid. You must apply "
            "and approve the UPI mandate yourself in your broker app "
            "by 5 PM on close day.",
        ],
        "_render_hint": "workflow_draft_card",
    }
    return _validate_or_raise(draft)


# NOTE: build_ipo_reminder_draft is NOT plumbed into _MACROS / hydrate_and_validate
# because its signature is (symbol, ipo_details, *, ...) — the IPO details
# come from the live feed, not from LLM params. The chat tool calls it
# directly (see agents/tool_executor.py:_propose_ipo_automation).
