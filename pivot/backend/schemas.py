from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


# ─── Auth Schemas ───────────────────────────────────────────────────
#
# Password policy and email normalisation live on the Pydantic models so
# every entry point (REST handler, propose-bridge, test harness) shares
# one source of truth. Anywhere a UserCreate / UserLogin is constructed
# the same rules apply, so the API can't drift from the policy.


def _normalize_email(value: str) -> str:
    """Trim + lowercase the email. Applied via field_validator on
    UserCreate / UserLogin so the DB only ever sees the canonical form
    (and case-only duplicates can't sneak past the UNIQUE index)."""
    if not isinstance(value, str):
        # EmailStr will already have rejected non-strings, but be defensive
        # so this helper is safe to reuse from other entry points.
        return value
    return value.strip().lower()


def _validate_password_strength(value: str) -> str:
    """Beta password policy: >=8 chars, at least one letter, at least one
    digit. Kept narrow on purpose — long passphrases shouldn't be rejected
    for missing punctuation. Raises ValueError with a single clear message
    Pydantic surfaces as a 422 validation error."""
    if not isinstance(value, str) or len(value) < 8:
        raise ValueError(
            "password must be at least 8 characters and contain a letter and a digit",
        )
    has_letter = any(c.isalpha() for c in value)
    has_digit = any(c.isdigit() for c in value)
    if not (has_letter and has_digit):
        raise ValueError(
            "password must be at least 8 characters and contain a letter and a digit",
        )
    return value


class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(
        min_length=8,
        description="Minimum 8 characters; must contain a letter and a digit",
    )
    full_name: Optional[str] = None

    @field_validator("email", mode="before")
    @classmethod
    def _normalize_email_field(cls, v: str) -> str:
        return _normalize_email(v)

    @field_validator("password")
    @classmethod
    def _validate_password_field(cls, v: str) -> str:
        return _validate_password_strength(v)


class UserLogin(BaseModel):
    email: EmailStr
    password: str

    @field_validator("email", mode="before")
    @classmethod
    def _normalize_email_field(cls, v: str) -> str:
        return _normalize_email(v)


class PasswordResetConfirm(BaseModel):
    """Body for POST /auth/reset-password. Password strength enforced
    identically to UserCreate so the policy can't be bypassed via reset."""
    token: str = Field(..., min_length=1)
    new_password: str = Field(min_length=8)

    @field_validator("new_password")
    @classmethod
    def _validate_password_field(cls, v: str) -> str:
        return _validate_password_strength(v)


class EmailVerifyRequest(BaseModel):
    token: str = Field(..., min_length=1)


class ForgotPasswordRequest(BaseModel):
    email: EmailStr

    @field_validator("email", mode="before")
    @classmethod
    def _normalize_email_field(cls, v: str) -> str:
        return _normalize_email(v)


class GoogleAuthRequest(BaseModel):
    """Body for POST /auth/google. `access_token` is the Google OAuth 2.0
    access token the browser obtained via Google Identity Services
    (initTokenClient, scope `openid email profile`). The backend verifies it
    against Google before trusting any identity claim."""
    access_token: str = Field(min_length=1)


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
    """Step shape on responses, includes server-assigned id + index.

    `next_run_at` is set on `trigger.schedule` steps when the workflow is
    active and the scheduler has computed the next fire time. NULL on
    every other step type and on paused / archived workflows.
    """
    id: str
    step_index: int
    step_type: str
    label: Optional[str]
    config: dict[str, object]
    next_run_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class WorkflowCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = None
    single_instance: bool = True
    steps: list[StepInput] = Field(default_factory=list)
    # R4b: optional auto-deactivation timestamp. NULL = perpetual.
    expires_at: Optional[datetime] = None


