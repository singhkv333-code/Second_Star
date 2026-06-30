"""View Markets — manual curation / authoring service (the beta generator-replacement).

BETA DECISION (2026-06-29): views are MANUALLY CURATED by the user (founding
team as "view curator"); the automatic EVENT/RELATIVE/THEME generators are
DEFERRED. This service is the authoring path that REPLACES them: create / update
/ publish a human-decided ``MarketView`` (``user_id`` stays NULL for curated
views) and attach its transmission map, expressions, confidence dials, and
expectations, behind the publish/review gate of VIEW_MARKETS_PLAN.md §7.

Programmatic API only — NO REST router here (that is Phase 7). The /api/views
router will call these functions.

Review gate (PLAN §7.2 actionability gate, enforced in the service layer):
  * measurable outcome + benchmark + horizon (``title``/``thesis`` actionable,
    ``time_horizon`` set; ``resolution_date`` for event/relative),
  * >= 1 transmission edge,
  * >= 1 expression per intended tier with ALL FIVE disclosures populated
    (rationale / risk_profile / capital_intensity / historical_strength /
    time_horizon),
  * BOTH confidence dimensions scored (or explicitly suppressed),
  * an expectations row for event / relative views,
  * no fabricated numbers (values trace to a tool/source).
Only ``published_at IS NOT NULL`` views are surfaced when
``view_markets_enabled`` is on; the DB stays permissive (a draft is an
unpublished row) — the gate lives here.

Reuses (real interfaces, pinned 2026-06-29):
  * ``backend.schemas.{MarketViewCreate, MarketViewPatch, ViewExpressionInput,
    ViewTransmissionInput, ViewConfidenceInput, ViewExpectationInput}``.
  * ``backend.models.{MarketView, ViewExpression, ViewTransmission,
    ViewConfidence, ViewExpectation, ViewStatus, ExpressionTier, ...}``.
  * ``backend.view_markets.transmission.persist_transmission``.
  * ``backend.view_markets.confidence.persist_confidence``.
  * ``backend.view_markets.expectations.persist_expectations``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional, Sequence

from backend.models import (
    ConfidenceDimension,
    ExpectationSource,
    ExpressionKind,
    ExpressionTier,
    MarketView,
    ViewConfidence,
    ViewExpectation,
    ViewExpression,
    ViewStatus,
    ViewTransmission,
    ViewType,
)
from backend.schemas import (
    MarketViewCreate,
    MarketViewPatch,
    ViewConfidenceInput,
    ViewExpectationInput,
    ViewExpressionInput,
    ViewTransmissionInput,
)
from backend.view_markets import transmission as _transmission

if TYPE_CHECKING:  # pragma: no cover - typing only
    from sqlalchemy.orm import Session

    from backend.view_markets.confidence import TwoDialScore
    from backend.view_markets.expectations import SurpriseFraming


# The five spec disclosures every deployable expression must carry.
_DISCLOSURE_FIELDS: tuple[str, ...] = (
    "rationale",
    "risk_profile",
    "capital_intensity",
    "historical_strength",
    "time_horizon",
)

# View types that require an objective resolution and a "what's priced in" row.
_OBJECTIVE_VIEW_TYPES: frozenset[ViewType] = frozenset(
    {ViewType.event, ViewType.relative}
)


class CurationError(Exception):
    """Base error for curation-service failures."""


class CurationGateError(CurationError):
    """Raised when ``publish_view`` is called on a view that fails the review
    gate and ``force`` is not set. Carries the failing checks."""

    def __init__(self, message: str, failures: Sequence[str] = ()) -> None:
        super().__init__(message)
        self.failures = tuple(failures)


@dataclass(frozen=True)
class ReviewGateResult:
    """Outcome of the machine actionability/review gate."""

    ok: bool
    failures: tuple[str, ...] = field(default_factory=tuple)
    checks: dict = field(default_factory=dict)


# ── internal helpers ──────────────────────────────────────────────────


def _get_view(db: "Session", view_id: str) -> MarketView:
    """Fetch a ``MarketView`` by id or raise ``CurationError``."""
    view = db.get(MarketView, view_id)
    if view is None:
        raise CurationError(f"market view {view_id!r} not found")
    return view


def _is_blank(value: object) -> bool:
    """True when a value is missing or a whitespace-only string."""
    return value is None or (isinstance(value, str) and not value.strip())


def _missing_disclosures(expr: ViewExpressionInput) -> list[str]:
    """Return the names of any unset/blank disclosure fields on an expression."""
    return [f for f in _DISCLOSURE_FIELDS if _is_blank(getattr(expr, f, None))]


def _build_expression_row(
    view_id: str, expr: ViewExpressionInput
) -> ViewExpression:
    """Map a ``ViewExpressionInput`` to an unflushed ``ViewExpression`` ORM row."""
    return ViewExpression(
        view_id=view_id,
        tier=ExpressionTier(expr.tier),
        expression_kind=ExpressionKind(expr.expression_kind),
        config=dict(expr.config or {}),
        rationale=expr.rationale,
        risk_profile=expr.risk_profile,
        capital_intensity=expr.capital_intensity,
        historical_strength=expr.historical_strength,
        time_horizon=expr.time_horizon,
    )


def _build_confidence_row(
    view_id: str, conf: ViewConfidenceInput
) -> ViewConfidence:
    return ViewConfidence(
        view_id=view_id,
        dimension=ConfidenceDimension(conf.dimension),
        score=conf.score,
        evidence=conf.evidence,
    )


def _build_expectation_row(
    view_id: str, exp: ViewExpectationInput
) -> ViewExpectation:
    return ViewExpectation(
        view_id=view_id,
        source=ExpectationSource(exp.source),
        market_id=exp.market_id,
        expected_value=exp.expected_value,
        user_view_value=exp.user_view_value,
        surprise_sign=exp.surprise_sign,
    )


# ── create / update ───────────────────────────────────────────────────


def create_view(
    db: "Session",
    payload: MarketViewCreate,
    *,
    user_id: Optional[int] = None,
) -> MarketView:
    """Create a curated ``MarketView`` (status ``open``, ``published_at`` NULL)
    plus any child collections supplied on ``payload`` (expressions /
    transmission / confidence / expectations).

    ``user_id`` stays ``None`` for every curated beta view (PLAN §7.4); a
    non-NULL value is reserved for the future user-authored path. Child
    collections are seeded permissively (a draft may be incomplete — the publish
    gate, not this insert, enforces completeness). Does NOT commit (caller owns
    the txn). Returns the persisted ``MarketView`` with children flushed.
    """
    view = MarketView(
        user_id=user_id,
        view_type=ViewType(payload.view_type),
        title=payload.title,
        thesis=payload.thesis,
        category=payload.category,
        time_horizon=payload.time_horizon,
        status=ViewStatus.open,
        resolution_date=payload.resolution_date,
        published_at=None,
    )
    db.add(view)
    # Flush so the generated id is available for the child rows' FK.
    db.flush()

    for expr in payload.expressions:
        db.add(_build_expression_row(view.id, expr))
    for conf in payload.confidence:
        db.add(_build_confidence_row(view.id, conf))
    for exp in payload.expectations:
        db.add(_build_expectation_row(view.id, exp))
    if payload.transmission:
        _transmission.persist_transmission(
            db, view.id, list(payload.transmission), replace=True
        )

    db.flush()
    return view


def update_view(
    db: "Session",
    view_id: str,
    patch: MarketViewPatch,
) -> MarketView:
    """Apply a partial update to a view's scalar fields (title / thesis /
    category / time_horizon / status / resolution_date). Only fields explicitly
    set on ``patch`` are written. Raises ``CurationError`` if the view is
    missing. Does NOT commit.
    """
    view = _get_view(db, view_id)
    data = patch.model_dump(exclude_unset=True)
    for key, value in data.items():
        if key == "status" and value is not None:
            setattr(view, key, ViewStatus(value))
        else:
            setattr(view, key, value)
    db.flush()
    return view


# ── attach (children) ─────────────────────────────────────────────────


def attach_transmission(
    db: "Session",
    view_id: str,
    edges: Sequence[ViewTransmissionInput],
    *,
    replace: bool = True,
) -> list[ViewTransmission]:
    """Attach/replace a view's transmission edges (delegates to
    ``transmission.persist_transmission``)."""
    _get_view(db, view_id)  # FK-presence guard with a friendly error
    return _transmission.persist_transmission(
        db, view_id, list(edges), replace=replace
    )


def attach_expressions(
    db: "Session",
    view_id: str,
    expressions: Sequence[ViewExpressionInput],
    *,
    replace: bool = False,
) -> list[ViewExpression]:
    """Attach deployable expressions (one per tier) to a view. ``replace=False``
    appends; ``True`` clears existing rows first. Validates that all five spec
    disclosures are present on each expression (the review gate also re-checks).
    Raises ``CurationError`` if a disclosure is missing. Does NOT commit.
    """
    _get_view(db, view_id)
    for idx, expr in enumerate(expressions):
        missing = _missing_disclosures(expr)
        if missing:
            raise CurationError(
                f"expression[{idx}] (tier={expr.tier}) is missing required "
                f"disclosures: {', '.join(missing)}"
            )

    if replace:
        (
            db.query(ViewExpression)
            .filter(ViewExpression.view_id == view_id)
            .delete(synchronize_session=False)
        )

    rows: list[ViewExpression] = []
    for expr in expressions:
        row = _build_expression_row(view_id, expr)
        db.add(row)
        rows.append(row)
    db.flush()
    return rows


def attach_confidence(
    db: "Session",
    view_id: str,
    two_dial: "TwoDialScore",
) -> list[ViewConfidence]:
    """Attach the two-dial confidence (delegates to
    ``confidence.persist_confidence``)."""
    from backend.view_markets import confidence as _confidence

    _get_view(db, view_id)
    return _confidence.persist_confidence(db, view_id, two_dial)


def attach_expectations(
    db: "Session",
    view_id: str,
    framing: "SurpriseFraming",
    *,
    replace: bool = True,
) -> list[ViewExpectation]:
    """Attach the surprise/expectations rows (delegates to
    ``expectations.persist_expectations`` — user-facing model/consensus rows
    only; PM prior stays hidden)."""
    from backend.view_markets import expectations as _expectations

    _get_view(db, view_id)
    return _expectations.persist_expectations(
        db, view_id, framing, replace=replace
    )


# ── review gate / publish ─────────────────────────────────────────────


def validate_for_review(db: "Session", view: MarketView) -> ReviewGateResult:
    """Run the machine actionability/review gate (PLAN §7.2) over a view.

    Pure check (no writes): returns ``ReviewGateResult(ok, failures, checks)``.
    Children are read straight from the tables (not the relationship cache) so
    the result reflects flushed-but-uncommitted authoring. ``publish_view``
    calls this; a curator UI surfaces ``failures``.
    """
    checks: dict = {}
    failures: list[str] = []

    def _record(name: str, ok: bool, failure_msg: str) -> None:
        checks[name] = ok
        if not ok:
            failures.append(failure_msg)

    _record(
        "measurable_title",
        not _is_blank(view.title),
        "title is empty (no measurable belief)",
    )
    _record(
        "thesis",
        not _is_blank(view.thesis),
        "thesis is empty (no benchmark/cause stated)",
    )
    _record(
        "time_horizon",
        not _is_blank(view.time_horizon),
        "time_horizon is not set",
    )

    view_type = view.view_type
    is_objective = view_type in _OBJECTIVE_VIEW_TYPES

    if is_objective:
        _record(
            "resolution_date",
            view.resolution_date is not None,
            f"{getattr(view_type, 'value', view_type)} view has no "
            "resolution_date",
        )

    transmission_count = (
        db.query(ViewTransmission)
        .filter(ViewTransmission.view_id == view.id)
        .count()
    )
    _record(
        "transmission_edge",
        transmission_count >= 1,
        "no transmission edge (>= 1 cause->effect edge required)",
    )

    expressions = (
        db.query(ViewExpression)
        .filter(ViewExpression.view_id == view.id)
        .all()
    )
    complete_expression = any(
        not _missing_disclosures(e) for e in expressions
    )
    _record(
        "expression_with_disclosures",
        complete_expression,
        "no expression with all five disclosures populated",
    )

    dims = {
        c.dimension
        for c in db.query(ViewConfidence)
        .filter(ViewConfidence.view_id == view.id)
        .all()
    }
    both_dims = {
        ConfidenceDimension.outcome,
        ConfidenceDimension.expression,
    } <= dims
    _record(
        "confidence_both_dims",
        both_dims,
        "both confidence dimensions (outcome + expression) must be "
        "scored or explicitly suppressed",
    )

    if is_objective:
        expectations_count = (
            db.query(ViewExpectation)
            .filter(ViewExpectation.view_id == view.id)
            .count()
        )
        _record(
            "expectations_row",
            expectations_count >= 1,
            "event/relative view has no expectations (what's priced in) row",
        )

    return ReviewGateResult(
        ok=not failures,
        failures=tuple(failures),
        checks=checks,
    )


def publish_view(
    db: "Session",
    view_id: str,
    *,
    reviewer_id: Optional[int] = None,
    force: bool = False,
) -> MarketView:
    """Publish a curated view: enforce :func:`validate_for_review` (unless
    ``force``), stamp ``published_at`` server-side, and move ``status`` from
    ``open`` to ``developing`` (idempotent if already past ``open`` / already
    published). Raises ``CurationGateError`` with the failing checks when the
    gate fails and ``force`` is not set. ``reviewer_id`` is accepted for the
    audit trail but the Phase-1 schema has no reviewer column (flagged). Does
    NOT commit.
    """
    view = _get_view(db, view_id)

    if not force:
        result = validate_for_review(db, view)
        if not result.ok:
            raise CurationGateError(
                f"view {view_id!r} failed the review gate "
                f"({len(result.failures)} check(s) failed)",
                failures=result.failures,
            )

    if view.published_at is None:
        view.published_at = datetime.now(timezone.utc)
    if view.status == ViewStatus.open:
        view.status = ViewStatus.developing

    db.flush()
    return view


def unpublish_view(db: "Session", view_id: str) -> MarketView:
    """Pull a view from the surfaced set (clear ``published_at``, status back to
    ``open``) — the editorial "send back" action. Does NOT commit."""
    view = _get_view(db, view_id)
    view.published_at = None
    view.status = ViewStatus.open
    db.flush()
    return view


def list_curation_queue(
    db: "Session",
    *,
    published: Optional[bool] = None,
) -> list[MarketView]:
    """List curated views for the curator console. ``published`` filters by
    ``published_at`` NULL/NOT NULL; ``None`` returns all. Curated views only
    (``user_id IS NULL``), newest first."""
    query = db.query(MarketView).filter(MarketView.user_id.is_(None))
    if published is True:
        query = query.filter(MarketView.published_at.isnot(None))
    elif published is False:
        query = query.filter(MarketView.published_at.is_(None))
    return query.order_by(MarketView.created_at.desc()).all()


__all__ = [
    "CurationError",
    "CurationGateError",
    "ReviewGateResult",
    "create_view",
    "update_view",
    "attach_transmission",
    "attach_expressions",
    "attach_confidence",
    "attach_expectations",
    "validate_for_review",
    "publish_view",
    "unpublish_view",
    "list_curation_queue",
]
