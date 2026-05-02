"""Workflow scheduler — fires `trigger.schedule` workflows on cron.

Plugs into the existing `backend/scheduler.py` `AsyncIOScheduler`
(don't add a parallel scheduler — see ARCHITECTURE.md §3 stack table).

Two surfaces:

  1. `upsert_workflow_schedule(db, workflow)` — called by the workflows
     router on activate / pause / archive / step edits. Computes
     `next_run_at` from the workflow's `trigger.schedule` step (if any)
     when status is `active`; otherwise clears it. **Does not** touch
     non-schedule trigger types (`trigger.price`, etc. are armed by the
     watcher subprocess, not here).

  2. `register_workflow_scheduler(scheduler)` — called once at app
     startup. Adds a recurring poll job that scans the workflows table
     every `_POLL_INTERVAL_SECONDS` and fires every active workflow
     whose `next_run_at <= now()`. After firing, recomputes `next_run_at`
     so the next tick is armed.

Why poll instead of register one APScheduler job per workflow:
  - Workflows can be activated / paused / patched at any time; a poll
    loop is naturally consistent with the DB without bookkeeping.
  - Cheaper at scale (one job vs N jobs).
  - The existing `check_strategy_triggers` job uses the same pattern.

Cron validation: `compute_next_run_at()` raises `InvalidCronError` when
the expression is malformed. The activate handler calls this before
flipping status, so an invalid cron fails 422 (not silently arms a dead
schedule — closes reviewer Day-2 edge case #1).
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler  # type: ignore[import-untyped]
from apscheduler.triggers.cron import CronTrigger  # type: ignore[import-untyped]
from pytz import timezone as pytz_timezone  # type: ignore[import-untyped]
from sqlalchemy.orm import Session

from backend.database import SessionLocal
from backend.models import (
    RunStatus,
    Workflow,
    WorkflowRun,
    WorkflowStatus,
    WorkflowStep,
)

logger = logging.getLogger(__name__)


# Polling cadence. Cron resolution is per-minute; 30s gives us at most
# 30s of jitter past the cron tick — acceptable for v1.
_POLL_INTERVAL_SECONDS = 30

# Price/indicator watcher cadence. Per ARCHITECTURE.md §8: every 60s
# during market hours. We register the job unconditionally (cheap when
# no watch-trigger workflows are active) and short-circuit inside the
# job when the market is closed.
_WATCHER_INTERVAL_SECONDS = 60
_WATCHER_JOB_ID = "pivot_workflows_watcher"

# APScheduler job id for the workflow poll job — keep stable across
# restarts so `replace_existing=True` works.
_POLL_JOB_ID = "pivot_workflows_poll"


class InvalidCronError(ValueError):
    """Raised when a `trigger.schedule` cron expression is malformed.

    Routers catch this and emit 422 with `validation_error` code.
    """


def _trigger_schedule_step(workflow: Workflow) -> Optional[WorkflowStep]:
    """Return the `trigger.schedule` step at index 0, or None if the
    workflow's trigger is a different type (manual, price, etc.)."""
    for step in workflow.steps:
        if int(step.step_index) == 0:
            return step if str(step.step_type) == "trigger.schedule" else None
    return None


def compute_next_run_at(
    cron: str,
    tz_str: str,
    *,
    after: Optional[datetime] = None,
) -> datetime:
    """Compute the next fire time for a cron expression in the given
    IANA timezone, returned as a UTC-aware datetime.

    Raises `InvalidCronError` if either the cron or timezone is bad.
    """
    try:
        tz = pytz_timezone(tz_str)
    except Exception as e:  # pytz.UnknownTimeZoneError, etc.
        raise InvalidCronError(f"unknown timezone: {tz_str}") from e
    try:
        trigger = CronTrigger.from_crontab(cron, timezone=tz)
    except Exception as e:  # ValueError on malformed cron
        raise InvalidCronError(f"invalid cron expression: {cron}") from e
    base = after or datetime.now(timezone.utc)
    # APScheduler returns the next fire time strictly *after* `base`.
    next_fire = trigger.get_next_fire_time(None, base.astimezone(tz))
    if next_fire is None:
        raise InvalidCronError(
            f"cron {cron!r} produces no future fire time"
        )
    utc: datetime = next_fire.astimezone(timezone.utc)
    return utc


