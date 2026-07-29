"""Pydantic models for the news_events subsystem.

Mirror of backend/workflows/schemas.py style: every model inherits
from ``_Strict`` (``extra='ignore'``), every field is explicitly
typed.

Phase 1 only uses the small set of admin / health DTOs at the bottom.
The EventSpec / ResolutionCriteria / RetractionPolicy / KeywordSet
models are declared now so the schema is committed up front and the
Phase 4 parser can target them without churn.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class _Strict(BaseModel):
    """Base for every news_events model — see workflows/schemas.py for
    the rationale on ``extra='ignore'``. We keep the same trade-off so
    a planner LLM that drops one stray field doesn't lose the whole
    spec."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)


# ── Keyword set for the Stage 2 funnel filter ────────────────────────

class KeywordSet(_Strict):
    """The Stage 2 keyword/regex filter operates on title+summary.

    Phase 1 stores keyword_set but does not yet evaluate it — Stage 2
    is wired in Phase 2. Three lists with the following semantics:

      - ``must_have_one``: at least one term from this list must appear
        for the article to pass.
      - ``must_have_one_of``: list-of-lists; each inner list contributes
        an additional "at least one of" constraint. Lets the planner
        say "must mention RBI AND (rate OR policy)" cleanly.
      - ``must_not_have``: any hit on this list rejects the article.
    """

    must_have_one: list[str] = Field(default_factory=list)
    must_have_one_of: list[list[str]] = Field(default_factory=list)
    must_not_have: list[str] = Field(default_factory=list)

    @field_validator("must_have_one", "must_not_have")
    @classmethod
    def _strip_blanks(cls, v: list[str]) -> list[str]:
        return [s.strip() for s in v if s and s.strip()]


# ── Resolution criteria + retraction policy ──────────────────────────

class ResolutionCriteria(_Strict):
    """Per-tier firing rule.

    The aggregator (Phase 5) evaluates these against the classifier
    verdicts in news_article_classifications. ``primary_sources`` is a
    list of source_ids that count as authoritative; secondaries are
    everything else. Tier 3 may additionally specify
    ``prediction_market_threshold`` for the Polymarket cross-check.
    """

    primary_sources: list[str] = Field(default_factory=list)
    min_secondary_confirmations: int = Field(default=0, ge=0, le=10)
    min_confidence: float = Field(default=0.85, ge=0.0, le=1.0)
    prediction_market_threshold: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    conflict_policy: Literal["hold", "fire", "alert"] = Field(default="hold")


class RetractionPolicy(_Strict):
    """What to do if a fired event is retracted within the safety window.

    The watcher polls for ``RETRACTION`` verdicts from Stage 6 for
    ``safety_window_minutes`` after firing. If one is detected and
    ``action`` is:

      - ``cancel_pending_approvals``: best-effort cancel any
        WorkflowApproval rows tied to the fired run.
      - ``cancel_and_alert``: above + post an alert via the existing
        observability surface.
      - ``ignore``: log and move on (Tier-1 official events).
    """

    safety_window_minutes: int = Field(default=120, ge=0, le=1440)
    action: Literal["cancel_pending_approvals", "cancel_and_alert", "ignore"] = (
        Field(default="cancel_and_alert")
    )


# ── EventSpec ────────────────────────────────────────────────────────

class EventSpec(_Strict):
    """The full event spec persisted as a ``news_event_specs`` row.

    Phase 4 lands the NL parser that produces this. Phase 5 lands the
    activation flow that turns ``state`` into ``active`` and arms the
    watchers.
    """

    id: Optional[str] = None
    user_id: int
    workflow_id: Optional[str] = None
    tier: Literal["tier1", "tier2", "tier3"]
    description: str = Field(..., min_length=4)
    resolution_criteria: ResolutionCriteria
    retraction_policy: RetractionPolicy
    keyword_set: KeywordSet
    deadline_at: Optional[datetime] = None
    watch_window_start_at: Optional[datetime] = None
    state: Literal[
        "draft",
        "pending_disambiguation",
        "active",
        "fired",
        "expired",
        "cancelled",
    ] = "draft"


# ── Admin / health DTOs (used by Phase 1's router) ───────────────────

class SourceHealthRow(_Strict):
    """Shape of one row in ``GET /api/news-events/admin/sources``."""

    source_id: str
    display_name: str
    feed_url: str
    enabled: bool
    poll_interval_seconds: int
    last_successful_fetch_at: Optional[datetime] = None
    last_error_at: Optional[datetime] = None
    last_error_message: Optional[str] = None
    consecutive_failures: int = 0
    articles_seen_24h: int = 0
    articles_passed_24h: int = 0
    updated_at: Optional[datetime] = None


class SourceHealthResponse(_Strict):
    """Wrapper so we can grow the response without a breaking change."""

    sources: list[SourceHealthRow]


class FunnelMetricsResponse(_Strict):
    """Phase 1 reports raw ingest counts only. Phase 2+ adds the
    after-dedup / after-keyword / sent-to-llm / fired counters."""

    window_hours: int = 24
    sources_active: int = 0
    articles_ingested: int = 0
    articles_deduped: int = 0  # always equals ingested in Phase 1
    articles_after_keyword: int = 0  # 0 in Phase 1
    articles_sent_to_llm: int = 0  # 0 in Phase 1
    events_fired: int = 0  # 0 in Phase 1


# ── Phase 4 — spec API DTOs ──────────────────────────────────────────


class DraftSpecRequest(_Strict):
    """Payload for ``POST /api/news-events/specs``.

    The user supplies free-form text; the parser does the rest.
    Optional ``workflow_id`` lets the user pin the target workflow
    at draft time; it can also be set later via PATCH.
    """

    text: str = Field(..., min_length=4, max_length=2_000)
    workflow_id: Optional[str] = Field(default=None, max_length=64)


