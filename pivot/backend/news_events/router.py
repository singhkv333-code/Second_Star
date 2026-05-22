"""FastAPI router for the news_events admin / metrics surface (Phase 1).

Mounted under ``/api/news-events`` from main.py *only* when
``settings.news_events_enabled`` is true. Auth: bearer JWT (same
``require_user`` dep the Agent System routers use). The user-level
spec-creation endpoints land in Phase 4; Phase 1 ships admin-only
endpoints so we can verify the firehose is healthy.

Endpoints:

  GET  /api/news-events/admin/sources
       List configured sources + last-fetch health rows.

  GET  /api/news-events/admin/metrics?window_hours=N
       Funnel metrics. In Phase 1 only "articles_ingested" is non-zero;
       the rest are 0 until Stages 1-7 land.

  POST /api/news-events/admin/sources/{source_id}/poll
       Force a one-shot poll of the given source. Returns the same
       summary the scheduled tick would have produced. Useful for
       smoke-testing in dev and recovering after a transient outage.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy import func as sql_func
from sqlalchemy.orm import Session

from backend.config import settings
from backend.database import get_db
from backend.news_events import specs as specs_mod
from backend.news_events.config import (
    get_source as get_source_def,
    list_sources,
)
from backend.news_events.models import (
    NewsArticle,
    NewsArticleClassification,
    NewsEventSpec,
    NewsFiredEvent,
    NewsSourceHealth,
)
from backend.news_events.parsing.disambiguation import (
    DisambiguationOption,
    DisambiguationQuestion,
)
from backend.news_events.parsing.event_spec_parser import (
    ParserError,
    parse_event_spec,
)
from backend.news_events.pipeline.ingest import (
    build_adapter,
    ingest_one_source,
    persist_pushed_items,
)
from backend.news_events.webhooks.miniflux import (
    parse_payload as parse_miniflux_payload,
    verify_signature as verify_miniflux_signature,
)
from backend.news_events.schemas import (
    CreateSpecResponse,
    DisambiguationAnswer,
    DisambiguationSessionView,
    DraftSpecRequest,
    EventSpecResponse,
    FiredClassificationView,
    FiredEventResponse,
    ForcePollResponse,
    FunnelMetricsResponse,
    ListSpecsResponse,
    SourceHealthResponse,
    SourceHealthRow,
)
from backend.routers._deps import require_user

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/news-events",
    tags=["NewsEvents"],
)


@router.get(
    "/admin/sources",
    response_model=SourceHealthResponse,
    summary="List configured sources + last-fetch health",
)
async def list_admin_sources(
    user_id: int = Depends(require_user),
    db: Session = Depends(get_db),
) -> SourceHealthResponse:
    """Returns one row per registered source. Joins the static
    registry definition (display name, feed URL, poll cadence) with
    the dynamic ``news_source_health`` row if it exists."""
    health_rows = {
        row.source_id: row
        for row in db.query(NewsSourceHealth).all()
    }

    out: list[SourceHealthRow] = []
    for src in list_sources():
        h = health_rows.get(src.source_id)
        out.append(
            SourceHealthRow(
                source_id=src.source_id,
                display_name=src.display_name,
                feed_url=src.feed_url,
                enabled=src.enabled,
                poll_interval_seconds=src.poll_interval_seconds,
                last_successful_fetch_at=getattr(h, "last_successful_fetch_at", None),
                last_error_at=getattr(h, "last_error_at", None),
                last_error_message=getattr(h, "last_error_message", None),
                consecutive_failures=int(getattr(h, "consecutive_failures", 0) or 0),
                articles_seen_24h=int(getattr(h, "articles_seen_24h", 0) or 0),
                articles_passed_24h=int(getattr(h, "articles_passed_24h", 0) or 0),
                updated_at=getattr(h, "updated_at", None),
            )
        )
    return SourceHealthResponse(sources=out)


@router.get(
    "/admin/metrics",
    response_model=FunnelMetricsResponse,
    summary="Funnel counters over a rolling window",
)
async def get_admin_metrics(
    window_hours: int = Query(default=24, ge=1, le=168),
    user_id: int = Depends(require_user),
    db: Session = Depends(get_db),
) -> FunnelMetricsResponse:
    """Funnel metrics.

    Phase 2 fills in the Stage 1 + Stage 2 counters. Stage 3-7
    counters stay 0 until the corresponding phases ship.

      - ``articles_ingested``    : every news_articles row fetched in the window
      - ``articles_deduped``     : rows where near_dup_of IS NULL
                                   (passed the Stage 1 cross-source dedup)
      - ``articles_after_keyword``: classifications.stage_2_passed=true
                                   for articles fetched in the window
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=window_hours)
    ingested = (
        db.query(sql_func.count(NewsArticle.id))
        .filter(NewsArticle.fetched_at >= cutoff)
        .scalar()
    ) or 0
    after_stage1 = (
        db.query(sql_func.count(NewsArticle.id))
        .filter(
            NewsArticle.fetched_at >= cutoff,
            NewsArticle.near_dup_of.is_(None),
        )
        .scalar()
    ) or 0
    after_stage2 = (
        db.query(sql_func.count(NewsArticleClassification.id))
        .join(NewsArticle, NewsArticle.id == NewsArticleClassification.article_id)
        .filter(
            NewsArticleClassification.stage_2_passed.is_(True),
            NewsArticle.fetched_at >= cutoff,
        )
        .scalar()
    ) or 0
    # Phase 3 counter: classifications that reached Stage 6 (any
    # verdict, including UNRELATED from the Stage-4 threshold gate
    # since those are still "we touched the LLM path enough to write
    # a verdict").
    sent_to_llm = (
        db.query(sql_func.count(NewsArticleClassification.id))
        .join(NewsArticle, NewsArticle.id == NewsArticleClassification.article_id)
        .filter(
            NewsArticleClassification.classifier_verdict.is_not(None),
            NewsArticle.fetched_at >= cutoff,
        )
        .scalar()
    ) or 0
    sources_with_recent = (
        db.query(NewsSourceHealth.source_id)
        .filter(NewsSourceHealth.last_successful_fetch_at >= cutoff)
        .count()
    )
    # Phase 5 counter — events that fired inside the window.
    events_fired = (
        db.query(sql_func.count(NewsFiredEvent.id))
        .filter(NewsFiredEvent.fired_at >= cutoff)
        .scalar()
    ) or 0

    return FunnelMetricsResponse(
        window_hours=window_hours,
        sources_active=int(sources_with_recent),
        articles_ingested=int(ingested),
        articles_deduped=int(after_stage1),
        articles_after_keyword=int(after_stage2),
        articles_sent_to_llm=int(sent_to_llm),
        events_fired=int(events_fired),
    )