def upsert_workflow_schedule(db: Session, workflow: Workflow) -> None:
    """Set or clear `workflow.next_run_at` based on current state.

    Called by the workflows router on activate / pause / archive /
    PATCH-with-steps. Caller is responsible for `db.commit()`.

    Behavior:
      - If status == active AND step 0 is `trigger.schedule` →
        recompute `next_run_at` from the cron config. Raises
        `InvalidCronError` if the cron is bad — caller should let it
        bubble so the router emits 422.
      - Otherwise (paused / archived / draft / non-schedule trigger) →
        clear `next_run_at` so the poller skips this workflow.
    """
    if workflow.status != WorkflowStatus.active:
        workflow.next_run_at = None  # type: ignore[assignment]
        return

    step = _trigger_schedule_step(workflow)
    if step is None:
        # Active but trigger isn't schedule (manual, price, etc.) —
        # not our problem; clear `next_run_at` so the poller skips.
        workflow.next_run_at = None  # type: ignore[assignment]
        return

    raw_cfg: dict[str, object] = step.config or {}  # type: ignore[assignment]
    cfg: dict[str, object] = dict(raw_cfg) if raw_cfg else {}
    cron = str(cfg.get("cron", ""))
    tz_str = str(cfg.get("timezone", "UTC"))
    workflow.next_run_at = compute_next_run_at(cron, tz_str)  # type: ignore[assignment]


async def _poll_due_workflows() -> None:
    """Polled job: find every active workflow whose `next_run_at`
    has passed, create a `triggered_by='schedule'` run, hand it to the
    engine, and recompute `next_run_at` for the next tick.

    All DB work via sync sessions inside `asyncio.to_thread()` so the
    APScheduler loop never blocks on I/O.
    """
    fired_at = datetime.now(timezone.utc)

    def _fetch_due() -> list[str]:
        """Returns workflow IDs to fire. Runs in a worker thread."""
        db = SessionLocal()
        try:
            due = (
                db.query(Workflow)
                .filter(
                    Workflow.status == WorkflowStatus.active,
                    Workflow.next_run_at.isnot(None),
                    Workflow.next_run_at <= fired_at,
                )
                .all()
            )
            return [str(wf.id) for wf in due]
        finally:
            db.close()

    workflow_ids = await asyncio.to_thread(_fetch_due)
    if not workflow_ids:
        return

    logger.info(
        "[workflow-scheduler] firing %d due workflow(s) at %s",
        len(workflow_ids),
        fired_at.isoformat(),
    )

    for wf_id in workflow_ids:
        try:
            await _fire_one(wf_id, fired_at)
        except Exception:
            # Don't let one bad workflow kill the poll cycle.
            logger.exception(
                "[workflow-scheduler] failed to fire workflow %s", wf_id
            )


async def _fire_one(workflow_id: str, fired_at: datetime) -> None:
    """Create a scheduled run row, recompute next_run_at, hand to
    engine. All DB work via to_thread; engine is async."""

    def _create_run_and_recompute() -> Optional[str]:
        """Returns the new run_id, or None if the workflow vanished /
        was paused between fetch and fire."""
        db = SessionLocal()
        try:
            wf = db.query(Workflow).filter(Workflow.id == workflow_id).first()
            if wf is None or wf.status != WorkflowStatus.active:
                return None
            run = WorkflowRun(
                workflow_id=wf.id,
                workflow_version=int(wf.version),
                triggered_by="schedule",
                status=RunStatus.running,
                context={},
            )
            db.add(run)
            wf.last_run_at = fired_at  # type: ignore[assignment]
            try:
                upsert_workflow_schedule(db, wf)
            except InvalidCronError:
                # Cron became invalid since activation (e.g. step
                # patched after activation through some future path).
                # Clear so we don't retry forever.
                wf.next_run_at = None  # type: ignore[assignment]
            db.commit()
            db.refresh(run)
            return str(run.id)
        finally:
            db.close()

    run_id = await asyncio.to_thread(_create_run_and_recompute)
    if run_id is None:
        return

    # Engine is async; run it as a fire-and-forget task on the loop.
    # Imported lazily to avoid circular import at module load.
    from backend.workflows.engine import WorkflowEngine

    engine = WorkflowEngine()
    asyncio.create_task(engine.execute_run(run_id))