class WorkflowPatch(BaseModel):
    """All fields optional. If `steps` is provided it FULLY replaces the
    existing step list (no partial step edits in v1)."""
    name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    description: Optional[str] = None
    single_instance: Optional[bool] = None
    steps: Optional[list[StepInput]] = None
    expires_at: Optional[datetime] = None


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
    expires_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class WorkflowOut(WorkflowSummary):
    """Detail-view shape used by GET /api/workflows/{id}, etc.

    `diagnostics` carries the full lint result (errors + warnings + info)
    computed by `backend.workflows.compat.lint_workflow` at response time
    so the editor can surface advisories on already-saved workflows
    without re-POSTing to /api/workflows/lint. Each item is the dict form
    of `compat.Diagnostic`: `{step_index, severity, code, message, field,
    suggested_fix}`. Empty list when the workflow lints clean.
    """
    steps: list[StepOut] = Field(default_factory=list)
    diagnostics: list[dict[str, object]] = Field(default_factory=list)


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
    """One upcoming fire of an active workflow's `trigger.schedule`.
    Used by the FE Calendar tab."""
    workflow_id: str
    workflow_name: str
    trigger_type: str  # 'trigger.schedule'
    fire_time: datetime  # UTC, ISO 8601
    fire_time_local: str  # Pre-formatted in trigger's tz, e.g. "3:55 PM IST"


class ScheduledRunsResponse(BaseModel):
    items: list[ScheduledRunItem]


# ── propose_workflow as a REST endpoint (Day 6 #38) ───────────────────

class ProposeWorkflowRequest(BaseModel):
    user_intent: str = Field(..., min_length=1, max_length=2000)


class ProposeWorkflowDraftStep(BaseModel):
    step_type: str
    label: Optional[str] = None
    config: dict[str, object] = Field(default_factory=dict)


class ProposeWorkflowResponse(BaseModel):
    """Mirror of WorkflowDraft from backend.workflows.propose so the
    frontend can render the result without double-decoding."""
    name: str
    description: Optional[str] = None
    steps: list[ProposeWorkflowDraftStep]
    rationale: Optional[str] = None
    warnings: list[str] = Field(default_factory=list)
    # Structured lint diagnostics (errors join `warnings` as text too); dict
    # form of compat.Diagnostic. Without this field the response model would
    # silently strip draft.diagnostics on model_validate. Empty when clean.
    diagnostics: list[dict[str, object]] = Field(default_factory=list)


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
    output, e.g. triggers and notify.log).

    `group` is the sub-group heading within the category (picker navigation);
    `compat` is the connection-logic metadata `{produces, requires, consumes}`
    from `backend.workflows.compat.catalog_compat` — used by the editor to
    bucket steps (recommended / available / needs-setup) at each insert
    position. Both are loose-typed pass-throughs (like config_schema) so the
    catalog source of truth stays in the registry/compat module, not here."""
    step_type: str
    category: str
    group: str = ""
    label: str
    description: str
    icon: str
    max_retries: int
    trigger_only: bool
    config_schema: dict[str, object]
    output_schema: Optional[dict[str, object]] = None
    compat: Optional[dict[str, object]] = None


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


# ─── View Markets (V2: belief -> expression -> deployment) ────────────
#
# Request/response shapes for the View Markets tables (migration 0023).
# Enums mirror the Postgres ENUM types as Literal[...] (one source of truth
# with backend.models ViewType / ViewStatus / ExpressionTier / ExpressionKind
# / ConfidenceDimension / ExpectationSource). Strict typing; ``config`` is a
# heterogeneous, builder-validated bag typed dict[str, object] like StepInput.
# All Out models read straight off the ORM (from_attributes=True).

ViewTypeLiteral = Literal["event", "relative", "theme"]
ViewStatusLiteral = Literal[
    "open", "developing", "consensus", "resolved", "archived"
]
ExpressionTierLiteral = Literal["conservative", "balanced", "aggressive"]
ExpressionKindLiteral = Literal[
    "basket", "option_strategy", "pair", "multi_asset", "hedge"
]
ConfidenceDimensionLiteral = Literal["outcome", "expression"]
ExpectationSourceLiteral = Literal["polymarket", "kalshi", "consensus", "model"]
SurpriseSignLiteral = Literal["positive", "negative", "inline"]


# ── Transmission edges ────────────────────────────────────────────────

class ViewTransmissionInput(BaseModel):
    """One cause->effect edge of a view's transmission DAG."""
    seq: int = 0
    from_node: str = Field(..., min_length=1)
    to_node: str = Field(..., min_length=1)
    edge_label: Optional[str] = None
    strength: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    evidence: Optional[str] = None


