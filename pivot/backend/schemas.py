from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field


# ─── Auth Schemas ───────────────────────────────────────────────────

class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, description="Minimum 8 characters")
    full_name: Optional[str] = None


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user_id: int
    email: str


class TokenRefreshRequest(BaseModel):
    refresh_token: str


# ─── User Schemas ────────────────────────────────────────────────────

class UserResponse(BaseModel):
    id: int
    email: str
    full_name: Optional[str]
    is_active: bool
    is_verified: bool
    created_at: datetime

    class Config:
        from_attributes = True


# ─── Health Check ────────────────────────────────────────────────────

class HealthResponse(BaseModel):
    status: str
    version: str
    database: str
    redis: str


# ─── Agent System (Workflows v1) ──────────────────────────────────────
#
# Mirrors docs/API_CONTRACT.md §3-§4 and §8.1. Strict typing — request
# bodies and responses are explicit. `config` and `output` payloads are
# typed as dict[str, object] (heterogeneous, validated separately by the
# step-type registry's JSON schema layer).

WorkflowStatusLiteral = Literal["draft", "active", "paused", "archived"]
RunStatusLiteral = Literal[
    "running", "succeeded", "failed", "cancelled", "awaiting_approval"
]
StepStatusLiteral = Literal[
    "pending", "running", "succeeded", "failed", "skipped", "awaiting_approval"
]
TriggeredByLiteral = Literal[
    "schedule", "manual", "webhook",
    "price_alert", "indicator_alert", "event_alert",
]
HaltReasonLiteral = Literal["condition_not_met", "time_budget"]


# ── Workflow CRUD ─────────────────────────────────────────────────────

class StepInput(BaseModel):
    """Step shape on POST/PATCH. step_index is implied by list order."""
    step_type: str = Field(..., min_length=1, max_length=64)
    label: Optional[str] = Field(default=None, max_length=255)
    # config is opaque to Pydantic — registry validates against per-type
    # JSON schema. Typed as dict[str, object] to satisfy strict typing
    # without the looseness of `Any`.
    config: dict[str, object] = Field(default_factory=dict)


class StepOut(BaseModel):
    """Step shape on responses, includes server-assigned id + index."""
    id: str
    step_index: int
    step_type: str
    label: Optional[str]
    config: dict[str, object]

    model_config = ConfigDict(from_attributes=True)


class WorkflowCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = None
    single_instance: bool = True
    steps: list[StepInput] = Field(default_factory=list)


class WorkflowPatch(BaseModel):
    """All fields optional. If `steps` is provided it FULLY replaces the
    existing step list (no partial step edits in v1)."""
    name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    description: Optional[str] = None
    single_instance: Optional[bool] = None
    steps: Optional[list[StepInput]] = None


class WorkflowSummary(BaseModel):
    """List-view shape (no `steps` payload to keep responses small)."""
    id: str
    name: str
    description: Optional[str]
    status: WorkflowStatusLiteral
    version: int
    single_instance: bool
    created_at: datetime
    updated_at: datetime
    activated_at: Optional[datetime]
    last_run_at: Optional[datetime]
    next_run_at: Optional[datetime]

    model_config = ConfigDict(from_attributes=True)


class WorkflowOut(WorkflowSummary):
    """Detail-view shape used by GET /api/workflows/{id}, etc."""
    steps: list[StepOut] = Field(default_factory=list)


class WorkflowListResponse(BaseModel):
    items: list[WorkflowSummary]
    next_cursor: Optional[str] = None


class RunCreatedResponse(BaseModel):
    run_id: str


# ── Run shapes ────────────────────────────────────────────────────────

class RunStepOut(BaseModel):
    step_index: int
    step_type: str
    status: StepStatusLiteral
    started_at: Optional[datetime]
    finished_at: Optional[datetime]
    output: Optional[dict[str, object]]
    error_message: Optional[str]
    attempts: int

    model_config = ConfigDict(from_attributes=True)


class RunOut(BaseModel):
    id: str
    workflow_id: str
    workflow_version: int
    triggered_by: TriggeredByLiteral
    started_at: datetime
    finished_at: Optional[datetime]
    status: RunStatusLiteral
    halt_reason: Optional[HaltReasonLiteral]
    error_message: Optional[str]
    # Keyed by stringified step_index per API_CONTRACT.md §4.
    context: dict[str, dict[str, object]] = Field(default_factory=dict)
    steps: list[RunStepOut] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


class RunSummary(BaseModel):
    """Run list-view shape: no `context` and no `steps[]`, with `step_count`
    int per API_CONTRACT.md §6.1."""
    id: str
    workflow_id: str
    workflow_version: int
    triggered_by: TriggeredByLiteral
    started_at: datetime
    finished_at: Optional[datetime]
    status: RunStatusLiteral
    halt_reason: Optional[HaltReasonLiteral]
    error_message: Optional[str]
    step_count: int

    model_config = ConfigDict(from_attributes=True)


class RunListResponse(BaseModel):
    items: list[RunSummary]
    next_cursor: Optional[str] = None


class RunCancelResponse(BaseModel):
    id: str
    status: RunStatusLiteral


class ScheduledRunItem(BaseModel):
    """One upcoming fire of an active workflow's `trigger.schedule`
    or `trigger.event`. Used by the FE Calendar tab."""
    workflow_id: str
    workflow_name: str
    trigger_type: str  # 'trigger.schedule' | 'trigger.event'
    fire_time: datetime  # UTC, ISO 8601
    fire_time_local: str  # Pre-formatted in trigger's tz, e.g. "3:55 PM IST"


class ScheduledRunsResponse(BaseModel):
    items: list[ScheduledRunItem]


# ── Approvals ────────────────────────────────────────────────────────

class ApprovalOut(BaseModel):
    id: str
    run_id: str
    step_index: int
    summary: str
    requested_at: datetime
    expires_at: datetime
    decision: Optional[Literal["approved", "rejected"]] = None
    decided_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class ApprovalListResponse(BaseModel):
    items: list[ApprovalOut]


class ApprovalDecisionRequest(BaseModel):
    decision: Literal["approved", "rejected"]


class ApprovalDecisionResponse(BaseModel):
    id: str
    decision: Literal["approved", "rejected"]
    decided_at: datetime


# ── Step-type catalog (GET /api/step-types, API_CONTRACT.md §8.1) ────

class StepCategory(BaseModel):
    id: str
    label: str


class StepTypeDefinition(BaseModel):
    """One entry in the catalog. config_schema is JSON Schema draft 2020-12;
    output_schema is the same dialect (or null when the step produces no
    output, e.g. triggers and notify.log)."""
    step_type: str
    category: str
    label: str
    description: str
    icon: str
    max_retries: int
    trigger_only: bool
    config_schema: dict[str, object]
    output_schema: Optional[dict[str, object]] = None


class StepTypeCatalogResponse(BaseModel):
    catalog_version: str
    categories: list[StepCategory]
    step_types: list[StepTypeDefinition]


# ── Standard error envelope (docs/API_CONTRACT.md §2) ─────────────────

class ErrorBody(BaseModel):
    code: str
    message: str
    details: Optional[dict[str, object]] = None


class ErrorResponse(BaseModel):
    error: ErrorBody