def register_workflow_scheduler(scheduler: AsyncIOScheduler) -> None:
    """Attach the workflow poll + watcher jobs to the existing scheduler.

    Idempotent: re-registering replaces the existing jobs.
    """
    scheduler.add_job(
        _poll_due_workflows,
        trigger="interval",
        seconds=_POLL_INTERVAL_SECONDS,
        id=_POLL_JOB_ID,
        name="Pivot Workflows — poll due schedules",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        _poll_watch_triggers,
        trigger="interval",
        seconds=_WATCHER_INTERVAL_SECONDS,
        id=_WATCHER_JOB_ID,
        name="Pivot Workflows — price / indicator watcher",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    logger.info(
        "[workflow-scheduler] registered poll job (%ss) + watcher (%ss)",
        _POLL_INTERVAL_SECONDS, _WATCHER_INTERVAL_SECONDS,
    )


# ── Price / indicator watcher ────────────────────────────────────────


# Price/indicator triggers store last_price under this key inside
# workflow_steps.config so the watcher can detect crossings on the
# next tick. Stored as a JSON-friendly float; absent on the first tick.
_LAST_PRICE_KEY = "_last_price"
_LAST_VALUE_KEY = "_last_value"  # for indicator triggers


async def _poll_watch_triggers() -> None:
    """Polled every 60s. During NSE market hours, scans active workflows
    whose first step is `trigger.price` or `trigger.indicator`,
    batch-fetches the relevant quotes / computes indicators, and fires
    runs when conditions match.

    Crossing semantics (`crosses_above` / `crosses_below`) require
    knowing the previous tick's value — we persist it under
    `workflow_steps.config[_last_price]` (or `_last_value`) so the
    next tick can compare.
    """
    from backend.utils.time_utils import is_market_open, is_trading_day

    # Out of market hours / weekend → cheap no-op.
    try:
        if not (is_trading_day() and is_market_open()):
            return
    except Exception:  # pragma: no cover — defensive
        return

    fired_at = datetime.now(timezone.utc)

    def _scan_active_watch_triggers() -> list[tuple[str, str, dict[str, object]]]:
        """Returns list of (workflow_id, step_type, config_copy)
        tuples. Runs in a worker thread."""
        db = SessionLocal()
        try:
            rows = (
                db.query(Workflow, WorkflowStep)
                .join(WorkflowStep, WorkflowStep.workflow_id == Workflow.id)
                .filter(
                    Workflow.status == WorkflowStatus.active,
                    WorkflowStep.step_index == 0,
                    WorkflowStep.step_type.in_(
                        ["trigger.price", "trigger.indicator"],
                    ),
                )
                .all()
            )
            return [
                (str(wf.id), str(step.step_type), dict(step.config or {}))
                for wf, step in rows
            ]
        finally:
            db.close()

    triggers = await asyncio.to_thread(_scan_active_watch_triggers)
    if not triggers:
        return

    # Group price triggers by symbol so we batch-fetch quotes once per
    # tick instead of once per workflow.
    price_symbols: set[str] = set()
    for _wf_id, step_type, cfg in triggers:
        if step_type == "trigger.price":
            sym = str(cfg.get("symbol", "")).upper()
            exch = str(cfg.get("exchange", "NSE")).upper()
            if sym:
                price_symbols.add(f"{exch}:{sym}")

    quotes: dict[str, float] = {}
    if price_symbols:
        try:
            quotes = await asyncio.to_thread(_batch_fetch_prices, sorted(price_symbols))
        except Exception:
            logger.exception("[watcher] price batch fetch failed")
            quotes = {}

    for wf_id, step_type, cfg in triggers:
        try:
            if step_type == "trigger.price":
                await _evaluate_price_trigger(wf_id, cfg, quotes, fired_at)
            elif step_type == "trigger.indicator":
                await _evaluate_indicator_trigger(wf_id, cfg, fired_at)
        except Exception:
            logger.exception(
                "[watcher] failed to evaluate %s for workflow %s",
                step_type, wf_id,
            )


def _batch_fetch_prices(instruments: list[str]) -> dict[str, float]:
    """Fetch live quotes for a batch of instruments. Uses Kite-mock-mode
    when no key is configured. Returns {instrument: ltp} for every
    instrument that has a price."""
    from backend.kite.market_data import get_live_quote

    raw = get_live_quote("mock_token", instruments) or {}
    out: dict[str, float] = {}
    for inst, payload in raw.items():
        if not isinstance(payload, dict):
            continue
        ltp = payload.get("last_price")
        if isinstance(ltp, (int, float)) and float(ltp) > 0:
            out[inst] = float(ltp)
    return out


def _matches_threshold(
    operator: str, current: float, threshold: float, last: Optional[float],
) -> bool:
    """Evaluate a price/indicator threshold operator. `last` is the
    previous tick's value, required for `crosses_*` operators."""
    if operator == ">":
        return current > threshold
    if operator == "<":
        return current < threshold
    if operator == "crosses_above":
        return last is not None and last <= threshold < current
    if operator == "crosses_below":
        return last is not None and last >= threshold > current
    return False


async def _evaluate_price_trigger(
    workflow_id: str,
    cfg: dict[str, object],
    quotes: dict[str, float],
    fired_at: datetime,
) -> None:
    sym = str(cfg.get("symbol", "")).upper()
    exch = str(cfg.get("exchange", "NSE")).upper()
    operator = str(cfg.get("operator", ""))
    threshold = float(cfg.get("value", 0.0))  # type: ignore[arg-type]
    instrument = f"{exch}:{sym}"
    current = quotes.get(instrument)
    if current is None:
        return  # no quote available this tick

    last_raw = cfg.get(_LAST_PRICE_KEY)
    last = float(last_raw) if isinstance(last_raw, (int, float)) else None

    matched = _matches_threshold(operator, current, threshold, last)

    # Persist last_price so next tick's crosses_* logic works.
    await asyncio.to_thread(
        _persist_last_value, workflow_id, _LAST_PRICE_KEY, current,
    )

    if matched:
        await _fire_watch_run(workflow_id, "price_alert", fired_at)


async def _evaluate_indicator_trigger(
    workflow_id: str,
    cfg: dict[str, object],
    fired_at: datetime,
) -> None:
    """Compute the indicator inline and apply the same threshold logic
    as price triggers. This is more expensive than price evaluation
    (yfinance + indicator math) so we do it per-workflow rather than
    batching — N is expected to be small in v1."""
    sym = str(cfg.get("symbol", "")).upper()
    indicator = str(cfg.get("indicator", "")).lower()
    period = int(cfg.get("period", 14))  # type: ignore[call-overload]
    operator = str(cfg.get("operator", ""))
    threshold = float(cfg.get("value", 0.0))  # type: ignore[arg-type]

    try:
        value = await asyncio.to_thread(
            _compute_indicator_sync, sym, indicator, period,
        )
    except Exception:
        # Indicator data temporarily unavailable — try again next tick.
        return
    if value is None:
        return

    last_raw = cfg.get(_LAST_VALUE_KEY)
    last = float(last_raw) if isinstance(last_raw, (int, float)) else None
    matched = _matches_threshold(operator, value, threshold, last)

    await asyncio.to_thread(
        _persist_last_value, workflow_id, _LAST_VALUE_KEY, value,
    )

    if matched:
        await _fire_watch_run(workflow_id, "indicator_alert", fired_at)


def _compute_indicator_sync(
    symbol: str, indicator: str, period: int,
) -> Optional[float]:
    """Sync version of the fetch.indicator computation, suitable for
    the watcher (which runs DB / network in worker threads). Returns
    the latest value or None on insufficient data."""
    import pandas as pd  # type: ignore[import-untyped]
    import pandas_ta_classic as ta  # type: ignore[import-untyped]

    from backend.kite.market_data import get_historical_ohlcv

    bars = get_historical_ohlcv(symbol, period="6mo", interval="1d") or []
    if len(bars) < period + 5:
        return None
    df = pd.DataFrame(bars)
    if "close" not in df.columns:
        return None

    if indicator == "rsi":
        s = ta.rsi(df["close"], length=period)
    elif indicator == "sma":
        s = ta.sma(df["close"], length=period)
    elif indicator == "ema":
        s = ta.ema(df["close"], length=period)
    elif indicator == "macd":
        macd_df = ta.macd(df["close"], fast=12, slow=max(period, 13), signal=9)
        if macd_df is None or macd_df.empty:
            return None
        col = next((c for c in macd_df.columns if c.startswith("MACDh_")), None)
        if col is None:
            return None
        s = macd_df[col]
    else:
        return None

    if s is None or s.dropna().empty:
        return None
    return float(s.dropna().iloc[-1])


def _persist_last_value(
    workflow_id: str, key: str, value: float,
) -> None:
    """Update the workflow's step-0 config with the latest observed
    value so the next tick can detect a crossing. Uses a fresh
    SessionLocal because we're in a worker thread."""
    db = SessionLocal()
    try:
        step = (
            db.query(WorkflowStep)
            .filter(
                WorkflowStep.workflow_id == workflow_id,
                WorkflowStep.step_index == 0,
            )
            .first()
        )
        if step is None:
            return
        # JSON column update — copy and reassign so SQLA detects the
        # change (in-place mutation of a JSON dict isn't auto-tracked).
        cfg = dict(step.config or {})
        cfg[key] = float(value)
        step.config = cfg  # type: ignore[assignment]
        db.commit()
    finally:
        db.close()


async def _fire_watch_run(
    workflow_id: str, triggered_by: str, fired_at: datetime,
) -> None:
    """Create the workflow_run row and hand to the engine. Mirrors
    `_fire_one` but with the watch-specific `triggered_by` value."""

    def _create() -> Optional[str]:
        db = SessionLocal()
        try:
            wf = db.query(Workflow).filter(Workflow.id == workflow_id).first()
            if wf is None or wf.status != WorkflowStatus.active:
                return None
            run = WorkflowRun(
                workflow_id=wf.id,
                workflow_version=int(wf.version),
                triggered_by=triggered_by,
                status=RunStatus.running,
                context={},
            )
            db.add(run)
            wf.last_run_at = fired_at  # type: ignore[assignment]
            db.commit()
            db.refresh(run)
            return str(run.id)
        finally:
            db.close()

    run_id = await asyncio.to_thread(_create)
    if run_id is None:
        return

    from backend.workflows.engine import WorkflowEngine

    engine = WorkflowEngine()
    asyncio.create_task(engine.execute_run(run_id))