@router.post(
    "/admin/sources/{source_id}/poll",
    response_model=ForcePollResponse,
    summary="Force a one-shot poll of a single source",
)
async def force_poll(
    source_id: str = Path(..., min_length=1, max_length=64),
    user_id: int = Depends(require_user),
    db: Session = Depends(get_db),
) -> ForcePollResponse:
    """Run the same ingest path the scheduler would, immediately, and
    return its summary. 404 if the source isn't in the registry."""
    src = get_source_def(source_id)
    if src is None:
        raise HTTPException(status_code=404, detail="source not registered")
    fetched_at = datetime.now(timezone.utc)
    if not src.enabled:
        return ForcePollResponse(
            source_id=source_id,
            status="skipped_disabled",
            fetched_at=fetched_at,
        )

    adapter = build_adapter(src)
    result = await ingest_one_source(adapter, db_session=db)
    return ForcePollResponse(
        source_id=source_id,
        status="ok" if result.ok else "error",
        articles_seen=result.items_seen,
        articles_new=result.items_new,
        articles_after_stage1=result.items_after_stage1,
        articles_after_stage2=result.items_after_stage2,
        error=result.error,
        fetched_at=result.fetched_at,
    )


# ── Phase 4 — spec lifecycle endpoints ───────────────────────────────


