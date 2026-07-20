"""Agent System (Workflows v1) router.

Owns:
  - GET    /api/step-types                 (catalog; auth-required)
  - POST   /api/workflows                  (create)
  - GET    /api/workflows                  (list, omit steps/context)
  - GET    /api/workflows/{id}             (full shape)
  - PATCH  /api/workflows/{id}             (bumps version on step edit)
  - POST   /api/workflows/{id}/activate
  - POST   /api/workflows/{id}/pause
  - POST   /api/workflows/{id}/archive
  - POST   /api/workflows/{id}/run         (enqueue manual run)

Every endpoint is JWT-bearer authenticated and user-scoped. Cross-user
access returns 404 with the canonical envelope (NOT 403 — never leak
existence per API_CONTRACT.md §1).

Schema validation policy (ARCHITECTURE.md §7 invariant 7):
  - on POST: validate every step config against the registry's
    Pydantic model; reject unknown step_type; require step_index=0
    to be a `trigger.*`.
  - on PATCH: same validation; bump `workflow.version` if `steps`
    changed; refuse if status='active' (caller must pause first).
  - on activate: re-validate every step config.

The actual run scheduling (cron triggers, watcher arming) happens in
`backend/workflows/scheduler.py` Day 3+. For Day 2 the activate/pause
endpoints flip status only; manual `POST .../run` enqueues to the
in-process worker.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional, cast

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.posthog_client import get_posthog
from backend.models import (
    ForwardIdea,
    PaperAccount,
    PaperFill,
    PaperIdeaNavSnapshot,
    PaperNavSnapshot,
    RunStatus,
    Workflow,
    WorkflowApproval,
    WorkflowRun,
    WorkflowRunStep,
    WorkflowStatus,
    WorkflowStep,
    WorkflowWebhookToken,
)
from backend.routers._deps import require_user
from backend.routers._errors import (
    not_found,
    state_conflict,
    validation_error,
)
from backend.schemas import (
    ProposeWorkflowRequest,
    ProposeWorkflowResponse,
    RunCreatedResponse,
    StepOut,
    StepTypeCatalogResponse,
    WorkflowCreate,
    WorkflowListResponse,
    WorkflowOut,
    WorkflowPatch,
    WorkflowSummary,
)
from backend.workflows.compat import (
    AmbientState,
    Diagnostic,
    lint_workflow,
)
from backend.workflows.engine import WorkflowEngine
from backend.workflows.registry import STEP_REGISTRY, get_catalog
from backend.workflows.scheduler import (
    InvalidCronError,
    upsert_workflow_schedule,
)

router = APIRouter(prefix="/api", tags=["Agents"])

logger = logging.getLogger(__name__)


# ── Helpers ──────────────────────────────────────────────────────────


def _validate_steps(
    steps: list[dict[str, object]],
) -> None:
    """Validate every step in a workflow body.

    Per API_CONTRACT.md §5.1:
      - reject unknown step_type → 422 validation_error with
        details.step_index and details.field='step_type'
      - reject step_index=0 that isn't a trigger.* → 400
        validation_error
      - reject any step whose config fails its Pydantic model → 422
        with details.step_index

    Steps come in as a list of dicts (already coerced from StepInput).
    Index in the list IS the step_index (per API_CONTRACT.md §5.1).
    """
    if not steps:
        # An empty workflow is allowed — chat-bot proposes a draft, user
        # may save with zero steps and add later.
        return

    for idx, step in enumerate(steps):
        step_type = step.get("step_type")
        if not isinstance(step_type, str):
            raise validation_error(
                f"step {idx} missing step_type",
                details={"step_index": idx, "field": "step_type"},
            )
        defn = STEP_REGISTRY.get(step_type)
        if defn is None:
            raise validation_error(
                f"unknown step_type {step_type!r}",
                details={
                    "step_index": idx,
                    "field": "step_type",
                    "reason": "unknown_step_type",
                    "known": sorted(STEP_REGISTRY.keys()),
                },
            )
        # step_index=0 must be a trigger.
        if idx == 0 and not defn.trigger_only:
            # 400 (per §5.1) — wrong shape, not a schema-validation
            # error.
            raise validation_error(
                f"step 0 must be a trigger.* (got {step_type!r})",
                details={
                    "step_index": 0,
                    "field": "step_type",
                    "reason": "step_0_must_be_trigger",
                },
            )
        # Multi-trigger: trigger.* is allowed at any later index too —
        # each one starts a new branch. The only invariant is that two
        # triggers can't sit back-to-back (an empty branch is almost
        # always a model mistake; reject so the user notices).
        if idx > 0 and defn.trigger_only:
            prev = steps[idx - 1]
            prev_type = (prev.get("step_type") or "")
            prev_defn = STEP_REGISTRY.get(prev_type)
            if prev_defn is not None and prev_defn.trigger_only:
                raise validation_error(
                    "two triggers in a row creates an empty branch — "
                    "give the previous trigger at least one action / "
                    "condition / fetch step",
                    details={
                        "step_index": idx,
                        "field": "step_type",
                        "reason": "empty_branch",
                    },
                )

        cfg = step.get("config") or {}
        try:
            defn.config_model.model_validate(cfg)
        except ValidationError as e:
            first = e.errors()[0]
            raise validation_error(
                f"step {idx} config invalid: {first.get('msg', '')}",
                details={
                    "step_index": idx,
                    "field": ".".join(str(p) for p in first.get("loc", [])),
                    "reason": str(first.get("type", "")),
                },
            )

    # Final pass: share the single-source-of-truth linter so create /
    # update / activate never drift from the editor's /lint endpoint.
    # ONLY severity=="error" diagnostics block; warnings + info are
    # advisory and surface via the GET response's `diagnostics` field.
    # No ambient state at the router boundary — ambient is a per-run
    # engine concept; the editor is reasoning about the workflow shape
    # in isolation here. Pass `step_names` from each step's caller-supplied
    # label so diagnostics read as `Step N ("Buy the dip")` instead of
    # leaking the raw step_type id.
    step_names: dict[int, str] = {}
    for idx, step in enumerate(steps):
        raw_label = step.get("label")
        if isinstance(raw_label, str) and raw_label.strip():
            step_names[idx] = raw_label.strip()
    lint_diags = lint_workflow(
        steps, ambient=None, step_names=step_names or None,
    )
    lint_errors = [d for d in lint_diags if d.severity == "error"]
    if lint_errors:
        first = lint_errors[0]
        raise validation_error(
            f"step {first.step_index} {first.code}: {first.message}",
            details={
                "step_index": first.step_index,
                "field": first.field,
                "reason": first.code,
                "diagnostics": [d.model_dump() for d in lint_errors],
            },
        )


def _workflow_for_user(
    db: Session, user_id: int, workflow_id: str,
) -> Workflow:
    """Look up a workflow scoped to the user. Cross-user access → 404
    (per §1, never 403). Same shape used by every endpoint that takes
    `{id}`."""
    wf = (
        db.query(Workflow)
        .filter(Workflow.id == workflow_id, Workflow.user_id == user_id)
        .first()
    )
    if wf is None:
        raise not_found("workflow not found")
    return wf


def _replace_steps(
    db: Session, wf: Workflow, steps_in: list[dict[str, object]],
) -> None:
    """Replace the workflow's step list. Caller must have validated."""
    db.query(WorkflowStep).filter(WorkflowStep.workflow_id == wf.id).delete()
    db.flush()
    for idx, s in enumerate(steps_in):
        ws = WorkflowStep(
            workflow_id=wf.id,
            step_index=idx,
            step_type=cast(str, s["step_type"]),
            config=s.get("config") or {},
            label=cast(Optional[str], s.get("label")),
        )
        db.add(ws)
    db.flush()