class ViewTransmissionOut(ViewTransmissionInput):
    id: str
    view_id: str

    model_config = ConfigDict(from_attributes=True)


# ── Expressions ───────────────────────────────────────────────────────

class ViewExpressionInput(BaseModel):
    """A deployable expression of a view at one risk tier. ``config`` is the
    kind-specific builder payload (legs / weights / thresholds), opaque here
    and validated by the expression builder."""
    tier: ExpressionTierLiteral
    expression_kind: ExpressionKindLiteral
    config: dict[str, object] = Field(default_factory=dict)
    rationale: Optional[str] = None
    risk_profile: Optional[str] = None
    capital_intensity: Optional[str] = None
    historical_strength: Optional[str] = None
    time_horizon: Optional[str] = None


class ViewExpressionOut(ViewExpressionInput):
    id: str
    view_id: str
    backtest_run_id: Optional[str] = None
    workflow_id: Optional[str] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ── Confidence dimensions ─────────────────────────────────────────────

class ViewConfidenceInput(BaseModel):
    """One confidence dimension (outcome vs expression), kept separate."""
    dimension: ConfidenceDimensionLiteral
    score: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    evidence: Optional[str] = None


class ViewConfidenceOut(ViewConfidenceInput):
    id: str
    view_id: str
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ── Market expectations / surprise framing ────────────────────────────

class ViewExpectationInput(BaseModel):
    """"What's priced in" vs the view's own number. READ from a prediction
    market / consensus / model — never an outcome-trading surface."""
    source: ExpectationSourceLiteral
    market_id: Optional[str] = None
    expected_value: Optional[float] = None
    user_view_value: Optional[float] = None
    surprise_sign: Optional[SurpriseSignLiteral] = None


class ViewExpectationOut(ViewExpectationInput):
    id: str
    view_id: str
    as_of: datetime
    resolved_value: Optional[float] = None

    model_config = ConfigDict(from_attributes=True)


# ── Views (CRUD) ──────────────────────────────────────────────────────

class MarketViewCreate(BaseModel):
    """Create a curated view (V1: backend-generated + human-reviewed). The
    child collections are optional on create — generators fill them in."""
    view_type: ViewTypeLiteral
    title: str = Field(..., min_length=1)
    thesis: Optional[str] = None
    category: Optional[str] = None
    time_horizon: Optional[str] = None
    resolution_date: Optional[datetime] = None
    expressions: list[ViewExpressionInput] = Field(default_factory=list)
    transmission: list[ViewTransmissionInput] = Field(default_factory=list)
    confidence: list[ViewConfidenceInput] = Field(default_factory=list)
    expectations: list[ViewExpectationInput] = Field(default_factory=list)


class MarketViewPatch(BaseModel):
    """All fields optional. Lifecycle moves status; publish stamps
    published_at server-side."""
    title: Optional[str] = Field(default=None, min_length=1)
    thesis: Optional[str] = None
    category: Optional[str] = None
    time_horizon: Optional[str] = None
    status: Optional[ViewStatusLiteral] = None
    resolution_date: Optional[datetime] = None


class MarketViewSummary(BaseModel):
    """Gallery / list-view shape (no child payloads)."""
    id: str
    user_id: Optional[int] = None
    view_type: ViewTypeLiteral
    title: str
    thesis: Optional[str] = None
    category: Optional[str] = None
    time_horizon: Optional[str] = None
    status: ViewStatusLiteral
    resolution_date: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
    published_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class MarketViewOut(MarketViewSummary):
    """Detail-view shape with the full transmission map, expressions,
    confidence dials, and expectations."""
    expressions: list[ViewExpressionOut] = Field(default_factory=list)
    transmission: list[ViewTransmissionOut] = Field(default_factory=list)
    confidence: list[ViewConfidenceOut] = Field(default_factory=list)
    expectations: list[ViewExpectationOut] = Field(default_factory=list)


class MarketViewListResponse(BaseModel):
    items: list[MarketViewSummary]
    next_cursor: Optional[str] = None


# ── Follows ───────────────────────────────────────────────────────────

class ViewFollowOut(BaseModel):
    id: str
    user_id: int
    view_id: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
