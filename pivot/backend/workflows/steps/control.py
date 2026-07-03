"""Control-flow step executors.

Single-track only — no branching, no loops, no sub-workflows
(ARCHITECTURE.md §5.6 + §13). max_retries=0: retrying a sleep or a
skip-marker has no semantic meaning.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, time as dtime, timezone
from typing import Any, Optional

from pytz import timezone as pytz_timezone  # type: ignore[import-untyped]

from backend.workflows.registry import register_step
from backend.workflows.schemas import SkipIfConfig, WaitDelayConfig

logger = logging.getLogger(__name__)


# Cap a single wait.delay at 1h so a typo (1000000 seconds) can't
# silently consume an entire run's time budget. Engine's wall-clock
# budget will catch it eventually but failing fast is friendlier.
_WAIT_DELAY_MAX_SECONDS = 60 * 60


@register_step(
    step_type="wait.delay",
    category="control",
    label="Wait",
    description="Pause for a set duration, or until a specific time of day.",
    icon="timer",
    max_retries=0,
    trigger_only=False,
    config_model=WaitDelayConfig,
    output_schema=None,
    group="Flow",
)
async def execute_wait_delay(ctx: Any) -> Optional[dict[str, Any]]:
    """Sleep for `duration_seconds` OR until `until_time` (HH:MM in
    `timezone`, defaults to UTC). Run state persists across worker
    restarts because the engine writes the run-step row before sleeping
    and the wait is non-cumulative — re-entry wakes immediately if the
    target time has passed."""
    cfg = ctx.config
    duration = cfg.get("duration_seconds")
    until = cfg.get("until_time")

    if duration is None and until is None:
        raise ValueError(
            "wait.delay requires either duration_seconds or until_time"
        )

    seconds: float
    if duration is not None:
        seconds = float(int(duration))
        if seconds < 0:
            raise ValueError("duration_seconds must be >= 0")
    else:
        # until_time is HH:MM in `timezone` — sleep until next occurrence.
        tz_str = cfg.get("timezone", "UTC")
        try:
            tz = pytz_timezone(tz_str)
        except Exception as e:
            raise ValueError(f"unknown timezone: {tz_str}") from e
        try:
            hh_str, mm_str = str(until).split(":", 1)
            target = dtime(int(hh_str), int(mm_str), 0)
        except (ValueError, AttributeError) as e:
            raise ValueError(
                f"until_time must be HH:MM (got {until!r})"
            ) from e
        now = datetime.now(timezone.utc).astimezone(tz)
        target_today = now.replace(
            hour=target.hour, minute=target.minute,
            second=0, microsecond=0,
        )
        if target_today <= now:
            # Already past today → next occurrence tomorrow (24h ahead).
            target_today = target_today.replace(day=now.day + 1)
        seconds = (target_today - now).total_seconds()

    seconds = min(seconds, float(_WAIT_DELAY_MAX_SECONDS))
    if seconds > 0:
        await asyncio.sleep(seconds)
    return {"slept_seconds": int(seconds)}


@register_step(
    # Renamed from bare "skip_if" to "control.skip_if" per Day-1
    # contract audit (STATUS.md Day 1, fix 1).
    step_type="control.skip_if",
    category="control",
    label="Skip next step if…",
    description="Skip the following step when a condition holds.",
    icon="skip-forward",
    max_retries=0,
    trigger_only=False,
    config_model=SkipIfConfig,
    group="Flow",
    output_schema={
        "type": "object",
        "properties": {"skipped_next": {"type": "boolean"}},
        "required": ["skipped_next"],
    },
)
async def execute_control_skip_if(ctx: Any) -> Optional[dict[str, Any]]:
    """Evaluate the inner condition payload; if it holds, return
    `{skipped_next: True}`. The engine reads this output and marks the
    NEXT step's status as `skipped` without executing it.

    The `condition` config is a dict that mirrors a numeric / market /
    position / time_window condition's config. Refs in numeric
    sub-config were already resolved by the engine. We support:
      - {"type": "numeric", "left": ..., "operator": ..., "right": ...}
      - {"type": "market_status", "require": "open"|"closed"|"pre"|"post"}
      - {"type": "time_window", "start_time": ..., "end_time": ..., "timezone": ...}
    """
    cond = dict(ctx.config.get("condition") or {})
    ctype = cond.get("type") or "numeric"  # default for legacy payloads

    holds = False
    if ctype == "numeric":
        # Inline import to avoid circular: conditions module imports control
        # transitively via the registry side effect.
        from backend.workflows.steps.conditions import (
            _coerce_number, _evaluate,
        )
        left = _coerce_number(cond.get("left"), "left")
        right = _coerce_number(cond.get("right"), "right")
        op = str(cond.get("operator", "=="))
        holds = _evaluate(left, op, right)
    elif ctype == "market_status":
        from backend.utils.time_utils import is_market_open, is_trading_day
        require = str(cond.get("require", "open"))
        holds = _market_status_matches(require, is_market_open, is_trading_day)
    elif ctype == "time_window":
        holds = _time_in_window(
            str(cond.get("start_time", "00:00")),
            str(cond.get("end_time", "23:59")),
            str(cond.get("timezone", "UTC")),
        )
    else:
        raise ValueError(f"unsupported skip_if condition type: {ctype!r}")

    return {"skipped_next": bool(holds)}


def _market_status_matches(
    require: str, is_market_open_fn: Any, is_trading_day_fn: Any,
) -> bool:
    """Shared logic between condition.market_status and skip_if."""
    market_open = bool(is_market_open_fn()) and bool(is_trading_day_fn())
    if require == "open":
        return market_open
    if require == "closed":
        return not market_open
    # NSE: pre-open 09:00-09:15 IST, post-close after 15:30 IST.
    from backend.utils.time_utils import now_ist
    now = now_ist()
    if not is_trading_day_fn():
        return False
    if require == "pre":
        return (now.hour, now.minute) < (9, 15) and (now.hour >= 9)
    if require == "post":
        return now.hour > 15 or (now.hour == 15 and now.minute >= 30)
    raise ValueError(f"unknown market_status require: {require!r}")


def _time_in_window(start: str, end: str, tz_str: str) -> bool:
    """True when current time in `tz` is within [start, end] (HH:MM,
    inclusive). Doesn't handle window crossing midnight in v1."""
    try:
        tz = pytz_timezone(tz_str)
    except Exception as e:
        raise ValueError(f"unknown timezone: {tz_str}") from e
    try:
        sh, sm = (int(x) for x in start.split(":"))
        eh, em = (int(x) for x in end.split(":"))
    except ValueError as e:
        raise ValueError(
            f"start/end_time must be HH:MM (got {start!r}, {end!r})"
        ) from e
    now = datetime.now(timezone.utc).astimezone(tz).time()
    start_t = dtime(sh, sm)
    end_t = dtime(eh, em)
    if end_t < start_t:
        # Window doesn't cross midnight in v1.
        raise ValueError("time_window cannot cross midnight (v1)")
    return start_t <= now <= end_t