def _to_workflow_out(wf: Workflow) -> WorkflowOut:
    """Build the canonical WorkflowOut shape with steps[] + diagnostics[].

    Diagnostics are the full lint result (errors + warnings + info) so
    already-saved workflows surface advisories on GET without a separate
    /lint round-trip. Never raises — lint_workflow is pure + defensive."""
    ordered_steps = sorted(wf.steps, key=lambda s: int(s.step_index))
    lint_input = [
        {
            "step_type": str(s.step_type),
            "config": dict(s.config or {}),
        }
        for s in ordered_steps
    ]
    # Build step_names from the persisted labels so already-saved workflows
    # surface friendly diagnostic text on GET (no raw step_type ids).
    step_names: dict[int, str] = {}
    for idx, s in enumerate(ordered_steps):
        if isinstance(s.label, str) and s.label.strip():
            step_names[idx] = s.label.strip()
    diagnostics = [
        d.model_dump()
        for d in lint_workflow(
            lint_input, ambient=None, step_names=step_names or None,
        )
    ]
    return WorkflowOut(
        id=str(wf.id),
        name=str(wf.name),
        description=wf.description,
        status=wf.status.value if hasattr(wf.status, "value")
        else str(wf.status),  # type: ignore[arg-type]
        version=int(wf.version),
        single_instance=bool(wf.single_instance),
        created_at=wf.created_at,
        updated_at=wf.updated_at,
        activated_at=wf.activated_at,
        last_run_at=wf.last_run_at,
        next_run_at=wf.next_run_at,
        expires_at=getattr(wf, "expires_at", None),
        steps=[
            StepOut(
                id=str(s.id),
                step_index=int(s.step_index),
                step_type=str(s.step_type),
                label=s.label,
                config=dict(s.config or {}),
                next_run_at=s.next_run_at,
            )
            for s in ordered_steps
        ],
        diagnostics=diagnostics,
    )


def _to_workflow_summary(wf: Workflow) -> WorkflowSummary:
    return WorkflowSummary(
        id=str(wf.id),
        name=str(wf.name),
        description=wf.description,
        status=wf.status.value if hasattr(wf.status, "value")
        else str(wf.status),  # type: ignore[arg-type]
        version=int(wf.version),
        single_instance=bool(wf.single_instance),
        created_at=wf.created_at,
        updated_at=wf.updated_at,
        activated_at=wf.activated_at,
        last_run_at=wf.last_run_at,
        next_run_at=wf.next_run_at,
        expires_at=getattr(wf, "expires_at", None),
    )


# ── Catalog (Day 1 carryover) ─────────────────────────────────────────


@router.get(
    "/step-types",
    response_model=StepTypeCatalogResponse,
    summary="Step-type catalog",
    description=(
        "Returns the full catalog of supported workflow step types — their "
        "JSON Schema (draft 2020-12) for config validation, output schemas, "
        "and UI metadata. See docs/API_CONTRACT.md §8.1."
    ),
)
def get_step_types(
    _user_id: int = Depends(require_user),
) -> StepTypeCatalogResponse:
    catalog = get_catalog()
    return StepTypeCatalogResponse.model_validate(catalog)


# ── /lint — editor-facing lint endpoint (shares lint_workflow) ────────


class _LintAmbientIn(BaseModel):
    """Per-call ambient state passed by the editor (mirrors
    `backend.workflows.compat.AmbientState`). Defaults are permissive-
    unknown so callers can omit it."""
    model_config = ConfigDict(extra="forbid")

    held_symbols: list[str] = Field(default_factory=list)
    has_pending_orders: bool = False


class _LintWorkflowRequest(BaseModel):
    """Body for ``POST /api/workflows/lint``.

    `steps` is the same shape the create/update path uses (list of
    `{step_type, config, label?}` dicts). `ambient` is optional —
    when present, capability checks that could be satisfied by an
    open position or a pending order in the user's book stop firing.
    The endpoint is PURE: no DB writes, no DB reads, no LLM, no
    network — it just calls `lint_workflow` and returns the result.
    """
    model_config = ConfigDict(extra="forbid")

    steps: list[dict[str, object]] = Field(default_factory=list)
    ambient: Optional[_LintAmbientIn] = None


class _LintWorkflowResponse(BaseModel):
    """Response is the lint result, 1:1 with `compat.Diagnostic`."""
    diagnostics: list[Diagnostic]


@router.post(
    "/workflows/lint",
    response_model=_LintWorkflowResponse,
    summary="Lint a workflow draft (errors + warnings + info)",
    description=(
        "Runs the single-source-of-truth `lint_workflow` over the supplied "
        "steps and returns the diagnostics the editor surfaces inline. "
        "Pure (no DB writes, no scheduling side-effects) so it can be "
        "called on every edit — the FE debounces at ~250ms. The same "
        "function is invoked at create/update/activate, where ONLY "
        "`severity=='error'` diagnostics block; warnings and info are "
        "advisory."
    ),
)
def lint_workflow_endpoint(
    body: _LintWorkflowRequest,
    _user_id: int = Depends(require_user),
) -> _LintWorkflowResponse:
    ambient = (
        AmbientState(
            held_symbols=body.ambient.held_symbols,
            has_pending_orders=body.ambient.has_pending_orders,
        )
        if body.ambient is not None
        else None
    )
    # Carry through any per-step labels the editor supplies so diagnostics
    # come back human-readable (e.g. `Step 3 ("Buy the dip")` instead of
    # `step 3 (trigger.exit_compound)`).
    step_names: dict[int, str] = {}
    for idx, step in enumerate(body.steps):
        raw_label = step.get("label")
        if isinstance(raw_label, str) and raw_label.strip():
            step_names[idx] = raw_label.strip()
    diagnostics = lint_workflow(
        body.steps, ambient=ambient, step_names=step_names or None,
    )
    return _LintWorkflowResponse(diagnostics=diagnostics)


# ── /dsl/schema + /dsl/describe — read-only ConditionBuilder helpers ──
#
# Metadata + english readback for the visual condition/tree builder that
# edits the compound DSL trees (trigger.compound / trigger.exit_compound /
# condition.compound). Read-only: full-workflow validation stays with
# POST /api/workflows/lint. Neither endpoint 500s on bad input — an invalid
# tree comes back 200 with {"english": "", "error": "..."} so the builder
# surfaces the message inline.

