import enum
import uuid as _uuid
from datetime import datetime as _datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Enum as SQLEnum,
    Float,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from backend.database import Base


def _uuid_str() -> str:
    """Cross-dialect UUID v4 string default. Postgres-compatible (TEXT/UUID),
    SQLite-compatible (TEXT). All workflow tables use 36-char string PKs."""
    return str(_uuid.uuid4())


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), unique=True, index=True, nullable=False)
    hashed_password = Column(String(255), nullable=False)
    full_name = Column(String(255), nullable=True)
    phone = Column(String(15), nullable=True)
    is_active = Column(Boolean, default=True)
    is_verified = Column(Boolean, default=False)
    tax_slab = Column(Float, default=0.30)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    kite_session = relationship("KiteSession", back_populates="user", uselist=False)
    strategies = relationship("Strategy", back_populates="user")
    sip_schedules = relationship("SIPSchedule", back_populates="user")
    product_positions = relationship("ProductPosition", back_populates="user")
    trade_logs = relationship("TradeLog", back_populates="user")


class WatchlistItem(Base):
    """A symbol the user is watching. Backs `action.update_watchlist`
    in the Agent System and a future per-user watchlist UI surface.

    Plain table on purpose (vs. a JSON array on User) so we get UNIQUE
    enforcement at the DB layer (a user can't accidentally have INFY
    twice) and so future fields (added_at, notes, sort_order) drop in
    without a JSON migration.
    """
    __tablename__ = "watchlist_items"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    symbol = Column(String(64), nullable=False)
    exchange = Column(String(8), nullable=False, default="NSE")
    added_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint(
            "user_id", "symbol", "exchange",
            name="uq_watchlist_items_user_symbol_exchange",
        ),
    )


