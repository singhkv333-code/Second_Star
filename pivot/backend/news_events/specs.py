"""Spec lifecycle helpers — create / disambiguate / activate / cancel.

State machine:

  draft ←→ pending_disambiguation
    │            │
    ├────────────┘
    ▼
  active ──► fired   (Phase 5)
          ├─► expired (Phase 5/6)
          └─► cancelled

Phase 4 ships the transitions on the top half of the diagram. The
``fired`` / ``expired`` paths land with the aggregator in Phase 5.

All helpers are sync — they take a Session, do their work, and let
the caller commit. The router opens one Session per request via
``Depends(get_db)``, calls these helpers, and commits at the end of
the request.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy.orm import Session

from backend.news_events.models import (
    NewsDisambiguationSession,
    NewsEventSpec,
)
from backend.news_events.parsing.disambiguation import (
    apply_answers,
    apply_option,
    parsed_spec_to_pending_dict,
    questions_for,
)
from backend.news_events.parsing.event_spec_parser import ParsedSpec

logger = logging.getLogger(__name__)


# A disambiguation session expires after 30 min. Long enough for a
# user to think; short enough that abandoned sessions don't pile up.
_DISAMB_SESSION_TTL_MIN: int = 30


class SpecError(Exception):
    """Surface for router-side error reporting. Carries an HTTP
    status hint so the router maps cleanly to a status code."""

    def __init__(self, message: str, *, status: int = 422):
        super().__init__(message)
        self.status = status


# ── Spec creation ────────────────────────────────────────────────────


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _serialize_questions(questions) -> list[dict[str, Any]]:
    return [q.model_dump() for q in questions]


def create_spec_from_parsed(
    db: Session,
    *,
    user_id: int,
    parsed: ParsedSpec,
    workflow_id: Optional[str] = None,
) -> tuple[NewsEventSpec, Optional[NewsDisambiguationSession]]:
    """Persist a new spec row.

    Tier 1 / Tier 2 → state ``draft``; the user reviews then
    activates.

    Tier 3 → state ``pending_disambiguation`` and a companion
    ``NewsDisambiguationSession`` row holding the questions. The
    user answers via ``record_answer``; once all answers are
    in, ``finalize_disambiguation`` flips the state to ``draft``.
    """
    pending = parsed_spec_to_pending_dict(parsed)
    state = (
        "pending_disambiguation" if parsed.needs_disambiguation else "draft"
    )

    spec = NewsEventSpec(
        user_id=user_id,
        workflow_id=workflow_id,
        tier=parsed.tier,
        description=pending["description"],
        resolution_criteria=pending["resolution_criteria"],
        retraction_policy=pending["retraction_policy"],
        keyword_set=pending["keyword_set"],
        state=state,
    )
    db.add(spec)
    db.flush()

    if not parsed.needs_disambiguation:
        return spec, None

    questions = questions_for(parsed.tier)
    session = NewsDisambiguationSession(
        user_id=user_id,
        conversation_id=None,
        pending_event_spec=pending,
        questions=_serialize_questions(questions),
        answers={},
        state="open",
        expires_at=_now() + timedelta(minutes=_DISAMB_SESSION_TTL_MIN),
    )
    # Soft FK — store the spec id inside the pending payload so we
    # can navigate either direction without a DB constraint.
    session.pending_event_spec = {**pending, "_spec_id": spec.id}
    db.add(session)
    db.flush()
    logger.info(
        "[news_events.specs] disambiguation_opened spec_id=%s session_id=%s",
        spec.id,
        session.id,
    )
    return spec, session


# ── Disambiguation answer ────────────────────────────────────────────


def _load_active_session(
    db: Session,
    *,
    spec_id: str,
    user_id: int,
) -> NewsDisambiguationSession:
    spec = db.query(NewsEventSpec).filter(NewsEventSpec.id == spec_id).first()
    if spec is None or spec.user_id != user_id:
        raise SpecError("spec not found", status=404)
    if spec.state != "pending_disambiguation":
        raise SpecError(
            f"spec is in state {spec.state!r}, not pending_disambiguation",
            status=409,
        )
    session = (
        db.query(NewsDisambiguationSession)
        .filter(
            NewsDisambiguationSession.user_id == user_id,
            NewsDisambiguationSession.state == "open",
        )
        .order_by(NewsDisambiguationSession.created_at.desc())
        .all()
    )
    # Match the session by spec_id in the pending payload.
    for s in session:
        if (s.pending_event_spec or {}).get("_spec_id") == spec_id:
            return s
    raise SpecError("no open disambiguation session for this spec", status=409)


def record_answer(
    db: Session,
    *,
    spec_id: str,
    user_id: int,
    question_id: str,
    option_id: str,
) -> tuple[NewsEventSpec, NewsDisambiguationSession]:
    """Record one (question, option) pair on the session.

    When the answer covers the final question, the session state
    flips to ``completed``, the pending spec is updated with the
    accumulated answers, and the spec state flips to ``draft`` so
    the user can ``activate`` it.
    """
    session = _load_active_session(db, spec_id=spec_id, user_id=user_id)
    # SQLite returns naive datetimes; Postgres returns UTC-aware.
    # Normalize the column-side value before comparing.
    exp = session.expires_at
    if exp is not None and exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    if exp is not None and exp < _now():
        session.state = "expired"
        db.flush()
        raise SpecError("disambiguation session expired", status=410)

    questions = [
        {"id": q["id"], "text": q["text"], "options": q["options"]}
        for q in (session.questions or [])
    ]
    matching = next((q for q in questions if q["id"] == question_id), None)
    if matching is None:
        raise SpecError(f"unknown question_id {question_id!r}", status=422)
    option_ids = {o["id"] for o in matching["options"]}
    if option_id not in option_ids:
        raise SpecError(
            f"option {option_id!r} not in question {question_id!r}",
            status=422,
        )

    answers = dict(session.answers or {})
    answers[question_id] = option_id
    session.answers = answers
    db.flush()

    spec = db.query(NewsEventSpec).filter(NewsEventSpec.id == spec_id).one()

    if all(q["id"] in answers for q in questions):
        # All answered — finalize.
        from backend.news_events.parsing.disambiguation import (
            DisambiguationQuestion,
            DisambiguationOption,
        )

        question_objs = [
            DisambiguationQuestion(
                id=q["id"],
                text=q["text"],
                options=[DisambiguationOption(**o) for o in q["options"]],
            )
            for q in questions
        ]
        pending = dict(session.pending_event_spec or {})
        pending.pop("_spec_id", None)
        final = apply_answers(pending, answers=answers, questions=question_objs)
        spec.description = final["description"]
        spec.keyword_set = final["keyword_set"]
        spec.resolution_criteria = final["resolution_criteria"]
        spec.retraction_policy = final["retraction_policy"]
        spec.state = "draft"
        session.state = "completed"
        db.flush()
        logger.info(
            "[news_events.specs] disambiguation_completed spec_id=%s",
            spec_id,
        )
    return spec, session


# ── State transitions ────────────────────────────────────────────────


def activate_spec(
    db: Session, *, spec_id: str, user_id: int
) -> NewsEventSpec:
    spec = db.query(NewsEventSpec).filter(NewsEventSpec.id == spec_id).first()
    if spec is None or spec.user_id != user_id:
        raise SpecError("spec not found", status=404)
    if spec.state == "active":
        return spec  # idempotent
    if spec.state != "draft":
        raise SpecError(
            f"cannot activate from state {spec.state!r}",
            status=409,
        )
    spec.state = "active"
    db.flush()
    logger.info("[news_events.specs] activated spec_id=%s", spec_id)
    return spec


def cancel_spec(
    db: Session, *, spec_id: str, user_id: int
) -> NewsEventSpec:
    spec = db.query(NewsEventSpec).filter(NewsEventSpec.id == spec_id).first()
    if spec is None or spec.user_id != user_id:
        raise SpecError("spec not found", status=404)
    if spec.state in {"cancelled", "fired", "expired"}:
        return spec  # idempotent / terminal
    spec.state = "cancelled"
    db.flush()
    logger.info("[news_events.specs] cancelled spec_id=%s", spec_id)
    return spec


# ── Reads ────────────────────────────────────────────────────────────


def list_user_specs(
    db: Session, *, user_id: int, limit: int = 100
) -> list[NewsEventSpec]:
    return (
        db.query(NewsEventSpec)
        .filter(NewsEventSpec.user_id == user_id)
        .order_by(NewsEventSpec.created_at.desc())
        .limit(limit)
        .all()
    )


def get_user_spec(
    db: Session, *, spec_id: str, user_id: int
) -> NewsEventSpec:
    spec = db.query(NewsEventSpec).filter(NewsEventSpec.id == spec_id).first()
    if spec is None or spec.user_id != user_id:
        raise SpecError("spec not found", status=404)
    return spec


def get_session_for_spec(
    db: Session, *, spec_id: str, user_id: int
) -> Optional[NewsDisambiguationSession]:
    """Find the open / completed disambiguation session for this spec.
    Returns None if no session exists (Tier 1/2 case)."""
    sessions = (
        db.query(NewsDisambiguationSession)
        .filter(NewsDisambiguationSession.user_id == user_id)
        .order_by(NewsDisambiguationSession.created_at.desc())
        .all()
    )
    for s in sessions:
        if (s.pending_event_spec or {}).get("_spec_id") == spec_id:
            return s
    return None