# Operator + position-field vocabularies (ordered; the FE renders them
# verbatim and the contract test pins the order).
_DSL_OPERATORS: list[tuple[str, str]] = [
    (">", "is above"),
    ("<", "is below"),
    (">=", "is at or above"),
    ("<=", "is at or below"),
    ("==", "equals"),
    ("crosses_above", "crosses above"),
    ("crosses_below", "crosses below"),
]
_DSL_POSITION_FIELDS: list[tuple[str, str]] = [
    ("entry_price", "Entry price"),
    ("unrealised_pct", "Unrealised P&L %"),
    ("unrealised_abs", "Unrealised P&L ₹"),
    ("bars_held", "Bars held"),
    ("peak_unrealised_pct", "Peak unrealised %"),
    ("drawdown_from_peak_pct", "Drawdown from peak %"),
]


class _DslIndicatorEntry(BaseModel):
    id: str
    label: str
    default_period: int
    multi_output: bool
    components: list[str]


class _DslLabeled(BaseModel):
    id: str
    label: str


class _DslTreeField(BaseModel):
    field: str
    mode: str  # "entry" | "exit"


class _DslSchemaResponse(BaseModel):
    indicators: list[_DslIndicatorEntry]
    operators: list[_DslLabeled]
    operand_kinds: list[str]
    price_bases: list[str]
    position_fields: list[_DslLabeled]
    logic_ops: list[str]
    timeframes: list[str]
    tree_fields: dict[str, _DslTreeField]


def _dsl_indicator_entries() -> list[_DslIndicatorEntry]:
    """Build the indicator picker list from the live indicator registry so
    a newly-registered indicator shows up without editing this endpoint.
    Aliases (bb/bollinger) collapse to their canonical spec.key."""
    from backend.services.backtest_indicators import (
        _REGISTRY,
        allowed_components,
        supported_indicators,
    )

    seen: set[str] = set()
    out: list[_DslIndicatorEntry] = []
    for key in supported_indicators():
        spec = _REGISTRY[key]
        if spec.key in seen:
            continue
        seen.add(spec.key)
        comps = list(allowed_components(spec.key))
        out.append(
            _DslIndicatorEntry(
                id=spec.key,
                label=spec.label,
                default_period=spec.default_period,
                multi_output=bool(comps),
                components=comps,
            )
        )
    return out


@router.get(
    "/workflows/dsl/schema",
    response_model=_DslSchemaResponse,
    summary="DSL builder metadata (indicators, operators, operand kinds…)",
    description=(
        "Read-only vocabularies the visual ConditionBuilder uses to render "
        "operand pickers for compound DSL trees. Indicators come from the "
        "live backtest_indicators registry; the rest are static. "
        "`tree_fields` maps each compound step_type to the config field that "
        "holds its tree and whether position leaves are allowed (mode=exit)."
    ),
)
def get_dsl_schema(
    _user_id: int = Depends(require_user),
) -> _DslSchemaResponse:
    return _DslSchemaResponse(
        indicators=_dsl_indicator_entries(),
        operators=[_DslLabeled(id=i, label=lbl) for i, lbl in _DSL_OPERATORS],
        operand_kinds=["indicator", "price", "constant", "position"],
        price_bases=["close", "open", "high", "low"],
        position_fields=[
            _DslLabeled(id=i, label=lbl) for i, lbl in _DSL_POSITION_FIELDS
        ],
        logic_ops=["and", "or"],
        timeframes=["daily", "weekly"],
        # All three compound steps store the tree under config['entry'];
        # only the exit trigger allows position-leaf operands.
        tree_fields={
            "trigger.compound": _DslTreeField(field="entry", mode="entry"),
            "trigger.exit_compound": _DslTreeField(field="entry", mode="exit"),
            "condition.compound": _DslTreeField(field="entry", mode="entry"),
        },
    )


class _DescribeDslRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tree: dict[str, object] = Field(default_factory=dict)
    mode: str = "entry"  # "entry" | "exit"


class _DescribeDslResponse(BaseModel):
    english: str
    error: Optional[str] = None


@router.post(
    "/workflows/dsl/describe",
    response_model=_DescribeDslResponse,
    summary="English readback of a DSL condition tree",
    description=(
        "Validates a single DSL tree (structural + semantic, with position "
        "leaves gated by `mode`) and returns a one-line english sentence for "
        "the builder's live readback. NEVER 500s — an invalid tree returns "
        "200 with english='' and a human error string."
    ),
)
def describe_dsl_tree(
    body: _DescribeDslRequest,
    _user_id: int = Depends(require_user),
) -> _DescribeDslResponse:
    from pydantic import TypeAdapter

    from backend.workflows.dsl.readback import tree_to_english
    from backend.workflows.dsl.schema import Tree, normalize_tree_aliases
    from backend.workflows.dsl.validators import (
        DSLValidationError,
        semantic_validate,
    )

    try:
        parsed = TypeAdapter(Tree).validate_python(
            normalize_tree_aliases(body.tree)
        )
        semantic_validate(parsed, allow_position=(body.mode == "exit"))
        english = tree_to_english(parsed)
    except (ValidationError, DSLValidationError, ValueError) as exc:
        return _DescribeDslResponse(english="", error=str(exc))
    return _DescribeDslResponse(english=english, error=None)


# ── propose_workflow as a direct REST endpoint (Day 6 #38) ────────────


@router.post(
    "/propose-workflow",
    response_model=ProposeWorkflowResponse,
    summary="Translate a NL strategy into a workflow draft",
    description=(
        "Surfaces the chatbot's `propose_workflow` tool as a direct REST "
        "endpoint so frontends can demo the chat→draft flow without porting "
        "the full chatbot. Calls the LLM with validation + one retry. On "
        "second failure returns 422 with a structured message naming the "
        "missing or invalid fields — does NOT fabricate a workflow when "
        "the LLM couldn't produce one. Successful drafts are NOT persisted; "
        "the user reviews and clicks Save & activate to commit."
    ),
)
async def propose_workflow_endpoint(
    body: ProposeWorkflowRequest,
    _user_id: int = Depends(require_user),
) -> ProposeWorkflowResponse:
    from backend.workflows.propose import (
        ProposalValidationError,
        propose_workflow_async,
    )
    try:
        draft = await propose_workflow_async(body.user_intent)
    except ProposalValidationError as e:
        # Surface a user-readable message, not a stack trace. The error
        # text from propose.py already names the offending field
        # ("step 2 (action.place_order) config invalid: quantity: ...").
        raise validation_error(
            f"I couldn't quite turn that into a workflow — {e}. "
            "Try rephrasing with the specific values (price thresholds, "
            "quantities, order types) you want.",
            details={"field": "user_intent", "reason": str(e)},
        )
    return ProposeWorkflowResponse.model_validate(draft.model_dump())


# ── Workflow CRUD ─────────────────────────────────────────────────────