class KiteSession(Base):
    __tablename__ = "kite_sessions"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), unique=True, nullable=False)
    access_token = Column(String(500), nullable=False)
    request_token = Column(String(500), nullable=True)
    kite_user_id = Column(String(50), nullable=True)
    login_time = Column(DateTime(timezone=True), nullable=True)
    token_expires_at = Column(DateTime(timezone=True), nullable=True)
    totp_secret = Column(String(100), nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    user = relationship("User", back_populates="kite_session")


class StrategyStatus(str, enum.Enum):
    active = "active"
    paused = "paused"
    completed = "completed"
    failed = "failed"


class Strategy(Base):
    __tablename__ = "strategies"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    strategy_type = Column(String(50), nullable=False)
    trigger_symbol = Column(String(50), nullable=True)
    trigger_condition = Column(Text, nullable=True)
    action_config = Column(Text, nullable=True)
    max_budget = Column(Float, nullable=True)
    status = Column(SQLEnum(StrategyStatus), default=StrategyStatus.active)
    last_triggered_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    user = relationship("User", back_populates="strategies")


class SIPSchedule(Base):
    __tablename__ = "sip_schedules"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    name = Column(String(255), nullable=False)
    symbol = Column(String(50), nullable=False)
    instrument_type = Column(String(20), nullable=False)
    amount = Column(Float, nullable=False)
    frequency = Column(String(20), nullable=False)
    day_of_month = Column(Integer, nullable=True)
    day_of_week = Column(Integer, nullable=True)
    next_execution_at = Column(DateTime(timezone=True), nullable=True)
    is_active = Column(Boolean, default=True)
    total_invested = Column(Float, default=0.0)
    total_units_bought = Column(Float, default=0.0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    user = relationship("User", back_populates="sip_schedules")


class ProductType(str, enum.Enum):
    safegrow = "safegrow"
    earnmore = "earnmore"
    stormshield = "stormshield"
    smartexit = "smartexit"
    ratebet = "ratebet"
    barbell = "barbell"
    warbasket = "warbasket"


class ProductPosition(Base):
    __tablename__ = "product_positions"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    product_type = Column(SQLEnum(ProductType), nullable=False)
    display_name = Column(String(255), nullable=False)
    capital_deployed = Column(Float, nullable=False)
    horizon_days = Column(Integer, nullable=False)
    maturity_date = Column(DateTime(timezone=True), nullable=True)
    safety_leg_amount = Column(Float, nullable=True)
    growth_leg_amount = Column(Float, nullable=True)
    arb_yield_at_entry = Column(Float, nullable=True)
    status = Column(String(20), default="active")
    exit_value = Column(Float, nullable=True)
    ai_explanation = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    user = relationship("User", back_populates="product_positions")
    legs = relationship("ProductLeg", back_populates="position")


class ProductLeg(Base):
    __tablename__ = "product_legs"

    id = Column(Integer, primary_key=True, index=True)
    position_id = Column(Integer, ForeignKey("product_positions.id"), nullable=False)
    leg_type = Column(String(50), nullable=False)
    instrument = Column(String(100), nullable=False)
    instrument_type = Column(String(50), nullable=False)
    amount_invested = Column(Float, nullable=False)
    units_quantity = Column(Float, nullable=True)
    entry_price = Column(Float, nullable=True)
    kite_order_id = Column(String(50), nullable=True)
    status = Column(String(20), default="active")
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    position = relationship("ProductPosition", back_populates="legs")


class TradeLog(Base):
    __tablename__ = "trade_logs"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    kite_order_id = Column(String(50), nullable=True, index=True)
    symbol = Column(String(50), nullable=False)
    exchange = Column(String(10), nullable=False)
    transaction_type = Column(String(10), nullable=False)
    order_type = Column(String(20), nullable=False)
    quantity = Column(Integer, nullable=False)
    price = Column(Float, nullable=True)
    trigger_price = Column(Float, nullable=True)
    status = Column(String(20), nullable=False)
    average_price = Column(Float, nullable=True)
    filled_quantity = Column(Integer, nullable=True)
    source = Column(String(50), nullable=True)
    source_id = Column(Integer, nullable=True)
    placed_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    user = relationship("User", back_populates="trade_logs")


# ─── Agent System (Workflows v1) ──────────────────────────────────────
#
# Schema mirrors docs/ARCHITECTURE.md §4 and the API contract in
# docs/API_CONTRACT.md §3-§4. Driver is sync SQLAlchemy 2.0 + psycopg2.
# Cross-dialect choices:
#   - String(36) for UUID PKs (Python-side default via _uuid_str), so the
#     in-memory SQLite test DB and Postgres production DB share schema.
#   - JSON column type: SQLAlchemy renders JSONB on Postgres and JSON on
#     SQLite; the migration file pins postgresql.JSONB explicitly for prod.
#   - SQLEnum(..., native_enum=False) becomes a CHECK constraint in SQLite
#     and a Postgres ENUM in the migration (which uses postgresql.ENUM
#     directly to get the proper PG type).


class WorkflowStatus(str, enum.Enum):
    draft = "draft"
    active = "active"
    paused = "paused"
    archived = "archived"


class RunStatus(str, enum.Enum):
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    cancelled = "cancelled"
    awaiting_approval = "awaiting_approval"


class StepStatus(str, enum.Enum):
    pending = "pending"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    skipped = "skipped"
    awaiting_approval = "awaiting_approval"


class Workflow(Base):
    __tablename__ = "workflows"

    id = Column(String(36), primary_key=True, default=_uuid_str)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    status = Column(
        SQLEnum(WorkflowStatus, name="workflow_status", native_enum=False),
        nullable=False,
        default=WorkflowStatus.draft,
    )
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    activated_at = Column(DateTime(timezone=True), nullable=True)
    last_run_at = Column(DateTime(timezone=True), nullable=True)
    next_run_at = Column(DateTime(timezone=True), nullable=True)
    version = Column(Integer, nullable=False, default=1)
    single_instance = Column(Boolean, nullable=False, default=True)

    steps = relationship(
        "WorkflowStep",
        back_populates="workflow",
        cascade="all, delete-orphan",
        order_by="WorkflowStep.step_index",
    )
    runs = relationship("WorkflowRun", back_populates="workflow")
    webhook_tokens = relationship(
        "WorkflowWebhookToken",
        back_populates="workflow",
        cascade="all, delete-orphan",
    )


class WorkflowStep(Base):
    __tablename__ = "workflow_steps"
    __table_args__ = (
        UniqueConstraint("workflow_id", "step_index", name="uq_workflow_step_index"),
    )

    id = Column(String(36), primary_key=True, default=_uuid_str)
    workflow_id = Column(
        String(36),
        ForeignKey("workflows.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    step_index = Column(Integer, nullable=False)
    step_type = Column(String(64), nullable=False)
    # config holds step-type-specific JSON; validated against the registry
    # JSON Schema at every API + engine boundary. NEVER store secrets here
    # (webhook tokens live in workflow_webhook_tokens).
    config = Column(JSON, nullable=False, default=dict)
    label = Column(String(255), nullable=True)

    workflow = relationship("Workflow", back_populates="steps")


class WorkflowRun(Base):
    __tablename__ = "workflow_runs"
    # CheckConstraint on triggered_by so direct ORM inserts can't bypass
    # the Pydantic Literal validation (per reviewer Day-1 audit). Values
    # mirror docs/ARCHITECTURE.md §4 and API_CONTRACT.md §11.
    __table_args__ = (
        CheckConstraint(
            "triggered_by IN ('schedule', 'manual', 'webhook', "
            "'price_alert', 'indicator_alert', 'event_alert')",
            name="ck_workflow_runs_triggered_by",
        ),
    )

    id = Column(String(36), primary_key=True, default=_uuid_str)
    workflow_id = Column(
        String(36),
        ForeignKey("workflows.id"),
        nullable=False,
        index=True,
    )
    workflow_version = Column(Integer, nullable=False)
    triggered_by = Column(String(32), nullable=False)
    started_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    status = Column(
        SQLEnum(RunStatus, name="run_status", native_enum=False),
        nullable=False,
        default=RunStatus.running,
    )
    halt_reason = Column(String(64), nullable=True)
    # context is the inter-step data bag, keyed by stringified step_index.
    context = Column(JSON, nullable=False, default=dict)
    error_message = Column(Text, nullable=True)

    workflow = relationship("Workflow", back_populates="runs")
    steps = relationship(
        "WorkflowRunStep",
        back_populates="run",
        cascade="all, delete-orphan",
        order_by="WorkflowRunStep.step_index",
    )
    approvals = relationship("WorkflowApproval", back_populates="run")


class WorkflowRunStep(Base):
    __tablename__ = "workflow_run_steps"

    id = Column(String(36), primary_key=True, default=_uuid_str)
    run_id = Column(
        String(36),
        ForeignKey("workflow_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    step_index = Column(Integer, nullable=False)
    step_type = Column(String(64), nullable=False)
    status = Column(
        SQLEnum(StepStatus, name="step_status", native_enum=False),
        nullable=False,
    )
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    output = Column(JSON, nullable=True)
    error_message = Column(Text, nullable=True)
    attempts = Column(Integer, nullable=False, default=1)

    run = relationship("WorkflowRun", back_populates="steps")


class WorkflowApproval(Base):
    __tablename__ = "workflow_approvals"

    id = Column(String(36), primary_key=True, default=_uuid_str)
    run_id = Column(
        String(36),
        ForeignKey("workflow_runs.id"),
        nullable=False,
        index=True,
    )
    step_index = Column(Integer, nullable=False)
    requested_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    decision = Column(String(16), nullable=True)  # 'approved' | 'rejected' | NULL
    decided_at = Column(DateTime(timezone=True), nullable=True)
    summary = Column(Text, nullable=False)

    run = relationship("WorkflowRun", back_populates="approvals")


class WorkflowWebhookToken(Base):
    __tablename__ = "workflow_webhook_tokens"

    # Token IS the primary key — see docs/ARCHITECTURE.md §4. URL-safe
    # random string. Stored separately from workflow_steps.config so
    # secrets never appear in step JSON.
    token = Column(String(64), primary_key=True)
    workflow_id = Column(
        String(36),
        ForeignKey("workflows.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    step_index = Column(Integer, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    workflow = relationship("Workflow", back_populates="webhook_tokens")


# ─── Chat persistence (Day 8 — backs /api/conversations) ──────────────
#
# Chat was previously stateless (rolling history sent in every request,
# Redis-only conv_id). The redesign's left sidebar needs a per-user
# conversation history rendered from `GET /api/conversations`, so we
# persist conversations + messages in Postgres.
#
# A conversation is auto-created on the first chat turn that doesn't
# carry a conversation_id. Subsequent turns under the same id append
# to its messages list.


class Conversation(Base):
    __tablename__ = "conversations"

    id = Column(String(36), primary_key=True, default=_uuid_str)
    user_id = Column(
        Integer, ForeignKey("users.id"), nullable=False, index=True,
    )
    title = Column(String(255), nullable=True)
    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    last_message_at = Column(DateTime(timezone=True), nullable=True)

    messages = relationship(
        "ConversationMessage",
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="ConversationMessage.created_at",
    )


class ConversationMessage(Base):
    __tablename__ = "conversation_messages"

    id = Column(String(36), primary_key=True, default=_uuid_str)
    conversation_id = Column(
        String(36),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role = Column(String(16), nullable=False)  # "user" | "assistant" | "tool"
    content = Column(Text, nullable=False, default="")
    # Tool calls + tool results carried as JSON when role != "user"/"assistant".
    tool_payload = Column(JSON, nullable=True)
    # Python-side default for microsecond precision in SQLite tests; in
    # Postgres prod this still resolves at row-creation time.
    created_at = Column(
        DateTime(timezone=True),
        default=_datetime.utcnow,
        server_default=func.now(),
        nullable=False,
    )

    conversation = relationship("Conversation", back_populates="messages")