def _spec_to_response(spec: NewsEventSpec) -> EventSpecResponse:
    """Coerce an ORM row into the public response DTO. Stays in the
    router so the model layer doesn't need a hand-rolled serializer."""
    return EventSpecResponse(
        id=spec.id,
        user_id=int(spec.user_id),
        workflow_id=spec.workflow_id,
        tier=spec.tier,  # type: ignore[arg-type]
        description=spec.description,
        resolution_criteria=spec.resolution_criteria or {},
        retraction_policy=spec.retraction_policy or {},
        keyword_set=spec.keyword_set or {},
        deadline_at=spec.deadline_at,
        watch_window_start_at=spec.watch_window_start_at,
        state=spec.state,  # type: ignore[arg-type]
        created_at=spec.created_at,
        updated_at=spec.updated_at,
    )


def _session_to_view(session, *, spec_id: str) -> DisambiguationSessionView:
    """Coerce a NewsDisambiguationSession ORM row into the FE view."""
    questions = [
        DisambiguationQuestion(
            id=q["id"],
            text=q["text"],
            options=[DisambiguationOption(**o) for o in q["options"]],
        )
        for q in (session.questions or [])
    ]
    return DisambiguationSessionView(
        session_id=session.id,
        spec_id=spec_id,
        questions=questions,
        answers=dict(session.answers or {}),
        expires_at=session.expires_at,
    )


def _spec_error_to_http(exc: specs_mod.SpecError) -> HTTPException:
    """Map a SpecError onto the canonical error envelope. Matches the
    style used elsewhere in the backend — see backend/main.py."""
    return HTTPException(status_code=exc.status, detail=str(exc))


@router.post(
    "/specs",
    response_model=CreateSpecResponse,
    summary="Parse a natural-language event automation into a spec",
)
async def create_spec(
    body: DraftSpecRequest,
    user_id: int = Depends(require_user),
    db: Session = Depends(get_db),
) -> CreateSpecResponse:
    """Tier 1/2 → returns the new spec in state ``draft``. Tier 3 →
    returns a disambiguation session (the spec exists in state
    ``pending_disambiguation`` until the user answers)."""
    try:
        parsed = await parse_event_spec(body.text)
    except ParserError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    spec, session = specs_mod.create_spec_from_parsed(
        db,
        user_id=user_id,
        parsed=parsed,
        workflow_id=body.workflow_id,
    )
    db.commit()
    db.refresh(spec)

    out_spec = _spec_to_response(spec) if session is None else None
    out_session = (
        _session_to_view(session, spec_id=spec.id) if session is not None else None
    )
    return CreateSpecResponse(
        spec=out_spec,
        disambiguation=out_session,
        warnings=list(parsed.warnings),
    )


@router.get(
    "/specs",
    response_model=ListSpecsResponse,
    summary="List the current user's event specs (newest first)",
)
async def list_specs(
    user_id: int = Depends(require_user),
    db: Session = Depends(get_db),
) -> ListSpecsResponse:
    rows = specs_mod.list_user_specs(db, user_id=user_id)
    return ListSpecsResponse(specs=[_spec_to_response(r) for r in rows])


@router.get(
    "/specs/{spec_id}",
    response_model=EventSpecResponse,
    summary="Fetch one spec",
)
async def get_spec(
    spec_id: str = Path(..., min_length=1, max_length=64),
    user_id: int = Depends(require_user),
    db: Session = Depends(get_db),
) -> EventSpecResponse:
    try:
        spec = specs_mod.get_user_spec(db, spec_id=spec_id, user_id=user_id)
    except specs_mod.SpecError as exc:
        raise _spec_error_to_http(exc)
    return _spec_to_response(spec)


