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

from dataclasses import dataclass, field
from typing import Any, Literal, Optional


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

    rationale = (
        f"{dow_label.capitalize()} at {hour.zfill(2)}:{minute.zfill(2)} "
        f"IST, place a market {side_low} for {size_label} of {sym}."
    )
    if sl_pct is not None:
        rationale += f" Apply a {sl_pct:g}% stop-loss after the fill."

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
    # Default to 1 share when the user didn't specify a size.
    # WHY: "Buy RELIANCE when RSI goes below 30" carries no quantity. The
    # LLM sometimes infers quantity=1 (TCS worked), sometimes omits it
    # (RELIANCE failed validation → "I couldn't complete that"). The LLM is
    # nondeterministic on this. Defaulting here makes the macro robust — the
    # card surfaces the value and the user can edit before confirming.
    if quantity is None and notional_inr is None:
        quantity = 1
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

    return {
        "name": name[:60],
        "description": (
            f"{side_low.capitalize()} {size_label} of {sym} when "
            f"{trigger_label}."
        ),
        "steps": steps,
        "rationale": (
            f"Trigger fires when {trigger_label}; market {side_low} for "
            f"{size_label} of {sym}."
            + (f" {sl_pct:g}% stop-loss after fill." if sl_pct else "")
        ),
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

    return {
        "name": name[:60],
        "description": " ".join(desc_bits),
        "steps": steps,
        "rationale": (
            f"Schedule fires {dow_label} at {hour.zfill(2)}:"
            f"{minute.zfill(2)} IST."
            + (f" Gates on {index_symbol} {gap_condition.replace('_',' ')}."
               if gap_condition else "")
            + f" Screens {sector} sector top {limit} by market cap and "
            f"allocates ₹{int(total_inr):,} {strategy}."
        ),
        "warnings": [],
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
    requires_approval: bool = False,
) -> dict[str, Any]:
    """'sell my INFY when RSI > 70' / 'set 2% SL on my RELIANCE'.

    Two action shapes:
      action_kind='sell'         → fetch.portfolio + place_order with
                                    quantity ref to the holding
      action_kind='set_stoploss' → action.set_stoploss with absolute
                                    price OR offset pct

    Three trigger shapes: indicator, price, schedule.
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
        sl_cfg: dict[str, Any] = {"symbol": sym}
        if sl_offset_pct is not None:
            if not (0 < float(sl_offset_pct) <= 50):
                raise ValueError(
                    f"sl_offset_pct must be in (0, 50]; got {sl_offset_pct}"
                )
            sl_cfg["trigger_offset_pct"] = float(sl_offset_pct)
            action_desc = (
                f"set a {sl_offset_pct:g}% stop-loss on the {sym} holding"
            )
        else:
            sl_cfg["trigger_price"] = float(sl_trigger_price)  # type: ignore[arg-type]
            action_desc = (
                f"set a stop-loss at ₹{float(sl_trigger_price):g} on the "  # type: ignore[arg-type]
                f"{sym} holding"
            )
        steps.append({
            "step_type": "action.set_stoploss",
            "label": (
                f"Stop-loss on {sym}"
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
        rationale = (
            "No automatic trigger requested; the workflow is set up to "
            f"run on demand and {action_desc}."
        )
    else:
        name = f"{sym}: {action_kind.replace('_', ' ')} on {trigger_label}"
        description = f"When {trigger_label}, {action_desc}."
        rationale = (
            f"Trigger fires when {trigger_label}; the workflow then "
            f"{action_desc}."
        )
    return {
        "name": name[:60],
        "description": description,
        "steps": steps,
        "rationale": rationale,
        "warnings": [],
        "_render_hint": "workflow_draft_card",
    }


# ── Validation helper ──────────────────────────────────────────────


def _validate_or_raise(draft: dict[str, Any]) -> dict[str, Any]:
    """Run the registry validator on the hydrated draft. Raises on
    failure so the macro fails server-side rather than leaking a
    malformed draft to the FE."""
    from backend.workflows.propose import (
        ProposalValidationError, validate_draft_against_registry,
    )
    try:
        validate_draft_against_registry(draft)
    except ProposalValidationError as e:
        raise ValueError(
            f"workflow_macros: hydrated draft failed registry validation: {e}"
        ) from None
    return draft


def hydrate_and_validate(macro_name: str, params: dict[str, Any]) -> dict[str, Any]:
    """Single dispatch entry point.

    Maps macro_name → hydration function, calls it with the params,
    and validates the result before returning. Raises ValueError on
    any failure path so the tool executor surfaces a clean error.
    """
    fn = _MACROS.get(macro_name)
    if fn is None:
        raise ValueError(f"unknown macro {macro_name!r}")
    draft = fn(**params)
    return _validate_or_raise(draft)


_MACROS = {
    "scheduled_order": hydrate_scheduled_order,
    "threshold_order": hydrate_threshold_order,
    "basket_allocation": hydrate_basket_allocation,
    "holding_action": hydrate_holding_action,
}
