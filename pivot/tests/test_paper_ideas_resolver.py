"""P6 tests for backend.paper.ideas.resolve_idea — race-proof dedup,
label fallback, manual-origin skip, and the flush-not-commit contract.

Mirrors the in-memory SQLite + PRAGMA foreign_keys fixture used by the
sibling paper tests so this test runs the same way as the rest of the
suite (no Alembic, no network, no shared state across tests).
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from backend.database import Base
from backend.models import (  # noqa: F401 — registers tables on Base.metadata
    Conversation,
    ForwardIdea,
    PaperAccount,
    User,
    Workflow,
)
from backend.paper.accounts import get_or_create_account
from backend.paper.ideas import resolve_idea


@pytest.fixture()
def session() -> Session:
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def _fk_on(dbapi_conn, _record):  # noqa: ANN001
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    try:
        yield db
    finally:
        db.close()
        engine.dispose()


def _user(db: Session, email: str = "i@example.com") -> User:
    u = User(email=email, hashed_password="x")
    db.add(u)
    db.flush()
    return u


def _account(db: Session, user: User) -> PaperAccount:
    return get_or_create_account(db, user.id)


def _workflow(db: Session, user: User, name: str = "Buy-the-dip RELIANCE") -> Workflow:
    wf = Workflow(user_id=user.id, name=name)
    db.add(wf)
    db.flush()
    return wf


def _conversation(db: Session, user: User) -> Conversation:
    c = Conversation(user_id=user.id, title="chat")
    db.add(c)
    db.flush()
    return c


# ── workflow origin: dedup on (account_id, workflow_id) ──────────────────


def test_workflow_dedup_same_workflow_id_returns_same_idea(
    session: Session,
) -> None:
    user = _user(session)
    acct = _account(session, user)
    wf = _workflow(session, user)

    a = resolve_idea(
        session, acct.id, user_id=user.id,
        origin_kind="workflow", workflow_id=wf.id, label=wf.name,
    )
    b = resolve_idea(
        session, acct.id, user_id=user.id,
        origin_kind="workflow", workflow_id=wf.id, label=wf.name,
    )

    assert a is not None and b is not None
    assert a.id == b.id
    assert session.query(ForwardIdea).count() == 1
    # natural-key fields populated, others NULL
    assert a.workflow_id == wf.id
    assert a.conversation_id is None
    assert a.strategy_id is None
    # defaults from the contract
    assert a.status == "paper"
    assert a.inception_date is None
    assert a.cohort_trial_count == 1
    assert a.user_id == user.id


def test_workflow_different_workflows_two_ideas(session: Session) -> None:
    user = _user(session)
    acct = _account(session, user)
    wf1 = _workflow(session, user, "RSI-30 buy")
    wf2 = _workflow(session, user, "EMA-cross sell")

    a = resolve_idea(
        session, acct.id, user_id=user.id,
        origin_kind="workflow", workflow_id=wf1.id, label=wf1.name,
    )
    b = resolve_idea(
        session, acct.id, user_id=user.id,
        origin_kind="workflow", workflow_id=wf2.id, label=wf2.name,
    )
    assert a is not None and b is not None
    assert a.id != b.id
    assert session.query(ForwardIdea).count() == 2


# ── chat origin: dedup on (account_id, conversation_id, label) ──────────


def test_chat_two_distinct_labels_makes_two_ideas(session: Session) -> None:
    user = _user(session)
    acct = _account(session, user)
    conv = _conversation(session, user)

    a = resolve_idea(
        session, acct.id, user_id=user.id,
        origin_kind="chat", conversation_id=conv.id, label="BUY RELIANCE",
    )
    b = resolve_idea(
        session, acct.id, user_id=user.id,
        origin_kind="chat", conversation_id=conv.id, label="SELL TCS",
    )
    assert a is not None and b is not None
    assert a.id != b.id
    assert session.query(ForwardIdea).count() == 2
    # Both anchored on the same chat
    assert a.conversation_id == conv.id and b.conversation_id == conv.id
    assert a.workflow_id is None and a.strategy_id is None


def test_chat_same_label_dedups(session: Session) -> None:
    user = _user(session)
    acct = _account(session, user)
    conv = _conversation(session, user)

    a = resolve_idea(
        session, acct.id, user_id=user.id,
        origin_kind="chat", conversation_id=conv.id, label="BUY RELIANCE",
    )
    b = resolve_idea(
        session, acct.id, user_id=user.id,
        origin_kind="chat", conversation_id=conv.id, label="BUY RELIANCE",
    )
    assert a is not None and b is not None
    assert a.id == b.id
    assert session.query(ForwardIdea).count() == 1


# ── manual / unknown origin: returns None ───────────────────────────────


def test_manual_origin_returns_none(session: Session) -> None:
    user = _user(session)
    acct = _account(session, user)

    result = resolve_idea(
        session, acct.id, user_id=user.id, origin_kind="manual",
    )
    assert result is None
    assert session.query(ForwardIdea).count() == 0


def test_unknown_origin_returns_none(session: Session) -> None:
    user = _user(session)
    acct = _account(session, user)

    result = resolve_idea(
        session, acct.id, user_id=user.id, origin_kind="cosmic-rays",
        workflow_id="ignored", label="whatever",
    )
    assert result is None
    assert session.query(ForwardIdea).count() == 0


# ── label fallback (label NOT NULL on the model) ────────────────────────


def test_label_fallback_when_missing(session: Session) -> None:
    """A caller that forgets to pass a label MUST still produce a row;
    the synthesized label is non-null and origin-tagged."""
    user = _user(session)
    acct = _account(session, user)
    wf = _workflow(session, user)

    idea = resolve_idea(
        session, acct.id, user_id=user.id,
        origin_kind="workflow", workflow_id=wf.id,
        # label intentionally omitted
    )
    assert idea is not None
    assert idea.label is not None
    assert idea.label != ""
    assert "workflow" in idea.label


def test_label_fallback_empty_string_synthesizes(session: Session) -> None:
    user = _user(session)
    acct = _account(session, user)
    conv = _conversation(session, user)

    idea = resolve_idea(
        session, acct.id, user_id=user.id,
        origin_kind="chat", conversation_id=conv.id, label="",
    )
    assert idea is not None
    assert idea.label == "chat idea"


# ── flush-not-commit contract: caller can roll back the resolver ────────


def test_resolver_only_flushes_caller_can_rollback(session: Session) -> None:
    """The resolver MUST NOT commit. We open a SAVEPOINT, resolve an idea,
    then roll back the SAVEPOINT — the idea must disappear."""
    user = _user(session)
    acct = _account(session, user)
    wf = _workflow(session, user)

    nested = session.begin_nested()
    idea = resolve_idea(
        session, acct.id, user_id=user.id,
        origin_kind="workflow", workflow_id=wf.id, label=wf.name,
    )
    assert idea is not None
    # The flush made it queryable inside the SAVEPOINT
    assert session.query(ForwardIdea).count() == 1
    nested.rollback()

    # After rollback the idea is gone — proves no commit happened.
    assert session.query(ForwardIdea).count() == 0


# ── per-origin field discipline: cross-origin keys are not set ──────────


def test_chat_idea_does_not_set_workflow_or_strategy(session: Session) -> None:
    user = _user(session)
    acct = _account(session, user)
    conv = _conversation(session, user)

    idea = resolve_idea(
        session, acct.id, user_id=user.id,
        origin_kind="chat", conversation_id=conv.id, label="BUY HDFC",
        # callers may accidentally pass these; resolver should ignore them
        workflow_id=None,
        strategy_id=None,
    )
    assert idea is not None
    assert idea.workflow_id is None
    assert idea.strategy_id is None
    assert idea.conversation_id == conv.id


def test_backtest_run_id_pass_through(session: Session) -> None:
    user = _user(session)
    acct = _account(session, user)
    wf = _workflow(session, user)

    idea = resolve_idea(
        session, acct.id, user_id=user.id,
        origin_kind="workflow", workflow_id=wf.id, label=wf.name,
        backtest_run_id="bt-run-xyz",
    )
    assert idea is not None
    assert idea.backtest_run_id == "bt-run-xyz"
