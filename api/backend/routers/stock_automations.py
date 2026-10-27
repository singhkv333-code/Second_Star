"""Stock automation overlays endpoint — back the Phase 3 stock detail
chart's killer feature (#52).

For a given symbol, returns:
  - automations:    user's active workflows that reference this symbol
                    (one entry per workflow, with the matching steps)
  - triggers:       price/stoploss/limit-order levels extracted from
                    those workflows (drawn as horizontal dashed lines
                    on the chart)
  - past_fires:     workflow_runs of those workflows that already
                    completed (drawn as vertical markers on the time
                    axis with status colors)
  - scheduled:      next upcoming fire times for any of those workflows
                    that have a trigger.schedule first step (drawn as
                    vertical lines on the future time axis)

Uses direct config extraction — no ref resolution. Step types we know
how to read: trigger.price, action.set_stoploss, action.place_order
(limit), trigger.indicator, plus any step with a top-level `symbol`
field for membership detection.

Endpoint:
  GET /api/stocks/{symbol}/automations
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from apscheduler.triggers.cron import CronTrigger  # type: ignore[import-untyped]
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from pytz import timezone as pytz_timezone  # type: ignore[import-untyped]
from sqlalchemy import desc
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.models import (
    RunStatus,
    Workflow,
    WorkflowRun,
    WorkflowStatus,
    WorkflowStep,
)
from backend.routers._deps import require_user
from backend.workflows.scheduler import _normalize_cron_dow


router = APIRouter(prefix="/api/stocks", tags=["Stocks"])
logger = logging.getLogger(__name__)


# ── Response models ──────────────────────────────────────────────────


class TriggerLevel(BaseModel):
    workflow_id: str
    workflow_name: str
    kind: str          # "price" | "stoploss" | "limit_buy" | "limit_sell" | "indicator"
    level: float
    operator: str | None
    label: str         # e.g. "Buy trigger @ ₹2,400"


class PastFire(BaseModel):
    workflow_id: str
    workflow_name: str
    run_id: str
    started_at: datetime
    status: str        # "succeeded" | "failed" | "cancelled" | "running" | ...
    triggered_by: str | None


class ScheduledFire(BaseModel):
    workflow_id: str
    workflow_name: str
    fire_time: datetime
    fire_time_local: str


class AutomationSummary(BaseModel):
    workflow_id: str
    workflow_name: str
    status: str
    matched_steps: int


class AutomationsResponse(BaseModel):
    symbol: str
    automations: list[AutomationSummary]
    triggers: list[TriggerLevel]
    past_fires: list[PastFire]
    scheduled: list[ScheduledFire]


# ── Helpers ──────────────────────────────────────────────────────────


_SCAN_FIELDS = ("symbol", "symbol_filter")


def _step_references_symbol(step_cfg: dict[str, Any], symbol: str) -> bool:
    """True if the step's config references `symbol` directly (top-level
    `symbol` / `symbol_filter`) or via a known nested path
    (nested `filter.symbol`)."""
    if not isinstance(step_cfg, dict):
        return False
    upper = symbol.upper()
    for f in _SCAN_FIELDS:
        v = step_cfg.get(f)
        if isinstance(v, str) and v.upper() == upper:
            return True
    nested = step_cfg.get("filter")
    if isinstance(nested, dict):
        v = nested.get("symbol")
        if isinstance(v, str) and v.upper() == upper:
            return True
    return False


def _format_money(v: float) -> str:
    """₹2,400.50 — Indian-style thousands? keeping it simple here so the
    label is read-friendly even at non-INR magnitudes."""
    return f"₹{v:,.2f}"


def _trigger_levels_for_step(
    wf: Workflow, step: WorkflowStep,
) -> list[TriggerLevel]:
    """Extract one or more horizontal-line entries from a step's config.
    Returns [] for steps that don't have a price level (e.g. fetch.quote,
    condition.position)."""
    cfg = dict(step.config or {})
    out: list[TriggerLevel] = []
    if step.step_type == "trigger.price":
        op = str(cfg.get("operator", ""))
        level = float(cfg.get("value", 0) or 0)
        kind_label = (
            "Buy trigger" if op in (">", "crosses_above") else "Sell trigger"
        )
        out.append(TriggerLevel(
            workflow_id=str(wf.id), workflow_name=str(wf.name),
            kind="price", level=level, operator=op,
            label=f"{kind_label} {_format_money(level)}",
        ))
    elif step.step_type == "trigger.indicator":
        op = str(cfg.get("operator", ""))
        level = float(cfg.get("value", 0) or 0)
        ind = str(cfg.get("indicator", "")).upper()
        out.append(TriggerLevel(
            workflow_id=str(wf.id), workflow_name=str(wf.name),
            kind="indicator", level=level, operator=op,
            label=f"{ind}({cfg.get('period')}) {op} {level}",
        ))
    elif step.step_type == "action.set_stoploss":
        level = float(cfg.get("trigger_price", 0) or 0)
        if level > 0:
            out.append(TriggerLevel(
                workflow_id=str(wf.id), workflow_name=str(wf.name),
                kind="stoploss", level=level, operator=None,
                label=f"Stop-loss {_format_money(level)}",
            ))
    elif step.step_type == "action.place_order":
        if cfg.get("order_type") == "limit":
            level = float(cfg.get("limit_price", 0) or 0)
            if level > 0:
                side = str(cfg.get("side", "buy"))
                out.append(TriggerLevel(
                    workflow_id=str(wf.id), workflow_name=str(wf.name),
                    kind=f"limit_{side}", level=level, operator=None,
                    label=f"Limit {side.upper()} {_format_money(level)}",
                ))
    return out


# ── Endpoint ─────────────────────────────────────────────────────────


@router.get(
    "/{symbol}/automations",
    response_model=AutomationsResponse,
    summary="Active workflows referencing this symbol — for chart overlays",
)
def get_stock_automations(
    symbol: str,
    user_id: int = Depends(require_user),
    db: Session = Depends(get_db),
) -> AutomationsResponse:
    sym = symbol.upper().strip()

    # Pull all active+paused workflows for this user with their steps
    # eagerly. We filter Python-side because the symbol can live at
    # different JSON paths and SQL on JSON across SQLite/Postgres is
    # awkward. The active-workflow count per user is small (<100) so
    # this is fine.
    wfs = (
        db.query(Workflow)
        .filter(
            Workflow.user_id == user_id,
            Workflow.status.in_(
                [WorkflowStatus.active, WorkflowStatus.paused],
            ),
        )
        .all()
    )

    automations: list[AutomationSummary] = []
    triggers: list[TriggerLevel] = []
    matched_workflow_ids: set[str] = set()
    has_schedule_first_step: dict[str, WorkflowStep] = {}

    for wf in wfs:
        matched = [s for s in wf.steps if _step_references_symbol(s.config or {}, sym)]
        if not matched:
            continue
        matched_workflow_ids.add(str(wf.id))
        automations.append(AutomationSummary(
            workflow_id=str(wf.id),
            workflow_name=str(wf.name),
            status=str(wf.status.value if hasattr(wf.status, "value") else wf.status),
            matched_steps=len(matched),
        ))
        for s in matched:
            triggers.extend(_trigger_levels_for_step(wf, s))
        # Note step 0 if it's a schedule trigger so we can compute
        # upcoming fires below.
        first = next((s for s in wf.steps if s.step_index == 0), None)
        if first is not None and first.step_type == "trigger.schedule":
            has_schedule_first_step[str(wf.id)] = first

    # Past fires for any matched workflow.
    past_fires: list[PastFire] = []
    if matched_workflow_ids:
        runs = (
            db.query(WorkflowRun)
            .filter(WorkflowRun.workflow_id.in_(matched_workflow_ids))
            .order_by(desc(WorkflowRun.started_at))
            .limit(40)
            .all()
        )
        # Map workflow_id → name for label hydration without a join.
        names = {str(wf.id): str(wf.name) for wf in wfs}
        for r in runs:
            past_fires.append(PastFire(
                workflow_id=str(r.workflow_id),
                workflow_name=names.get(str(r.workflow_id), ""),
                run_id=str(r.id),
                started_at=r.started_at,
                status=str(
                    r.status.value if hasattr(r.status, "value") else r.status
                ),
                triggered_by=(
                    str(r.triggered_by) if r.triggered_by else None
                ),
            ))

    # Scheduled (next ~5 fires per workflow with trigger.schedule).
    scheduled: list[ScheduledFire] = []
    now = datetime.now(timezone.utc)
    horizon = now.replace(year=now.year + 1)  # 1Y forward, plenty for charts
    for wf_id, step in has_schedule_first_step.items():
        cfg = dict(step.config or {})
        cron = str(cfg.get("cron", ""))
        tz_str = str(cfg.get("timezone", "UTC"))
        if not cron:
            continue
        try:
            tz = pytz_timezone(tz_str)
            # Day-of-week digits in a stored cron are POSIX-convention;
            # from_crontab needs the translated day-name form (scheduler.py).
            trig = CronTrigger.from_crontab(_normalize_cron_dow(cron), timezone=tz)
        except Exception:
            continue
        # Lookup the workflow name once.
        wf_name = next((str(w.name) for w in wfs if str(w.id) == wf_id), "")
        next_fire = trig.get_next_fire_time(None, now.astimezone(tz))
        i = 0
        while next_fire is not None and next_fire <= horizon and i < 5:
            tz_label = tz_str.split("/")[-1].upper().replace("KOLKATA", "IST")
            local_str = (
                next_fire.strftime("%I:%M %p").lstrip("0") + " " + tz_label
            )
            scheduled.append(ScheduledFire(
                workflow_id=wf_id,
                workflow_name=wf_name,
                fire_time=next_fire.astimezone(timezone.utc),
                fire_time_local=local_str,
            ))
            next_fire = trig.get_next_fire_time(next_fire, next_fire)
            i += 1

    scheduled.sort(key=lambda x: x.fire_time)
    past_fires.sort(key=lambda x: x.started_at, reverse=True)
    triggers.sort(key=lambda t: t.level)

    return AutomationsResponse(
        symbol=sym,
        automations=automations,
        triggers=triggers,
        past_fires=past_fires,
        scheduled=scheduled,
    )


# Suppress unused-import warning for `RunStatus` reserved for future
# filter on past_fires (e.g. only succeeded).
_ = RunStatus