class DisambiguationOption(_Strict):
    """One multi-choice option inside a Tier-3 disambiguation question.

    ``apply`` describes how the option modifies the pending spec when
    selected. Phase-4 supports three keys:

      - ``resolution_criteria``: dict, merged into the spec's
        ``resolution_criteria`` field.
      - ``retraction_policy``: dict, merged into ``retraction_policy``.
      - ``description_suffix``: str, appended to the description.

    Other keys are ignored (forward-compat).
    """

    id: str = Field(..., min_length=1, max_length=64)
    label: str = Field(..., min_length=1, max_length=200)
    apply: dict[str, object] = Field(default_factory=dict)


class DisambiguationQuestion(_Strict):
    """One question presented to the user during disambiguation."""

    id: str = Field(..., min_length=1, max_length=64)
    text: str = Field(..., min_length=4, max_length=500)
    options: list[DisambiguationOption] = Field(..., min_length=2, max_length=6)


class DisambiguationSessionView(_Strict):
    """Shape returned to the FE when the spec is in
    ``pending_disambiguation``."""

    session_id: str
    spec_id: str
    questions: list[DisambiguationQuestion]
    # Subset of question.id → option.id pairs the user has answered
    # so far. Empty on first response.
    answers: dict[str, str] = Field(default_factory=dict)
    expires_at: datetime


class DisambiguationAnswer(_Strict):
    """Payload for ``POST /api/news-events/specs/{id}/disambiguate``."""

    question_id: str = Field(..., min_length=1, max_length=64)
    option_id: str = Field(..., min_length=1, max_length=64)


class EventSpecResponse(_Strict):
    """Public view of a single spec row. Excludes internal
    bookkeeping that the FE doesn't need."""

    id: str
    user_id: int
    workflow_id: Optional[str] = None
    tier: Literal["tier1", "tier2", "tier3"]
    description: str
    resolution_criteria: dict
    retraction_policy: dict
    keyword_set: dict
    deadline_at: Optional[datetime] = None
    watch_window_start_at: Optional[datetime] = None
    state: Literal[
        "draft",
        "pending_disambiguation",
        "active",
        "fired",
        "expired",
        "cancelled",
    ]
    created_at: datetime
    updated_at: datetime


class CreateSpecResponse(_Strict):
    """Returned by ``POST /api/news-events/specs``. Exactly one of
    ``spec`` (Tier 1/2 case) or ``disambiguation`` (Tier 3 case) is
    non-null."""

    spec: Optional[EventSpecResponse] = None
    disambiguation: Optional[DisambiguationSessionView] = None
    warnings: list[str] = Field(default_factory=list)


class CreatePolymarketSpecRequest(_Strict):
    """Payload for ``POST /api/news-events/specs/polymarket``.

    Used to persist a Polymarket WS-driven trigger after the user has
    either accepted the chat tool's auto-pick OR chosen a candidate
    from the picker. The created spec lands in state ``draft``;
    activation is a separate POST to ``/specs/{id}/activate`` so the
    UX of "review → activate" matches the rest of the surface.
    """

    event_description: str = Field(..., min_length=4, max_length=2_000)
    market_id: str = Field(..., min_length=1, max_length=128)
    token_id: str = Field(..., min_length=1, max_length=256)
    side: Literal["YES", "NO"] = "YES"
    threshold: float = Field(..., ge=0.0, le=1.0)
    direction: Literal["above", "below"] = "above"
    question: Optional[str] = Field(default=None, max_length=500)
    workflow_id: Optional[str] = Field(default=None, max_length=64)


class ListSpecsResponse(_Strict):
    """``GET /api/news-events/specs`` — list-only view."""

    specs: list[EventSpecResponse]


# ── Phase 5 — fired-event audit DTOs ─────────────────────────────────


class FiredClassificationView(_Strict):
    """One supporting classification, surfaced in the audit view."""

    classification_id: str
    article_id: str
    article_title: str
    article_url: str
    source_id: str
    classifier_verdict: Optional[str] = None
    confidence: Optional[float] = None
    excerpt: Optional[str] = None
    embedding_similarity: Optional[float] = None


class FiredEventResponse(_Strict):
    """Returned from ``GET /api/news-events/fired/{id}``.

    Carries the full "why we fired" payload — the supporting
    classifications, the aggregated confidence, the audit timestamps,
    plus the linked workflow_run_id when one exists.
    """

    id: str
    event_spec_id: str
    spec_description: str
    spec_tier: Literal["tier1", "tier2", "tier3"]
    workflow_run_id: Optional[str] = None
    fired_at: datetime
    aggregated_confidence: float
    retraction_window_ends_at: Optional[datetime] = None
    retraction_status: Literal["none", "detected", "handled"] = "none"
    # Phase 6 audit fields.
    retraction_detected_at: Optional[datetime] = None
    retraction_classification_id: Optional[str] = None
    retraction_action_taken: Optional[str] = None
    prediction_market_snapshot: Optional[dict] = None
    supporting: list[FiredClassificationView]


class ForcePollResponse(_Strict):
    """Returned from ``POST /api/news-events/admin/sources/{id}/poll``."""

    source_id: str
    status: Literal["ok", "skipped_disabled", "error"]
    articles_seen: int = 0
    articles_new: int = 0
    # Phase 2 surface. ``articles_after_stage1`` is the count of new
    # rows that survived cross-source dedup; ``articles_after_stage2``
    # is the sum of (article, spec) classifications that passed the
    # keyword filter. The latter is 0 when no active spec exists.
    articles_after_stage1: int = 0
    articles_after_stage2: int = 0
    error: Optional[str] = None
    fetched_at: datetime