@router.post(
    "/workflows",
    response_model=WorkflowOut,
    status_code=201,
    summary="Create a workflow",
)
def create_workflow(
    body: WorkflowCreate,
    user_id: int = Depends(require_user),
    db: Session = Depends(get_db),
) -> WorkflowOut:
    steps_in = [s.model_dump() for s in body.steps]
    _validate_steps(steps_in)

    wf = Workflow(
        user_id=user_id,
        name=body.name,
        description=body.description,
        single_instance=body.single_instance,
        status=WorkflowStatus.draft,
        expires_at=body.expires_at,
    )
    db.add(wf)
    db.flush()
    _replace_steps(db, wf, steps_in)
    db.commit()
    db.refresh(wf)

    _ph = get_posthog()
    if _ph:
        trigger_type = steps_in[0].get("step_type") if steps_in else None
        _ph.capture("workflow_created", distinct_id=str(user_id), properties={
            "step_count": len(steps_in),
            "trigger_type": trigger_type,
        })

    return _to_workflow_out(wf)


@router.get(
    "/workflows",
    response_model=WorkflowListResponse,
    summary="List workflows",
)
def list_workflows(
    user_id: int = Depends(require_user),
    db: Session = Depends(get_db),
    status: Optional[str] = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    cursor: Optional[str] = Query(default=None),
) -> WorkflowListResponse:
    """Cursor-based pagination. Cursor is the previous page's last
    workflow_id; rows after it (ordered by created_at desc) are
    returned."""
    q = db.query(Workflow).filter(Workflow.user_id == user_id)
    if status:
        statuses = [s.strip() for s in status.split(",") if s.strip()]
        q = q.filter(Workflow.status.in_(statuses))
    q = q.order_by(Workflow.created_at.desc(), Workflow.id.desc())

    if cursor:
        cur_wf = db.query(Workflow).filter_by(id=cursor).first()
        if cur_wf is not None:
            q = q.filter(Workflow.created_at < cur_wf.created_at)

    rows = q.limit(limit + 1).all()
    has_more = len(rows) > limit
    items = [_to_workflow_summary(w) for w in rows[:limit]]
    next_cursor = str(rows[limit - 1].id) if has_more else None
    return WorkflowListResponse(items=items, next_cursor=next_cursor)


# ── Agents-tab aggregate summary + per-agent performance ──────────────
#
# These power the Agents tab's top cards (counts, a 6-month trade
# scorecard, a GitHub-style daily-P&L heatmap, and per-agent return
# chips) and each agent card's sparkline. Everything is computed
# on-read from existing paper-trading + workflow tables for the CURRENT
# user only — no new columns, no seeded/faked numbers. When a user has
# no paper account, no fills, or no NAV snapshots, the corresponding
# field comes back as zeros / empty arrays with a clear flag.

_SUMMARY_WINDOW_DAYS = 182  # ~6 months


class _TradeScorecard(BaseModel):
    """6-month closed-trade tally derived from PaperFill.realized_pnl.

    A "win" is a closing fill (one that booked realized_pnl) with
    realized_pnl > 0; a "loss" is realized_pnl < 0. Opening fills carry a
    NULL realized_pnl and are ignored. `total` counts only fills that
    booked a P&L (i.e. wins + losses), so win_rate_pct is over decided
    trades. All zeros when the user has no paper account / no fills.
    """
    total: int = 0
    wins: int = 0
    losses: int = 0
    win_rate_pct: Optional[float] = None


class _DailyPnlPoint(BaseModel):
    date: str  # YYYY-MM-DD
    pnl: float


class _NavPoint(BaseModel):
    date: str  # YYYY-MM-DD
    nav: float


class _StrategyReturn(BaseModel):
    workflow_id: str
    name: str
    # cum_return_pct from the linked ForwardIdea's scorecard_cache when
    # available; null when the agent has no forward-test track record yet
    # (never seeded).
    return_pct: Optional[float] = None
    # The same sparkline/headline data GET /workflows/{id}/performance serves,
    # batched here for EVERY workflow so the Agents tab can render every card
    # from this one response instead of firing one /performance call per card
    # (plus a /workflow + backtest fallback for idle agents) on first paint.
    series: list[_NavPoint] = Field(default_factory=list)
    run_count: int = 0
    success_rate: Optional[float] = None
    last_run_at: Optional[datetime] = None
    has_data: bool = False


class _WorkflowsSummaryResponse(BaseModel):
    active_count: int = 0
    paused_count: int = 0
    draft_count: int = 0
    trades_6mo: _TradeScorecard = Field(default_factory=_TradeScorecard)
    daily_pnl: list[_DailyPnlPoint] = Field(default_factory=list)
    strategy_returns: list[_StrategyReturn] = Field(default_factory=list)
    total_pnl: float = 0.0
    has_data: bool = False


def _live_idea_return_pct(db: Session, idea) -> Optional[float]:
    """An agent's LIVE return %, marked on read — the SAME number the agent
    Positions view headlines, so the card / strategy-list % can never disagree
    with the positions panel or the portfolio. Computed from
    ``compute_idea_positions`` (live ``get_mark_price``): the open book's
    unrealized %. Falls back to the (stale) cached cumulative return only for a
    fully-closed idea with no open positions to live-mark, and to ``None`` when
    neither exists — never a fabricated number."""
    try:
        from backend.paper.idea_valuation import compute_idea_positions

        roll = compute_idea_positions(db, idea)
        if roll["positions"]:
            pct = roll["unrealized_pnl_pct"]
            return float(pct) if pct is not None else None
    except Exception:  # noqa: BLE001 — a pricing blip must not break the card
        logger.debug("live idea return failed for %s", idea.id, exc_info=True)
    if idea.scorecard_cache:
        raw = idea.scorecard_cache.get("cum_return_pct")
        if isinstance(raw, (int, float)):
            return float(raw)
    return None