@router.get(
    "/specs/{spec_id}/disambiguation",
    response_model=DisambiguationSessionView,
    summary="Fetch the open disambiguation session for a Tier-3 spec",
)
async def get_disambiguation(
    spec_id: str = Path(..., min_length=1, max_length=64),
    user_id: int = Depends(require_user),
    db: Session = Depends(get_db),
) -> DisambiguationSessionView:
    try:
        spec = specs_mod.get_user_spec(db, spec_id=spec_id, user_id=user_id)
    except specs_mod.SpecError as exc:
        raise _spec_error_to_http(exc)
    if spec.state != "pending_disambiguation":
        raise HTTPException(
            status_code=409,
            detail=f"spec is in state {spec.state!r}, not pending_disambiguation",
        )
    session = specs_mod.get_session_for_spec(
        db, spec_id=spec_id, user_id=user_id
    )
    if session is None:
        raise HTTPException(
            status_code=404,
            detail="no open disambiguation session for this spec",
        )
    return _session_to_view(session, spec_id=spec_id)


@router.post(
    "/specs/{spec_id}/disambiguate",
    response_model=CreateSpecResponse,
    summary="Record one disambiguation answer",
)
async def disambiguate(
    body: DisambiguationAnswer,
    spec_id: str = Path(..., min_length=1, max_length=64),
    user_id: int = Depends(require_user),
    db: Session = Depends(get_db),
) -> CreateSpecResponse:
    """Records the answer. If all questions are now answered, the
    spec flips to ``draft`` and the response carries the final
    spec view. Otherwise the response carries the updated session
    view so the FE can render the next question."""
    try:
        spec, session = specs_mod.record_answer(
            db,
            spec_id=spec_id,
            user_id=user_id,
            question_id=body.question_id,
            option_id=body.option_id,
        )
    except specs_mod.SpecError as exc:
        raise _spec_error_to_http(exc)
    db.commit()
    db.refresh(spec)
    db.refresh(session)

    if spec.state == "draft":
        return CreateSpecResponse(spec=_spec_to_response(spec))
    return CreateSpecResponse(
        disambiguation=_session_to_view(session, spec_id=spec_id),
    )


@router.post(
    "/specs/{spec_id}/activate",
    response_model=EventSpecResponse,
    summary="Activate a draft spec (start watching)",
)
async def activate(
    spec_id: str = Path(..., min_length=1, max_length=64),
    user_id: int = Depends(require_user),
    db: Session = Depends(get_db),
) -> EventSpecResponse:
    try:
        spec = specs_mod.activate_spec(db, spec_id=spec_id, user_id=user_id)
    except specs_mod.SpecError as exc:
        raise _spec_error_to_http(exc)
    db.commit()
    db.refresh(spec)
    return _spec_to_response(spec)


@router.post(
    "/specs/{spec_id}/cancel",
    response_model=EventSpecResponse,
    summary="Cancel a spec (any non-terminal state)",
)
async def cancel(
    spec_id: str = Path(..., min_length=1, max_length=64),
    user_id: int = Depends(require_user),
    db: Session = Depends(get_db),
) -> EventSpecResponse:
    try:
        spec = specs_mod.cancel_spec(db, spec_id=spec_id, user_id=user_id)
    except specs_mod.SpecError as exc:
        raise _spec_error_to_http(exc)
    db.commit()
    db.refresh(spec)
    return _spec_to_response(spec)


