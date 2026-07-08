"""Forward-test idea resolver — the race-proof attribution anchor (P6).

Every paper fill that should accrue to a durable, attributable forward
track record is stamped with a ``ForwardIdea.id``. This module owns the
SELECT-or-CREATE for that idea, keyed on the natural identity of its
origin:

    workflow   ->  (account_id, origin_kind="workflow",  workflow_id)
    strategy   ->  (account_id, origin_kind="strategy",  strategy_id)
    chat       ->  (account_id, origin_kind="chat",      conversation_id, label)
    manual / unknown -> idea_id stays NULL (return None)

Dedup is enforced HERE, not by a DB index (the P0 schema is permissive
so existing data doesn't need a backfill). To stay safe under the
concurrent scheduler we acquire a per-key Postgres transaction-scoped
advisory lock BEFORE the SELECT — on SQLite the test suite is single-
threaded so a SELECT-then-SAVEPOINT-INSERT-then-re-SELECT is sufficient
(and harmless on Postgres if the lock somehow missed).

Money discipline doesn't apply (no Numeric columns touched). Session
discipline: ``flush()`` only — the broker / engine / router owns commit.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.models import ForwardIdea

# Origin kinds that anchor a forward-test idea. Anything else (the
# manual placeholder, an unknown / None producer) returns None so the
# caller leaves ``idea_id`` NULL on the fill — unattributed, but a
# legitimate paper-trading event nonetheless. ``strategy`` is supported
# by the resolver even though no live producer exists in v1 (per the
# build contract DECISIONS §2).
_VALID_ORIGINS = frozenset({"workflow", "chat", "strategy"})


def _natural_key(
    *,
    account_id: str,
    origin_kind: str,
    workflow_id: Optional[str],
    conversation_id: Optional[str],
    strategy_id: Optional[int],
    label: str,
) -> str:
    """Stable, human-readable natural-key string for the advisory lock
    and for debugging. Per-origin shape exactly mirrors the dedup
    filter so the lock granularity tracks the dedup granularity."""
    if origin_kind == "workflow":
        return f"forward_idea:{account_id}:workflow:{workflow_id or ''}"
    if origin_kind == "strategy":
        return f"forward_idea:{account_id}:strategy:{strategy_id or ''}"
    # chat
    return (
        f"forward_idea:{account_id}:chat:{conversation_id or ''}:{label}"
    )


def _query_existing(
    db: Session,
    *,
    account_id: str,
    origin_kind: str,
    workflow_id: Optional[str],
    conversation_id: Optional[str],
    strategy_id: Optional[int],
    label: str,
) -> Optional[ForwardIdea]:
    """The per-origin dedup SELECT. None on miss."""
    q = db.query(ForwardIdea).filter(
        ForwardIdea.account_id == account_id,
        ForwardIdea.origin_kind == origin_kind,
    )
    if origin_kind == "workflow":
        q = q.filter(ForwardIdea.workflow_id == workflow_id)
    elif origin_kind == "strategy":
        q = q.filter(ForwardIdea.strategy_id == strategy_id)
    else:  # chat
        q = q.filter(
            ForwardIdea.conversation_id == conversation_id,
            ForwardIdea.label == label,
        )
    return q.first()


def resolve_idea(
    db: Session,
    account_id: str,
    *,
    user_id: int,
    origin_kind: str,
    workflow_id: Optional[str] = None,
    conversation_id: Optional[str] = None,
    strategy_id: Optional[int] = None,
    label: Optional[str] = None,
    backtest_run_id: Optional[str] = None,
) -> Optional[ForwardIdea]:
    """Return the ForwardIdea this fill should attribute to (creating it
    on first touch), or ``None`` if the producer is manual / unknown.

    Race semantics
    --------------
    On Postgres we take a transaction-scoped advisory lock keyed on the
    natural-key hash BEFORE the SELECT. That serializes concurrent
    first-touches of the same idea inside their own transactions: the
    loser blocks on the lock until the winner commits, then its SELECT
    finds the winner's row. The advisory lock is released at COMMIT or
    ROLLBACK of the caller's transaction (``xact``).

    On SQLite (tests) the dialect check skips the advisory call. We
    still wrap the INSERT in a SAVEPOINT so a hypothetical lost race
    cleanly rolls back only the failed INSERT (not the caller's
    surrounding work) and the post-IntegrityError re-SELECT returns
    the winner.

    The resolver ``flush()`` es so the new id is materialised and
    available to ``order.idea_id = idea.id`` in the same transaction.
    It never ``commit()`` s — that's the caller's job.
    """
    if origin_kind not in _VALID_ORIGINS:
        return None

    # The dedup key for each origin MUST be present, else genuinely
    # unrelated orders collapse into ONE idea (e.g. every chat order with a
    # missing conversation_id would share the (account, chat, None, symbol)
    # key and splice their NAV series). When the key is absent, leave the
    # order unattributed (idea_id stays NULL) rather than mis-merge.
    if origin_kind == "workflow" and not workflow_id:
        return None
    if origin_kind == "chat" and not conversation_id:
        return None
    if origin_kind == "strategy" and not strategy_id:
        return None

    # Label is NOT NULL on the model. Callers SHOULD pass a meaningful
    # label (workflow name, "BUY RELIANCE", etc.) — fall back to a
    # generic synthetic so a misuse doesn't crash on the FK / constraint.
    resolved_label = label if label else f"{origin_kind} idea"

    natural_key = _natural_key(
        account_id=account_id,
        origin_kind=origin_kind,
        workflow_id=workflow_id,
        conversation_id=conversation_id,
        strategy_id=strategy_id,
        label=resolved_label,
    )

    # Dialect-guarded advisory lock. ``hashtext`` exists on Postgres
    # only; we never call this on SQLite. The lock is transaction-
    # scoped (pg_advisory_xact_lock) so it releases automatically when
    # the caller's xact ends.
    bind = db.get_bind()
    if bind is not None and bind.dialect.name == "postgresql":
        db.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:k))"),
            {"k": natural_key},
        )

    existing = _query_existing(
        db,
        account_id=account_id,
        origin_kind=origin_kind,
        workflow_id=workflow_id,
        conversation_id=conversation_id,
        strategy_id=strategy_id,
        label=resolved_label,
    )
    if existing is not None:
        return existing

    idea = ForwardIdea(
        user_id=int(user_id),
        account_id=account_id,
        origin_kind=origin_kind,
        workflow_id=workflow_id if origin_kind == "workflow" else None,
        conversation_id=(
            conversation_id if origin_kind == "chat" else None
        ),
        strategy_id=strategy_id if origin_kind == "strategy" else None,
        label=resolved_label,
        inception_date=None,
        status="paper",
        cohort_trial_count=1,
        backtest_run_id=backtest_run_id,
    )
    try:
        with db.begin_nested():
            db.add(idea)
            db.flush()
    except IntegrityError:
        # Lost the create race (or hit some other constraint). Re-SELECT
        # the winner; if there genuinely isn't one, re-raise.
        winner = _query_existing(
            db,
            account_id=account_id,
            origin_kind=origin_kind,
            workflow_id=workflow_id,
            conversation_id=conversation_id,
            strategy_id=strategy_id,
            label=resolved_label,
        )
        if winner is not None:
            return winner
        raise
    db.flush()
    return idea


__all__ = ["resolve_idea"]