@router.get(
    "/workflows/summary",
    response_model=_WorkflowsSummaryResponse,
    summary="Agents-tab aggregate stats for the current user",
    description=(
        "Aggregates the current user's agents (workflows) and paper-trading "
        "history for the Agents-tab top cards: status counts, a 6-month "
        "closed-trade scorecard (from PaperFill.realized_pnl), a daily-P&L "
        "series for a GitHub-style heatmap (from PaperNavSnapshot NAV "
        "day-over-day deltas), and per-active-agent return chips (from the "
        "linked ForwardIdea scorecard). Everything is computed on-read for "
        "this user only; missing underlying data yields zeros / empty arrays, "
        "never fabricated numbers."
    ),
)
def workflows_summary(
    user_id: int = Depends(require_user),
    db: Session = Depends(get_db),
) -> _WorkflowsSummaryResponse:
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=_SUMMARY_WINDOW_DAYS)

    # ── Status counts ────────────────────────────────────────────────
    wfs = (
        db.query(Workflow)
        .filter(Workflow.user_id == user_id)
        .all()
    )
    active_count = sum(1 for w in wfs if w.status == WorkflowStatus.active)
    paused_count = sum(1 for w in wfs if w.status == WorkflowStatus.paused)
    draft_count = sum(1 for w in wfs if w.status == WorkflowStatus.draft)

    # ── Paper account (single book per user) ─────────────────────────
    account = (
        db.query(PaperAccount)
        .filter(PaperAccount.user_id == user_id)
        .first()
    )

    # ── 6-month closed-trade scorecard ───────────────────────────────
    trades = _TradeScorecard()
    if account is not None:
        closing = (
            db.query(PaperFill.realized_pnl)
            .filter(
                PaperFill.account_id == account.id,
                PaperFill.filled_at >= since,
                PaperFill.realized_pnl.isnot(None),
            )
            .all()
        )
        wins = 0
        losses = 0
        for (rpnl,) in closing:
            val = float(rpnl)
            if val > 0:
                wins += 1
            elif val < 0:
                losses += 1
        total = wins + losses
        trades = _TradeScorecard(
            total=total,
            wins=wins,
            losses=losses,
            win_rate_pct=(
                round(wins / total * 100.0, 2) if total > 0 else None
            ),
        )

    # ── Daily P&L from NAV day-over-day deltas ───────────────────────
    daily_pnl: list[_DailyPnlPoint] = []
    total_pnl = 0.0
    if account is not None:
        snaps = (
            db.query(PaperNavSnapshot)
            .filter(
                PaperNavSnapshot.account_id == account.id,
                PaperNavSnapshot.as_of_date >= since.date(),
            )
            .order_by(PaperNavSnapshot.as_of_date.asc())
            .all()
        )
        prev_nav: Optional[float] = None
        for snap in snaps:
            nav = float(snap.nav)
            if prev_nav is not None:
                pnl = round(nav - prev_nav, 2)
                daily_pnl.append(
                    _DailyPnlPoint(
                        date=snap.as_of_date.isoformat(),
                        pnl=pnl,
                    )
                )
                total_pnl += pnl
            prev_nav = nav
        total_pnl = round(total_pnl, 2)

    # ── Per-agent sparkline + returns + run stats, batched for EVERY
    # workflow ── The Agents tab used to fire one GET
    # /workflows/{id}/performance per card (plus a GET /workflow +
    # backtest-draft fallback for any card with no live NAV yet) — up to 3N
    # backend round trips on first paint. Computing the same fields here for
    # every workflow in ONE request lets the FE render every card off this
    # one response instead.
    strategy_returns: list[_StrategyReturn] = []
    if wfs:
        wf_ids = [str(w.id) for w in wfs]

        run_rows = (
            db.query(WorkflowRun.workflow_id, WorkflowRun.status)
            .filter(WorkflowRun.workflow_id.in_([w.id for w in wfs]))
            .all()
        )
        runs_by_wf: dict[str, list] = {}
        for run_wf_id, status in run_rows:
            runs_by_wf.setdefault(str(run_wf_id), []).append(status)

        ideas = (
            db.query(ForwardIdea)
            .filter(
                ForwardIdea.user_id == user_id,
                ForwardIdea.origin_kind == "workflow",
                ForwardIdea.workflow_id.in_(wf_ids),
            )
            .all()
        )
        idea_by_wf: dict[str, ForwardIdea] = {}
        for idea in ideas:
            # Most-recent idea wins if a workflow ever forked one.
            existing = idea_by_wf.get(str(idea.workflow_id))
            if existing is None or idea.created_at > existing.created_at:
                idea_by_wf[str(idea.workflow_id)] = idea

        idea_ids = [idea.id for idea in idea_by_wf.values()]
        snaps_by_idea: dict[str, list] = {}
        if idea_ids:
            snap_rows = (
                db.query(PaperIdeaNavSnapshot)
                .filter(PaperIdeaNavSnapshot.idea_id.in_(idea_ids))
                .order_by(PaperIdeaNavSnapshot.as_of_date.asc())
                .all()
            )
            for snap in snap_rows:
                snaps_by_idea.setdefault(str(snap.idea_id), []).append(snap)

        today = now.date().isoformat()
        for w in wfs:
            wf_id = str(w.id)
            statuses = runs_by_wf.get(wf_id, [])
            run_count = len(statuses)
            succeeded = sum(1 for s in statuses if s == RunStatus.succeeded)
            success_rate = (
                round(succeeded / run_count * 100.0, 2) if run_count > 0 else None
            )

            idea = idea_by_wf.get(wf_id)
            series: list[_NavPoint] = []
            ret: Optional[float] = None
            if idea is not None:
                # LIVE return (marked on read) so this matches the agent's
                # Positions panel and the portfolio, not a stale EOD scorecard.
                ret = _live_idea_return_pct(db, idea)
                for snap in snaps_by_idea.get(str(idea.id), []):
                    series.append(
                        _NavPoint(date=snap.as_of_date.isoformat(), nav=float(snap.idea_nav))
                    )
                # Pin a live "today" point onto the EOD series so the
                # sparkline ends at the current mark (mirrors
                # GET /workflows/{id}/performance).
                try:
                    from backend.paper.idea_valuation import compute_idea_nav

                    live_nav = float(compute_idea_nav(db, idea)["idea_nav"])
                    if series and series[-1].date == today:
                        series[-1] = _NavPoint(date=today, nav=live_nav)
                    else:
                        series.append(_NavPoint(date=today, nav=live_nav))
                except Exception:  # noqa: BLE001 — sparkline tail is best-effort
                    logger.debug(
                        "live idea nav tail failed for %s", idea.id, exc_info=True
                    )

            strategy_returns.append(
                _StrategyReturn(
                    workflow_id=wf_id,
                    name=str(w.name),
                    return_pct=ret,
                    series=series,
                    run_count=run_count,
                    success_rate=success_rate,
                    last_run_at=w.last_run_at,
                    has_data=bool(series) or run_count > 0,
                )
            )

    has_data = bool(wfs) or bool(daily_pnl) or trades.total > 0

    return _WorkflowsSummaryResponse(
        active_count=active_count,
        paused_count=paused_count,
        draft_count=draft_count,
        trades_6mo=trades,
        daily_pnl=daily_pnl,
        strategy_returns=strategy_returns,
        total_pnl=total_pnl,
        has_data=has_data,
    )


@router.get(
    "/workflows/{workflow_id}",
    response_model=WorkflowOut,
    summary="Get a workflow",
)
def get_workflow(
    workflow_id: str,
    user_id: int = Depends(require_user),
    db: Session = Depends(get_db),
) -> WorkflowOut:
    wf = _workflow_for_user(db, user_id, workflow_id)
    return _to_workflow_out(wf)


@router.patch(
    "/workflows/{workflow_id}",
    response_model=WorkflowOut,
    summary="Update a workflow",
)
def patch_workflow(
    workflow_id: str,
    body: WorkflowPatch,
    user_id: int = Depends(require_user),
    db: Session = Depends(get_db),
) -> WorkflowOut:
    wf = _workflow_for_user(db, user_id, workflow_id)

    # API_CONTRACT.md §5.4: editing is forbidden while active. Caller
    # must pause first.
    if wf.status == WorkflowStatus.active:
        raise state_conflict(
            "cannot edit an active workflow; pause first",
            details={"current_status": "active"},
        )
    if wf.status == WorkflowStatus.archived:
        raise state_conflict(
            "cannot edit an archived workflow",
            details={"current_status": "archived"},
        )

    if body.name is not None:
        wf.name = body.name
    if body.description is not None:
        wf.description = body.description
    if body.single_instance is not None:
        wf.single_instance = body.single_instance
    if body.expires_at is not None:
        wf.expires_at = body.expires_at

    if body.steps is not None:
        steps_in = [s.model_dump() for s in body.steps]
        _validate_steps(steps_in)
        _replace_steps(db, wf, steps_in)
        # Bump version per §5.4 — runs reference the version at run
        # creation time so old run rows still join their original
        # step list.
        wf.version = int(wf.version) + 1

    db.commit()
    db.refresh(wf)
    return _to_workflow_out(wf)