@router.get(
    "/fired/{fired_id}",
    response_model=FiredEventResponse,
    summary="Audit trail for a fired event",
)
async def get_fired_event(
    fired_id: str = Path(..., min_length=1, max_length=64),
    user_id: int = Depends(require_user),
    db: Session = Depends(get_db),
) -> FiredEventResponse:
    """The 'why we fired' view. Joins news_fired_events →
    news_event_specs → news_article_classifications → news_articles
    so the FE can render a single coherent audit pane.
    """
    fired = (
        db.query(NewsFiredEvent)
        .filter(NewsFiredEvent.id == fired_id)
        .first()
    )
    if fired is None:
        raise HTTPException(status_code=404, detail="fired event not found")
    spec = (
        db.query(NewsEventSpec)
        .filter(NewsEventSpec.id == fired.event_spec_id)
        .first()
    )
    if spec is None or spec.user_id != user_id:
        raise HTTPException(status_code=404, detail="fired event not found")

    # Join the supporting classifications + article metadata.
    supporting_ids = list(fired.supporting_classification_ids or [])
    rows: list[FiredClassificationView] = []
    if supporting_ids:
        joined = (
            db.query(NewsArticleClassification, NewsArticle)
            .join(
                NewsArticle,
                NewsArticle.id == NewsArticleClassification.article_id,
            )
            .filter(NewsArticleClassification.id.in_(supporting_ids))
            .all()
        )
        for cls, art in joined:
            rows.append(
                FiredClassificationView(
                    classification_id=cls.id,
                    article_id=art.id,
                    article_title=art.title,
                    article_url=art.url,
                    source_id=art.source_id,
                    classifier_verdict=cls.classifier_verdict,
                    confidence=cls.confidence,
                    excerpt=cls.excerpt,
                    embedding_similarity=cls.embedding_similarity,
                )
            )

    return FiredEventResponse(
        id=fired.id,
        event_spec_id=spec.id,
        spec_description=spec.description,
        spec_tier=spec.tier,  # type: ignore[arg-type]
        workflow_run_id=fired.workflow_run_id,
        fired_at=fired.fired_at,
        aggregated_confidence=float(fired.aggregated_confidence),
        retraction_window_ends_at=fired.retraction_window_ends_at,
        retraction_status=fired.retraction_status,  # type: ignore[arg-type]
        retraction_detected_at=fired.retraction_detected_at,
        retraction_classification_id=fired.retraction_classification_id,
        retraction_action_taken=fired.retraction_action_taken,
        prediction_market_snapshot=fired.prediction_market_snapshot,
        supporting=rows,
    )


# ── Phase 7 — Miniflux HMAC webhook receiver ─────────────────────────


@router.post(
    "/webhook/miniflux",
    summary="Receive new entries from a self-hosted Miniflux instance",
    description=(
        "HMAC-SHA256 signed POST from Miniflux 2.0.48+. Configure the "
        "matching secret on the Miniflux side via WEBHOOK_SECRET and "
        "WEBHOOK_URL. Body is parsed and persisted through the same "
        "Stage-0 dedup + Stage-1 cross-source dedup + Stage-2 keyword "
        "filter the in-process poller uses. Returns 401 on signature "
        "failure, 200 with a count summary on success."
    ),
)
async def miniflux_webhook(
    request: Request,
    db: Session = Depends(get_db),
) -> JSONResponse:
    secret = settings.miniflux_webhook_secret
    if not secret:
        # Endpoint is wired but no secret is configured — refuse all
        # requests. Avoids accidentally accepting unsigned posts.
        raise HTTPException(
            status_code=401,
            detail="miniflux webhook receiver not configured",
        )

    raw_body = await request.body()
    sig_header = request.headers.get("x-miniflux-signature", "")
    if not verify_miniflux_signature(
        secret=secret, raw_body=raw_body, signature_header=sig_header
    ):
        raise HTTPException(status_code=401, detail="invalid signature")

    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="invalid JSON body")

    source_id, items = parse_miniflux_payload(payload)
    if not items:
        # Either non-new_entries event, or empty entries list — still 200
        # so Miniflux doesn't retry.
        return JSONResponse(
            content={
                "status": "ok",
                "source_id": source_id,
                "items_seen": 0,
                "items_new": 0,
            }
        )

    outcome = persist_pushed_items(db, source_id=source_id, items=items)
    db.commit()
    return JSONResponse(
        content={
            "status": "ok",
            "source_id": source_id,
            "items_seen": len(items),
            "items_new": outcome.new_count,
            "after_stage1": outcome.after_stage1,
            "after_stage2": outcome.after_stage2,
        }
    )