# ── State transitions ────────────────────────────────────────────────


def _register_armed_idea(db: Session, user_id: int, wf) -> None:
    """Register an ARMED forward-test idea for a trading agent on activation.

    Creates the ForwardIdea (origin='workflow') so the agent shows in
    Paper -> Ideas right away, WITHOUT placing any order or position. When the
    agent later fires, action.place_order resolves to this SAME idea (dedup on
    workflow_id) and the fill sets its inception_date + creates the position.
    Only for paper-mode users and only for agents that actually place orders.
    """
    has_order_action = any(
        str(s.step_type).startswith(("action.place_order", "action.open_basket",
                                     "action.allocate"))
        for s in wf.steps
    )
    if not has_order_action:
        return
    from backend.paper.routing import should_use_paper
    from backend.paper.accounts import get_or_create_account
    from backend.paper.ideas import resolve_idea

    if not should_use_paper(db, user_id):
        return
    account = get_or_create_account(db, user_id)
    resolve_idea(
        db, account.id,
        user_id=user_id,
        origin_kind="workflow",
        workflow_id=str(wf.id),
        label=wf.name,
    )
    db.commit()


def _schedule_window_minutes(wf: Workflow) -> Optional[int]:
    """Shortest bounded window (``duration_minutes``) across the workflow's
    ``trigger.schedule`` steps, or None if none of them bounds its window.

    Backs "every 5 min FOR THE NEXT HOUR" agents: the trigger carries the
    duration; activation turns the SHORTEST such window into a concrete
    ``expires_at`` so the scheduler auto-pauses the agent once it elapses.
    """
    windows: list[int] = []
    for s in wf.steps:
        if s.step_type == "trigger.schedule":
            d = (s.config or {}).get("duration_minutes")
            if isinstance(d, int) and d > 0:
                windows.append(d)
    return min(windows) if windows else None


@router.post(
    "/workflows/{workflow_id}/activate",
    response_model=WorkflowOut,
    summary="Activate a workflow",
)
def activate_workflow(
    workflow_id: str,
    user_id: int = Depends(require_user),
    db: Session = Depends(get_db),
) -> WorkflowOut:
    wf = _workflow_for_user(db, user_id, workflow_id)
    if wf.status == WorkflowStatus.active:
        raise state_conflict(
            "workflow already active",
            details={"current_status": "active"},
        )
    if wf.status == WorkflowStatus.archived:
        raise state_conflict(
            "cannot activate an archived workflow",
            details={"current_status": "archived"},
        )

    # Re-validate every step on activate (defense in depth, §7.7).
    steps_in = [
        {
            "step_type": s.step_type,
            "config": s.config or {},
            "label": s.label,
        }
        for s in sorted(wf.steps, key=lambda s: int(s.step_index))
    ]
    _validate_steps(steps_in)

    wf.status = WorkflowStatus.active
    wf.activated_at = datetime.now(timezone.utc)
    # Bounded recurring schedules ("every 5 min for the next hour"): the
    # trigger.schedule step carries `duration_minutes`; anchor the workflow's
    # expires_at at activation + the SHORTEST such window. The scheduler
    # already refuses to fire — and auto-pauses — any workflow past its
    # expires_at (_poll_due_workflows R4b), so no scheduler change is needed;
    # this is what makes "for the next hour" actually stop after an hour
    # instead of firing forever. Only tighten (never loosen) a pre-existing
    # expiry so an IPO/event window set elsewhere still wins if it's sooner.
    _window_min = _schedule_window_minutes(wf)
    if _window_min is not None:
        _window_expiry = wf.activated_at + timedelta(minutes=_window_min)
        if wf.expires_at is None or _window_expiry < wf.expires_at:
            wf.expires_at = _window_expiry
    # Compute `next_run_at` for trigger.schedule workflows. Invalid
    # cron / timezone fails the activation 422 (closes reviewer
    # Day-2 edge case #1 — never silently arm a dead schedule).
    try:
        upsert_workflow_schedule(db, wf)
    except InvalidCronError as e:
        raise validation_error(
            str(e),
            details={"step_index": 0, "field": "config.cron"},
        )
    db.commit()
    db.refresh(wf)

    # Forward-test (P6): register an ARMED idea for trading agents so they
    # appear in Paper -> Ideas immediately on activation — NO order is placed
    # and NO position is created here. The position only appears later, when
    # the agent's trigger actually fires and action.place_order runs (the fill
    # then attributes to this same idea, via dedup on workflow_id). Paper-mode
    # only; never let it block activation.
    try:
        _register_armed_idea(db, user_id, wf)
    except Exception:  # noqa: BLE001
        logger.exception(
            "[workflows.activate] armed-idea registration failed wf=%s",
            workflow_id,
        )

    _ph = get_posthog()
    if _ph:
        trigger_type = next(
            (s.step_type for s in sorted(wf.steps, key=lambda s: int(s.step_index))),
            None,
        )
        _ph.capture("workflow_activated", distinct_id=str(user_id), properties={
            "workflow_id": str(wf.id),
            "trigger_type": trigger_type,
            "step_count": len(wf.steps),
        })

    return _to_workflow_out(wf)


@router.post(
    "/workflows/{workflow_id}/pause",
    response_model=WorkflowOut,
    summary="Pause a workflow",
)
def pause_workflow(
    workflow_id: str,
    user_id: int = Depends(require_user),
    db: Session = Depends(get_db),
) -> WorkflowOut:
    wf = _workflow_for_user(db, user_id, workflow_id)
    if wf.status == WorkflowStatus.archived:
        raise state_conflict(
            "cannot pause an archived workflow",
            details={"current_status": "archived"},
        )
    wf.status = WorkflowStatus.paused
    upsert_workflow_schedule(db, wf)  # clears next_run_at
    db.commit()
    db.refresh(wf)
    return _to_workflow_out(wf)


@router.post(
    "/workflows/{workflow_id}/archive",
    response_model=WorkflowOut,
    summary="Archive a workflow",
)
def archive_workflow(
    workflow_id: str,
    user_id: int = Depends(require_user),
    db: Session = Depends(get_db),
) -> WorkflowOut:
    wf = _workflow_for_user(db, user_id, workflow_id)
    wf.status = WorkflowStatus.archived
    upsert_workflow_schedule(db, wf)  # clears next_run_at
    db.commit()
    db.refresh(wf)
    return _to_workflow_out(wf)


# ── Hard delete ───────────────────────────────────────────────────────


class _WorkflowDeletedResponse(BaseModel):
    deleted: bool
    id: str


@router.delete(
    "/workflows/{workflow_id}",
    response_model=_WorkflowDeletedResponse,
    summary="Delete a workflow and all its children",
    description=(
        "Hard-deletes the user's workflow and every child row "
        "(steps, runs + run-steps, approvals, webhook tokens). Cross-user "
        "or missing workflow returns 404 (never 403, per §1). Idea / paper "
        "rows attributed to the workflow are NOT touched — they keep the "
        "soft `workflow_id` reference so the forward-test track record "
        "survives the agent's deletion."
    ),
)
def delete_workflow(
    workflow_id: str,
    user_id: int = Depends(require_user),
    db: Session = Depends(get_db),
) -> _WorkflowDeletedResponse:
    wf = _workflow_for_user(db, user_id, workflow_id)

    # Delete children explicitly in FK-safe order. `steps`,
    # `webhook_tokens`, and a run's `steps` cascade via the ORM
    # relationship, but `runs` and `approvals` are NOT cascaded off the
    # workflow, so we drain them by hand. Order: approvals -> run_steps
    # -> runs (children of runs first), then steps + tokens, then the
    # workflow row itself.
    run_ids = [
        r.id
        for r in db.query(WorkflowRun.id)
        .filter(WorkflowRun.workflow_id == wf.id)
        .all()
    ]
    if run_ids:
        db.query(WorkflowApproval).filter(
            WorkflowApproval.run_id.in_(run_ids)
        ).delete(synchronize_session=False)
        db.query(WorkflowRunStep).filter(
            WorkflowRunStep.run_id.in_(run_ids)
        ).delete(synchronize_session=False)
        db.query(WorkflowRun).filter(
            WorkflowRun.workflow_id == wf.id
        ).delete(synchronize_session=False)

    db.query(WorkflowWebhookToken).filter(
        WorkflowWebhookToken.workflow_id == wf.id
    ).delete(synchronize_session=False)
    db.query(WorkflowStep).filter(
        WorkflowStep.workflow_id == wf.id
    ).delete(synchronize_session=False)

    db.delete(wf)
    db.commit()
    return _WorkflowDeletedResponse(deleted=True, id=workflow_id)


@router.post(
    "/workflows/{workflow_id}/run",
    response_model=RunCreatedResponse,
    status_code=201,
    summary="Manually run a workflow",
)
async def run_workflow(
    workflow_id: str,
    user_id: int = Depends(require_user),
    db: Session = Depends(get_db),
) -> RunCreatedResponse:
    """Create a `triggered_by='manual'` run row and enqueue it on the
    in-process worker. Allowed for any non-archived status — including
    paused (so users can test before activating)."""
    wf = _workflow_for_user(db, user_id, workflow_id)
    if wf.status == WorkflowStatus.archived:
        raise state_conflict(
            "cannot run an archived workflow",
            details={"current_status": "archived"},
        )
    run = WorkflowRun(
        workflow_id=wf.id,
        workflow_version=int(wf.version),
        triggered_by="manual",
        status=RunStatus.running,
        context={},
    )
    db.add(run)
    wf.last_run_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(run)

    # Enqueue on the loop. The engine takes care of acquiring the
    # single-instance lock; if held, the run terminates as cancelled.
    engine = WorkflowEngine()
    asyncio.create_task(engine.execute_run(str(run.id)))

    return RunCreatedResponse(run_id=str(run.id))


# ── Per-agent performance (sparkline + headline metrics) ──────────────
# `_NavPoint` is defined earlier in the file (next to `_StrategyReturn`,
# which reuses it for the batched per-workflow series in /workflows/summary).


class _WorkflowPerformanceResponse(BaseModel):
    """Per-agent sparkline + headline metrics, all on-read from real data.

    `series` is the linked ForwardIdea's idea_nav over time (empty when
    the agent has never filled / has no forward-test idea). `return_pct`
    is the idea's cum_return_pct from its scorecard_cache. Run stats come
    from WorkflowRun rows. `has_data` is true when there is either a NAV
    series to chart or at least one run to summarise.
    """
    series: list[_NavPoint] = Field(default_factory=list)
    return_pct: Optional[float] = None
    last_run_at: Optional[datetime] = None
    run_count: int = 0
    success_rate: Optional[float] = None
    has_data: bool = False


@router.get(
    "/workflows/{workflow_id}/performance",
    response_model=_WorkflowPerformanceResponse,
    summary="Per-agent sparkline + headline metrics",
    description=(
        "Returns the agent's forward-test NAV series (from "
        "PaperIdeaNavSnapshot for the ForwardIdea whose workflow_id matches "
        "this workflow) plus headline run metrics (run_count, success_rate, "
        "last_run_at) from WorkflowRun. Cross-user / missing workflow → 404. "
        "All numbers are real; absent data yields an empty series, nulls, and "
        "has_data=false so the FE can show a 'no runs yet' state."
    ),
)
def workflow_performance(
    workflow_id: str,
    user_id: int = Depends(require_user),
    db: Session = Depends(get_db),
) -> _WorkflowPerformanceResponse:
    wf = _workflow_for_user(db, user_id, workflow_id)

    # ── Run metrics ──────────────────────────────────────────────────
    runs = (
        db.query(WorkflowRun.status, WorkflowRun.started_at)
        .filter(WorkflowRun.workflow_id == wf.id)
        .all()
    )
    run_count = len(runs)
    succeeded = sum(
        1 for status, _ in runs if status == RunStatus.succeeded
    )
    success_rate = (
        round(succeeded / run_count * 100.0, 2) if run_count > 0 else None
    )
    last_run_at = wf.last_run_at

    # ── Forward-test NAV series + return ─────────────────────────────
    series: list[_NavPoint] = []
    return_pct: Optional[float] = None
    idea = (
        db.query(ForwardIdea)
        .filter(
            ForwardIdea.user_id == user_id,
            ForwardIdea.origin_kind == "workflow",
            ForwardIdea.workflow_id == str(wf.id),
        )
        .order_by(ForwardIdea.created_at.desc())
        .first()
    )
    if idea is not None:
        # LIVE return (marked on read) so the card's headline % matches the
        # agent's Positions panel and the portfolio — not the stale EOD
        # scorecard, which read unmarked positions and drifted (e.g. −0.9%
        # while the live position is +3.55%).
        return_pct = _live_idea_return_pct(db, idea)
        snaps = (
            db.query(PaperIdeaNavSnapshot)
            .filter(PaperIdeaNavSnapshot.idea_id == idea.id)
            .order_by(PaperIdeaNavSnapshot.as_of_date.asc())
            .all()
        )
        for snap in snaps:
            series.append(
                _NavPoint(
                    date=snap.as_of_date.isoformat(),
                    nav=float(snap.idea_nav),
                )
            )
        # Pin a live "today" point onto the EOD series so the sparkline ends at
        # the current mark — the line and the headline % then tell one story
        # instead of a red decline under a green number.
        try:
            from backend.paper.idea_valuation import compute_idea_nav

            live_nav = float(compute_idea_nav(db, idea)["idea_nav"])
            today = datetime.now(timezone.utc).date().isoformat()
            if series and series[-1].date == today:
                series[-1] = _NavPoint(date=today, nav=live_nav)
            else:
                series.append(_NavPoint(date=today, nav=live_nav))
        except Exception:  # noqa: BLE001 — sparkline tail is best-effort
            logger.debug("live idea nav tail failed for %s", idea.id, exc_info=True)

    has_data = bool(series) or run_count > 0

    return _WorkflowPerformanceResponse(
        series=series,
        return_pct=return_pct,
        last_run_at=last_run_at,
        run_count=run_count,
        success_rate=success_rate,
        has_data=has_data,
    )


# ── Per-agent open positions ──────────────────────────────────────────


class _AgentPosition(BaseModel):
    """One still-open symbol held by this agent, with returns since fill."""
    symbol: str
    quantity: int
    avg_cost: float           # weighted, charge-inclusive basis / share
    last_price: Optional[float] = None   # None when no live mark available
    invested: float           # cost basis of the open lots
    market_value: float
    unrealized_pnl: float
    unrealized_pnl_pct: Optional[float] = None
    realized_pnl: float       # booked P&L on this symbol so far


class _AgentPositionsResponse(BaseModel):
    """The positions this agent's trades opened, plus rolled-up returns.

    Derived on-read by FIFO-replaying the fills tagged to this workflow's
    ForwardIdea (never the account-grain PaperPosition cache, which can't be
    attributed to one agent). Absent data → empty list + zeros + has_data
    false, never fabricated numbers.
    """
    positions: list[_AgentPosition] = Field(default_factory=list)
    invested: float = 0.0
    market_value: float = 0.0
    unrealized_pnl: float = 0.0
    unrealized_pnl_pct: Optional[float] = None
    realized_pnl: float = 0.0
    has_data: bool = False


@router.get(
    "/workflows/{workflow_id}/positions",
    response_model=_AgentPositionsResponse,
    summary="Open positions held for this agent + returns since the trades",
    description=(
        "The positions this agent's own trades opened — one row per "
        "still-open symbol with net quantity, weighted charge-inclusive "
        "cost basis, current mark, market value, and unrealized P&L since "
        "the fills, plus rolled-up invested / market-value / unrealized / "
        "realized totals. Attribution is by FIFO replay over PaperFill rows "
        "tagged to the ForwardIdea whose workflow_id matches this workflow "
        "(the account-grain position cache carries no agent attribution). "
        "Cross-user / missing workflow → 404. No idea / no fills yet → empty "
        "positions, zero totals, has_data=false — never fabricated numbers."
    ),
)
def workflow_positions(
    workflow_id: str,
    user_id: int = Depends(require_user),
    db: Session = Depends(get_db),
) -> _AgentPositionsResponse:
    wf = _workflow_for_user(db, user_id, workflow_id)

    idea = (
        db.query(ForwardIdea)
        .filter(
            ForwardIdea.user_id == user_id,
            ForwardIdea.origin_kind == "workflow",
            ForwardIdea.workflow_id == str(wf.id),
        )
        .order_by(ForwardIdea.created_at.desc())
        .first()
    )
    if idea is None:
        return _AgentPositionsResponse(has_data=False)

    from backend.paper.idea_valuation import compute_idea_positions

    rollup = compute_idea_positions(db, idea)
    positions = [
        _AgentPosition(**p) for p in cast(list, rollup["positions"])
    ]
    has_data = bool(positions) or float(rollup["realized_pnl"]) != 0.0

    return _AgentPositionsResponse(
        positions=positions,
        invested=cast(float, rollup["invested"]),
        market_value=cast(float, rollup["market_value"]),
        unrealized_pnl=cast(float, rollup["unrealized_pnl"]),
        unrealized_pnl_pct=cast(Optional[float], rollup["unrealized_pnl_pct"]),
        realized_pnl=cast(float, rollup["realized_pnl"]),
        has_data=has_data,
    )


# ── Workflow draft backtest ───────────────────────────────────────────


class _BacktestDraftRequest(BaseModel):
    """Body for ``POST /api/workflows/backtest-draft``.

    The chat sends the same draft shape it returns from
    ``propose_workflow``. Period defaults to 5y to match the
    indicator-backtest UX. Name is purely cosmetic — used in the
    summary string of the result."""
    name: str = Field(default="Workflow")
    description: str | None = None
    steps: list[dict] = Field(default_factory=list)
    period: str = Field(
        default="5y",
        description=(
            "yfinance period: 1mo, 3mo, 6mo, 1y, 2y, 5y, 10y, max."
        ),
    )
    interval: str = Field(
        default="1d",
        description=(
            "Bar interval. Daily by default; intraday accepted (1h, "
            "15m, 5m, 30m — normalised via core.data.intervals). Data "
            "windows for intraday are shallow (yfinance 1h=730d, Kite "
            "1h=400d) and clamped honestly rather than fabricated."
        ),
    )


@router.post(
    "/workflows/backtest-draft",
    summary="Backtest a workflow draft against historical bars",
    description=(
        "Replays the workflow's logic over historical daily prices and "
        "returns the same chart payload the indicator backtester does. "
        "Eligible workflow shapes: trigger.schedule / trigger.indicator / "
        "trigger.price + action.place_order. Returns 422 with a "
        "user-readable reason for shapes that can't be replayed "
        "historically (event triggers, fundamentals fetches, etc.)."
    ),
)
async def backtest_draft(
    body: _BacktestDraftRequest,
    user_id: int = Depends(require_user),
) -> dict:
    from backend.services.workflow_backtester import (
        backtest_workflow,
        check_eligibility,
    )

    elig = check_eligibility(body.steps)
    if not elig.eligible:
        return {
            "eligible": False,
            "reason": elig.reason,
            "warnings": [],
        }

    try:
        result = await asyncio.to_thread(
            backtest_workflow,
            body.steps,
            period=body.period,
            name=body.name,
            interval=body.interval,
        )
    except ValueError as e:
        return {
            "eligible": False,
            "reason": str(e),
            "warnings": elig.warnings,
        }

    # Match the shape the FE chart card already consumes (mirrors the
    # raw_data block on POST /chat for indicator backtests).
    return {
        "eligible": True,
        "warnings": elig.warnings,
        "_render_hint": "indicator_backtest_chart",
        "symbol": result.symbol,
        "indicator": result.indicator,
        "indicator_period": result.indicator_period,
        "operator": result.operator,
        "threshold": result.threshold,
        "period_label": result.period_label,
        "price_curve": result.price_curve,
        "equity_curve": result.equity_curve,
        "indicator_curve": result.indicator_curve,
        "signals": result.signals,
        "metrics": result.metrics,
        "bench_buy_hold_return_pct": result.bench_buy_hold_return_pct,
        "benchmark_label": getattr(result, "benchmark_label", None),
        "methodology": result.methodology,
        "summary": result.summary_text,
        "strategy_kind": getattr(result, "strategy_kind", "indicator"),
        "display_title": getattr(result, "display_title", None),
        "display_subtitle": getattr(result, "display_subtitle", None),
        "window_start": result.window_start,
        "window_end": result.window_end,
        "n_bars": result.n_bars,
        "bar_interval": result.bar_interval,
    }
