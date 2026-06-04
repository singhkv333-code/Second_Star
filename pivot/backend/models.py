import enum
import uuid as _uuid
from datetime import datetime as _datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    Enum as SQLEnum,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
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
    paper_account = relationship(
        "PaperAccount", back_populates="user", uselist=False,
    )


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


# ─── Paper Trading & Forward-Testing ──────────────────────────────────
#
# A simulated broker accrues every triggered/registered order into a
# structured, evolving portfolio: cash ledger -> immutable fills ->
# positions -> daily NAV snapshots. Second aim: attribute each fill to
# the originating idea (a workflow, a chat turn, or a saved strategy)
# and snapshot per-idea NAV so each idea can be FORWARD-TESTED live and
# compared against its stored backtest.
#
# Conventions mirror the most recent additive table (dsl_backtest_runs,
# migration 0011) and the cross-dialect notes above: String(36) UUID PKs
# via _uuid_str; enum-like columns are String + CheckConstraint (NOT a
# native Postgres ENUM) so the SQLite test DB (Base.metadata.create_all)
# and the Postgres prod DB (Alembic) share one schema; timestamps are
# DateTime(timezone=True) with server_default=func.now().
#
# Spec: docs/PAPER_TRADING_PLAN.md §6(a). Migration: 0013_paper_trading.
#
# Deliberate decisions vs the prose spec (recorded so a later engineer
# doesn't "fix" them back):
#   - conversation_id is String(36) (conversations.id is a UUID string),
#     NOT Integer as the prose draft said.
#   - forward_ideas.backtest_run_id is a SOFT reference (plain String,
#     no ForeignKey): the dsl_backtest_runs model lives outside
#     backend.models, so it is absent from the test create_all metadata;
#     a hard FK would dangle there. The degradation panel joins by value.
#   - Reconciled-money columns (cash balances, ledger amounts, fill
#     economics, accrued P&L, NAV figures) are Numeric(18,4) — paise
#     precision, crore headroom — mirroring the llm_usage.cost_usd
#     precedent. Binary Float drifts cents across a long replay chain
#     (reserve→fill→release→settle), which would break the ledger's
#     reconcile-by-replay guarantee. Instantaneous market prices
#     (fill_price, last_price, limit/trigger/intended, nifty_close) and
#     pure ratios (slippage_bps) stay Float, the way live quotes arrive.
#     NOTE for the P1 broker: Numeric reads back as decimal.Decimal —
#     do money math in Decimal and cast to float() only at the JSON edge.
#   - trade_logs.idea_id is intentionally NOT added here. P0's 0013 is
#     additive-only (no ALTER on existing tables). Live attribution
#     already flows through paper_fills.idea_id + paper_fills.trade_log_id
#     (FK back to trade_logs), so the existing audit timeline stays
#     linked. The optional §3.5 historical backfill (stamping idea_id on
#     pre-existing TradeLog rows) is a separate later migration if ever
#     wanted; it is not required for forward-testing.

# Allowed enum-like values, exported as frozensets so the paper-broker
# service can validate before INSERT without importing the constraints
# (mirrors RUN_STATUSES in backend/workflows/dsl/backtest/models.py).
PAPER_ACCOUNT_MODES: frozenset[str] = frozenset({"paper", "live"})
PAPER_ORDER_STATUSES: frozenset[str] = frozenset({
    "pending", "queued", "resting", "partially_filled",
    "filled", "cancelled", "rejected",
})
PAPER_LEDGER_KINDS: frozenset[str] = frozenset({
    "seed", "buy_debit", "sell_credit", "reserve", "release", "settlement",
})
FORWARD_IDEA_STATUSES: frozenset[str] = frozenset({
    "paper", "candidate", "promoted", "retired",
})


class PaperAccount(Base):
    """One simulated broker book per user (single-book v1; `label` leaves
    room for per-idea sub-accounts later). Buying power for the default
    long-only-CNC mode is `cash_available - cash_reserved`."""
    __tablename__ = "paper_accounts"
    __table_args__ = (
        CheckConstraint(
            "mode IN ('paper', 'live')", name="ck_paper_accounts_mode",
        ),
    )

    id = Column(String(36), primary_key=True, default=_uuid_str)
    user_id = Column(
        Integer, ForeignKey("users.id"), nullable=False,
        unique=True, index=True,
    )
    label = Column(String(64), nullable=False, default="default")
    currency = Column(String(3), nullable=False, default="INR")
    # Seed = the existing MOCK_MARGINS figure. cash_settled/available are
    # seeded equal to starting_capital by the account-creation service.
    starting_capital = Column(Numeric(18, 4), nullable=False, default=150000.0)
    cash_settled = Column(Numeric(18, 4), nullable=False, default=150000.0)
    cash_available = Column(Numeric(18, 4), nullable=False, default=150000.0)
    cash_reserved = Column(Numeric(18, 4), nullable=False, default=0.0)
    mode = Column(String(8), nullable=False, default="paper")
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(),
        onupdate=func.now(), nullable=False,
    )

    user = relationship("User", back_populates="paper_account")
    orders = relationship(
        "PaperOrder", back_populates="account",
        cascade="all, delete-orphan",
    )
    fills = relationship(
        "PaperFill", back_populates="account",
        cascade="all, delete-orphan",
    )
    positions = relationship(
        "PaperPosition", back_populates="account",
        cascade="all, delete-orphan",
    )
    ledger_entries = relationship(
        "PaperLedgerEntry", back_populates="account",
        cascade="all, delete-orphan",
    )
    nav_snapshots = relationship(
        "PaperNavSnapshot", back_populates="account",
        cascade="all, delete-orphan",
    )
    ideas = relationship(
        "ForwardIdea", back_populates="account",
        cascade="all, delete-orphan",
    )


class PaperOrder(Base):
    """Order lifecycle row. MARKET orders fill synchronously; LIMIT/SL/
    GTT/SL-M rows REST (status='resting') until the scheduler's fill
    evaluator marks them. `client_request_id` is UNIQUE for idempotency
    on scheduler retries. Attribution FKs link the order to its origin."""
    __tablename__ = "paper_orders"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'queued', 'resting', "
            "'partially_filled', 'filled', 'cancelled', 'rejected')",
            name="ck_paper_orders_status",
        ),
        # Hot path: the resting-order drain job + the open-orders blotter
        # filter by (account_id, status). account_id is the selective key;
        # status is low-cardinality, so the composite is the right shape.
        Index("ix_paper_orders_account_status", "account_id", "status"),
    )

    id = Column(String(36), primary_key=True, default=_uuid_str)
    account_id = Column(
        String(36), ForeignKey("paper_accounts.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    user_id = Column(
        Integer, ForeignKey("users.id"), nullable=False, index=True,
    )
    # Wide enough for the longest id the workflow engine builds — a
    # squareoff_symbol leg: "sqoff_sym:{SYM}:{run_uuid-36}:{step}:{att}:
    # legN:{SYM}" = 57 + 2*len(symbol). String(80) overflowed (and lost
    # the idempotency key) for symbols >= 12 chars; 120 covers a 30-char
    # symbol with margin. The P1 broker may additionally hash over-length
    # ids to a fixed-width key.
    client_request_id = Column(
        String(120), nullable=True, unique=True, index=True,
    )
    symbol = Column(String(50), nullable=False, index=True)
    exchange = Column(String(10), nullable=False, default="NSE")
    transaction_type = Column(String(10), nullable=False)  # BUY / SELL
    order_type = Column(String(16), nullable=False)  # MARKET/LIMIT/SL/SL-M/GTT
    product = Column(String(8), nullable=False, default="CNC")
    variety = Column(String(16), nullable=False, default="regular")  # regular/amo
    quantity = Column(Integer, nullable=False)
    limit_price = Column(Float, nullable=True)
    trigger_price = Column(Float, nullable=True)
    # LTP at decision time + its quote timestamp — drives slippage-vs-
    # intended and guards against look-ahead in the fill evaluator.
    intended_price = Column(Float, nullable=True)
    intended_quote_at = Column(DateTime(timezone=True), nullable=True)
    status = Column(
        String(16), nullable=False, default="pending", index=True,
    )
    reserved_cash = Column(Numeric(18, 4), nullable=False, default=0.0)
    filled_quantity = Column(Integer, nullable=False, default=0)
    reject_reason = Column(String(200), nullable=True)
    # OCO: SL and TP siblings share a group; one fill cancels the other.
    gtt_oco_group = Column(String(36), nullable=True, index=True)
    parent_order_id = Column(
        String(36), ForeignKey("paper_orders.id"), nullable=True,
    )
    # Attribution. source mirrors TradeLog.source; origin_kind +
    # the *_id FKs resolve the durable idea for forward-testing.
    source = Column(String(50), nullable=True, index=True)
    origin_kind = Column(String(16), nullable=True)  # workflow/chat/strategy/manual
    # SOFT references (no hard FK): the prod Agent-System ids are native
    # `uuid` while these are String(36), and `conversations` may not exist in
    # every deployment — a hard FK fails to build on Postgres (varchar↔uuid)
    # and on a DB without that table. We store the id as text and resolve the
    # idea in code (backend/paper/ideas.py), exactly like backtest_run_id.
    workflow_id = Column(String(36), nullable=True)
    workflow_run_id = Column(String(36), nullable=True)
    conversation_id = Column(String(36), nullable=True)
    strategy_id = Column(Integer, ForeignKey("strategies.id"), nullable=True)
    # F&O P2: SOFT reference to option_strategies.id — one PaperOrder per
    # LEG, grouped by the parent strategy. Soft (no FK) per the
    # cross-domain pattern above; idempotency key is
    # "optstrat:{option_strategy_id}:leg{n}".
    option_strategy_id = Column(String(36), nullable=True, index=True)
    idea_id = Column(
        String(36), ForeignKey("forward_ideas.id"), nullable=True, index=True,
    )
    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(),
        onupdate=func.now(), nullable=False,
    )

    account = relationship("PaperAccount", back_populates="orders")
    fills = relationship(
        "PaperFill", back_populates="order", cascade="all, delete-orphan",
    )
    idea = relationship("ForwardIdea", back_populates="orders")
    # Self-referential bracket grouping: entry -> SL/TP children.
    parent = relationship(
        "PaperOrder", remote_side="PaperOrder.id", backref="children",
    )


class PaperFill(Base):
    """An immutable execution — the SOURCE OF TRUTH. Positions and cash
    are derived from the fills log, so scheduler retries can't double-
    count. Charges come from services/trading_costs (no new cost code)."""
    __tablename__ = "paper_fills"

    id = Column(String(36), primary_key=True, default=_uuid_str)
    order_id = Column(
        String(36), ForeignKey("paper_orders.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    account_id = Column(
        String(36), ForeignKey("paper_accounts.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    user_id = Column(
        Integer, ForeignKey("users.id"), nullable=False, index=True,
    )
    symbol = Column(String(50), nullable=False, index=True)
    transaction_type = Column(String(10), nullable=False)  # BUY / SELL
    quantity = Column(Integer, nullable=False)
    fill_price = Column(Float, nullable=False)  # market touch +/- slippage
    gross_value = Column(Numeric(18, 4), nullable=False)  # fill_price * quantity
    charges = Column(Numeric(18, 4), nullable=False, default=0.0)  # trading_costs
    net_cashflow = Column(Numeric(18, 4), nullable=False)  # - on buy, + on sell
    slippage_bps = Column(Float, nullable=True)  # ratio, not money
    realized_pnl = Column(Numeric(18, 4), nullable=True)  # booked on SELLs (avg cost)
    settles_at = Column(DateTime(timezone=True), nullable=True)  # T+1 on SELL
    # F&O P2: IV of the option at fill time (from the chain solve) —
    # feeds P&L attribution (delta/theta/vega decomposition) later.
    # NULL for equity fills.
    iv_at_fill = Column(Float, nullable=True)
    idea_id = Column(
        String(36), ForeignKey("forward_ideas.id"), nullable=True, index=True,
    )
    # Link to the existing audit row so order history stays one timeline.
    trade_log_id = Column(Integer, ForeignKey("trade_logs.id"), nullable=True)
    filled_at = Column(
        DateTime(timezone=True), server_default=func.now(),
        nullable=False, index=True,
    )

    order = relationship("PaperOrder", back_populates="fills")
    account = relationship("PaperAccount", back_populates="fills")
    idea = relationship("ForwardIdea", back_populates="fills")


class PaperPosition(Base):
    """Open-lot cache derived from fills. unrealized_pnl and day_pnl are
    computed on read from last_price / prev_close (never stored)."""
    __tablename__ = "paper_positions"
    __table_args__ = (
        UniqueConstraint(
            "account_id", "symbol",
            name="uq_paper_positions_account_symbol",
        ),
    )

    id = Column(String(36), primary_key=True, default=_uuid_str)
    account_id = Column(
        String(36), ForeignKey("paper_accounts.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    user_id = Column(
        Integer, ForeignKey("users.id"), nullable=False, index=True,
    )
    symbol = Column(String(50), nullable=False)
    # EQUITY positions are long-only (>= 0, enforced by the fill engine).
    # OPTION positions (is_option=True, F&O P2) are SIGNED — a short
    # straddle holds quantity < 0 on both legs. The equity invariant is
    # untouched: only paper/options_routing writes negative quantities.
    quantity = Column(Integer, nullable=False, default=0)
    avg_cost = Column(Numeric(18, 4), nullable=False, default=0.0)  # incl. buy charges
    realized_pnl = Column(Numeric(18, 4), nullable=False, default=0.0)  # cumulative
    last_price = Column(Float, nullable=True)  # market mark-to-market
    last_mark_at = Column(DateTime(timezone=True), nullable=True)
    prev_close = Column(Float, nullable=True)  # for day P&L
    stale = Column(Boolean, nullable=False, default=False)
    # F&O P2: option-position routing flags. ``segment`` carries
    # NFO-OPT/BFO-OPT so valuation + greeks services mark through the
    # option chain instead of the equity quote path.
    is_option = Column(Boolean, nullable=False, default=False)
    segment = Column(String(16), nullable=True)
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(),
        onupdate=func.now(), nullable=False,
    )

    account = relationship("PaperAccount", back_populates="positions")


class PaperLedgerEntry(Base):
    """Append-only cash transaction trail. Every fill, reserve/release,
    and settlement writes one row; balance_after is the running
    cash_available so the ledger reconciles the account by replay.

    Replay invariant the P1 broker MUST uphold: balance_after always
    equals the account's running cash_available AFTER this row. 'reserve'
    moves money OUT of cash_available into cash_reserved (negative
    amount); 'release' moves it back (positive). So SUM(amount) over a
    range reconstructs cash_available, and buying power is
    cash_available - cash_reserved. Pin this so a future reconciler and
    the live column can't silently diverge."""
    __tablename__ = "paper_ledger"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('seed', 'buy_debit', 'sell_credit', "
            "'reserve', 'release', 'settlement')",
            name="ck_paper_ledger_kind",
        ),
    )

    id = Column(String(36), primary_key=True, default=_uuid_str)
    account_id = Column(
        String(36), ForeignKey("paper_accounts.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    fill_id = Column(
        String(36), ForeignKey("paper_fills.id"), nullable=True,
    )
    kind = Column(String(24), nullable=False)
    amount = Column(Numeric(18, 4), nullable=False)  # signed
    balance_after = Column(Numeric(18, 4), nullable=False)  # running cash_available
    note = Column(String(200), nullable=True)
    recorded_at = Column(
        DateTime(timezone=True), server_default=func.now(),
        nullable=False, index=True,
    )

    account = relationship("PaperAccount", back_populates="ledger_entries")


class ForwardIdea(Base):
    """The forward-test unit: a durable idea (a workflow, a chat turn, or
    a saved strategy) whose paper fills accrue an attributable, live,
    out-of-sample track record.

    Dedup of idea creation is enforced in the RESOLVER, not a DB index
    (P0 keeps the schema permissive). Natural identity per origin_kind:
    workflow -> (account_id, workflow_id); strategy -> (account_id,
    strategy_id); chat -> (account_id, conversation_id, label) — note
    one chat can spawn several distinct ideas, so the chat key includes
    the label. The resolver MUST be race-proof under the concurrent
    scheduler: SELECT ... FOR UPDATE (Postgres) or an advisory lock, or
    add partial UNIQUE indexes in the forward-test phase. Without that a
    double-fire forks an idea and splits its scorecard series."""
    __tablename__ = "forward_ideas"
    __table_args__ = (
        CheckConstraint(
            "status IN ('paper', 'candidate', 'promoted', 'retired')",
            name="ck_forward_ideas_status",
        ),
    )

    id = Column(String(36), primary_key=True, default=_uuid_str)
    user_id = Column(
        Integer, ForeignKey("users.id"), nullable=False, index=True,
    )
    account_id = Column(
        String(36), ForeignKey("paper_accounts.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    origin_kind = Column(String(16), nullable=False)  # workflow/chat/strategy/manual
    # SOFT references (no hard FK) — see PaperOrder above: prod ids are uuid,
    # these are String(36), and `conversations` may be absent. Resolved in
    # code. workflow_id stays indexed (the resolver dedups on it).
    workflow_id = Column(String(36), nullable=True, index=True)
    conversation_id = Column(String(36), nullable=True)
    strategy_id = Column(Integer, ForeignKey("strategies.id"), nullable=True)
    label = Column(String(140), nullable=False)  # LLM/user-named
    inception_date = Column(Date, nullable=True)  # first paper fill
    status = Column(String(16), nullable=False, default="paper")
    status_changed_at = Column(DateTime(timezone=True), nullable=True)
    # SOFT reference to dsl_backtest_runs.id (no hard FK — see header).
    backtest_run_id = Column(String(36), nullable=True)
    cohort_trial_count = Column(Integer, nullable=False, default=1)  # DSR deflation
    # List-view copy (cum_return, sharpe, alpha, psr, mdd), refreshed at
    # each daily close. Everything else is computed on read.
    # Dialect-aware so create_all matches the migration on Postgres:
    # JSONB on PG (operator + GIN support), JSON on SQLite. Plain `JSON`
    # would render as `json` on PG via create_all while the migration
    # pins `jsonb` — a silent physical-type divergence between the two
    # build paths. with_variant closes that gap.
    scorecard_cache = Column(
        JSON().with_variant(JSONB(astext_type=Text()), "postgresql"),
        nullable=True,
    )
    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(),
        onupdate=func.now(), nullable=False,
    )

    account = relationship("PaperAccount", back_populates="ideas")
    orders = relationship("PaperOrder", back_populates="idea")
    fills = relationship("PaperFill", back_populates="idea")
    idea_nav_snapshots = relationship(
        "PaperIdeaNavSnapshot", back_populates="idea",
        cascade="all, delete-orphan",
    )


class PaperNavSnapshot(Base):
    """Account-grain daily equity point — backs the NAV / equity curve.
    One row per (account, as_of_date) (the EOD snapshot job upserts)."""
    __tablename__ = "paper_nav_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "account_id", "as_of_date",
            name="uq_paper_nav_snapshots_account_date",
        ),
    )

    id = Column(String(36), primary_key=True, default=_uuid_str)
    account_id = Column(
        String(36), ForeignKey("paper_accounts.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    user_id = Column(
        Integer, ForeignKey("users.id"), nullable=False, index=True,
    )
    as_of_date = Column(Date, nullable=False, index=True)
    cash_available = Column(Numeric(18, 4), nullable=False)
    cash_settled = Column(Numeric(18, 4), nullable=False)
    positions_mv = Column(Numeric(18, 4), nullable=False)  # sum qty * LTP
    nav = Column(Numeric(18, 4), nullable=False)  # cash_available + positions_mv
    realized_pnl_cum = Column(Numeric(18, 4), nullable=False)
    unrealized_pnl = Column(Numeric(18, 4), nullable=False)
    nifty_close = Column(Float, nullable=True)  # market benchmark for alpha/IR
    is_stale = Column(Boolean, nullable=False, default=False)
    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )

    account = relationship("PaperAccount", back_populates="nav_snapshots")


class PaperIdeaNavSnapshot(Base):
    """Idea-grain daily equity point — the forward-test scorecard series.
    An idea owns the lots its fills opened (FIFO over paper_fills.idea_id;
    no separate lots table in v1)."""
    __tablename__ = "paper_idea_nav_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "idea_id", "as_of_date",
            name="uq_paper_idea_nav_snapshots_idea_date",
        ),
    )

    id = Column(String(36), primary_key=True, default=_uuid_str)
    idea_id = Column(
        String(36), ForeignKey("forward_ideas.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    # CASCADE for symmetry with every other account-pointing child, so an
    # account delete drains this table directly (not only via the idea
    # CASCADE path) — removes the ordering dependency a NO ACTION FK had.
    account_id = Column(
        String(36), ForeignKey("paper_accounts.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    as_of_date = Column(Date, nullable=False, index=True)
    committed_capital = Column(Numeric(18, 4), nullable=False)  # open-lot cost basis
    positions_mv = Column(Numeric(18, 4), nullable=False)  # MV of this idea's lots
    idea_nav = Column(Numeric(18, 4), nullable=False)  # committed-cash slice + MV
    realized_pnl = Column(Numeric(18, 4), nullable=False)
    unrealized_pnl = Column(Numeric(18, 4), nullable=False)
    nifty_close = Column(Float, nullable=True)  # market shared benchmark
    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )

    idea = relationship("ForwardIdea", back_populates="idea_nav_snapshots")


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
    # R4b: explicit auto-deactivation timestamp. NULL = no expiry.
    # The engine consults this before firing any trigger; past expiry
    # transitions the workflow to `paused` and skips the run.
    expires_at = Column(DateTime(timezone=True), nullable=True)
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
    # Per-step next-run-at, set by the scheduler for trigger.schedule
    # steps. NULL for non-trigger steps and for triggers that aren't
    # scheduled (manual / webhook / price / indicator). Multi-trigger
    # workflows can have several rows here, one per trigger.schedule.
    next_run_at = Column(DateTime(timezone=True), nullable=True, index=True)

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
    # The step_index of the trigger.* that fired this run. NULL means
    # "the workflow's only trigger" (legacy single-trigger workflows
    # written before 2026-05-04). Multi-trigger workflows always set
    # this so the engine knows which branch to execute.
    triggered_step_index = Column(Integer, nullable=True)
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


class LlmUsage(Base):
    """One row per LLM API call. Drives cost dashboards.

    Indexed for the two queries we'll actually run:
      - "spend by user in the last 24h"
      - "spend by model in the last 7d"
    """
    __tablename__ = "llm_usage"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    conversation_id = Column(String(64), nullable=True, index=True)
    turn_id = Column(String(64), nullable=True)
    request_id = Column(String(64), nullable=True, index=True)
    endpoint = Column(String(64), nullable=False)    # "chat", "propose", "router", "agentic", ...
    provider = Column(String(32), nullable=False)    # "openai" | "azure"
    model = Column(String(64), nullable=False)
    input_tokens = Column(Integer, nullable=False, default=0)
    # Subset of input_tokens served from the OpenAI prompt cache. Billed
    # at 50% of the normal input rate on the Responses API. 0 for rows
    # written before migration 0006 shipped.
    cached_input_tokens = Column(Integer, nullable=False, default=0)
    output_tokens = Column(Integer, nullable=False, default=0)
    reasoning_tokens = Column(Integer, nullable=False, default=0)
    total_tokens = Column(Integer, nullable=False, default=0)
    cost_usd = Column(Numeric(12, 6), nullable=False, default=0)
    latency_ms = Column(Float, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)


# ─── IPO Applications (P0: register-not-execute) ─────────────────────
#
# Mirrors TradeLog: a row per user-registered IPO application intent.
# v1 model is REGISTER-NOT-EXECUTE — no broker/ASBA/UPI-mandate call ever.
# We persist what the user said they want to apply for so the UI can
# show the application card again on reload and a (future) P2 reminder
# job can email them at the open / close. Statuses kept open-ended for
# the reminder/applied/allotted lifecycle that follows in later phases;
# v1 only writes "registered" and "withdrawn".
#
# Soft references (no hard FK) for conversation_id / workflow_id —
# conversations table is owned by another module and prod workflows.id is
# native uuid which doesn't FK cleanly across the SQLite test DB. Mirrors
# the soft-ref pattern paper_orders adopted in 0013.
IPO_APPLICATION_STATUSES: frozenset[str] = frozenset({
    "registered", "withdrawn",
    # Reserved for the P2 reminder / broker-cycle bridge — never written
    # by v1 code, listed here so the CheckConstraint is forward-compatible.
    "intent_armed", "applied", "blocked", "allotted",
    "not_allotted", "rejected",
})

IPO_TYPES: frozenset[str] = frozenset({"mainboard", "sme"})

IPO_CATEGORIES: frozenset[str] = frozenset({
    "retail", "snii", "bnii", "shareholder", "employee",
})

IPO_BID_PRICE_MODES: frozenset[str] = frozenset({"cutoff", "fixed"})


class IPOApplication(Base):
    """One row per user-registered IPO application intent.

    P0: registers intent only — Pivot never submits or funds the bid.
    The user places + approves the UPI/ASBA mandate themselves in the
    broker app by 5 PM on close day. ``amount_estimate`` is the FE-shown
    "estimated amount you'll need", computed SERVER-SIDE (we don't trust
    the client's number). Never store the raw UPI handle — only the
    masked form (``upi_id_masked``).
    """
    __tablename__ = "ipo_applications"
    __table_args__ = (
        CheckConstraint(
            "ipo_type IN ('mainboard', 'sme')",
            name="ck_ipo_applications_type",
        ),
        CheckConstraint(
            "category IN ('retail', 'snii', 'bnii', 'shareholder', 'employee')",
            name="ck_ipo_applications_category",
        ),
        CheckConstraint(
            "bid_price_mode IN ('cutoff', 'fixed')",
            name="ck_ipo_applications_bid_price_mode",
        ),
        CheckConstraint(
            "status IN ('registered', 'withdrawn', 'intent_armed', "
            "'applied', 'blocked', 'allotted', 'not_allotted', 'rejected')",
            name="ck_ipo_applications_status",
        ),
        Index(
            "ix_ipo_applications_user_status",
            "user_id", "status",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(
        Integer, ForeignKey("users.id"), nullable=False, index=True,
    )
    ipo_symbol = Column(String(50), nullable=False, index=True)
    ipo_name = Column(String(200), nullable=True)
    ipo_type = Column(String(16), nullable=False)
    category = Column(String(16), nullable=False)

    quantity_lots = Column(Integer, nullable=False)
    lot_size = Column(Integer, nullable=False)
    bid_price_mode = Column(String(8), nullable=False)
    bid_price = Column(Float, nullable=True)
    amount_estimate = Column(Float, nullable=False)

    upi_id_masked = Column(String(64), nullable=True)
    status = Column(String(16), nullable=False, default="registered")

    autonomous = Column(Boolean, nullable=False, default=False)
    paper_mode = Column(Boolean, nullable=False, default=False)
    stale = Column(Boolean, nullable=False, default=False)

    # SOFT references — no FK, see header above.
    conversation_id = Column(String(64), nullable=True)
    workflow_id = Column(Integer, nullable=True)

    source = Column(String(50), nullable=True)
    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(),
        onupdate=func.now(), nullable=False,
    )


# ── IPO paper-mode simulated allocations (P3) ─────────────────────────────
#
# P3 introduces a LABELLED ledger of simulated IPO allocations parallel to
# IPOApplication. When a user is in paper mode and registers / arms an IPO
# intent, a PaperIpoAllocation row is written alongside the IPOApplication
# with a DETERMINISTIC lottery outcome (the simulator in
# backend/paper/ipo_sim.py). The row is purely a tracking artefact — it does
# NOT move paper-account cash/positions/NAV (that is P3.1). The "Simulated"
# label is load-bearing: the FE renders this set distinctly so the user is
# never confused about whether real money moved.
#
# paper_account_id is a HARD FK to paper_accounts.id (String(36)) — same
# pattern as paper_orders.account_id, since both live in the paper domain.
# ipo_application_id is a SOFT reference to ipo_applications.id (Integer):
# no hard FK because that table is in the IPO domain and we mirror the
# soft-ref pattern paper_orders adopted for cross-domain links.
#
# allotment_status is one of {'allotted', 'not_allotted', 'pending'}:
#   - allotted     -> quantity_allotted == quantity_applied (all-or-nothing
#                     simplification; documented in ipo_sim.py)
#   - not_allotted -> quantity_allotted == 0
#   - pending      -> result not yet drawn (reserved; the deterministic
#                     simulator always resolves to allotted/not_allotted in
#                     P3, but the state is forward-compatible with a future
#                     defer-until-close path).
#
# listing_price / simulated_pnl are P3.1 placeholders — left NULL in P3
# (no fabricated listing prices, never).
class PaperIpoAllocation(Base):
    """Labelled-simulation IPO allocation row written in paper mode.

    See the §"IPO paper-mode simulated allocations" header above. This row
    is NEVER part of the paper cash/NAV ledger in P3 — that integration is
    deferred to P3.1. ``simulated=True`` is enforced at the column default
    and asserted in the simulator so the row is unmistakeable end-to-end.
    """
    __tablename__ = "paper_ipo_allocations"
    __table_args__ = (
        CheckConstraint(
            "allotment_status IN ('allotted', 'not_allotted', 'pending')",
            name="ck_paper_ipo_allocations_status",
        ),
        Index(
            "ix_paper_ipo_allocations_user_symbol",
            "user_id", "ipo_symbol",
        ),
    )

    id = Column(String(36), primary_key=True, default=_uuid_str)
    user_id = Column(
        Integer, ForeignKey("users.id"), nullable=False, index=True,
    )
    paper_account_id = Column(
        String(36), ForeignKey("paper_accounts.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    # SOFT reference to ipo_applications.id (cross-domain, no hard FK).
    ipo_application_id = Column(Integer, nullable=True)
    ipo_symbol = Column(String(50), nullable=False, index=True)
    ipo_name = Column(String(200), nullable=True)
    ipo_type = Column(String(16), nullable=False)

    lots_applied = Column(Integer, nullable=False)
    quantity_applied = Column(Integer, nullable=False)
    amount_applied = Column(Numeric(18, 4), nullable=False)
    issue_price = Column(Numeric(18, 4), nullable=False)

    quantity_allotted = Column(Integer, nullable=False, default=0)
    allotment_status = Column(String(16), nullable=False, default="pending")
    allotment_date = Column(Date, nullable=True)

    # listing-day fields — populated by the P3.1 listing-credit poller
    # when listing_date arrives. ``listing_price`` is snapshotted at the
    # moment of credit via marks.get_mark_price (honest None when the
    # just-listed scrip has no quote yet); ``simulated_pnl`` is the
    # (listing_price - issue_price) * quantity_allotted record at credit
    # time. The LIVE mark-to-market on the resulting PaperPosition then
    # tracks the listing gain in real time on the Paper dashboard.
    listing_date = Column(Date, nullable=True)
    listing_price = Column(Numeric(18, 4), nullable=True)
    simulated_pnl = Column(Numeric(18, 4), nullable=True)

    # P3.1 idempotency latch + bookkeeping. ``book_credited`` flips True
    # once the allotted shares have either been credited into the paper
    # book (paper_fill_id set) OR terminally skipped (e.g. insufficient
    # paper buying power; book_note records the reason). Together with
    # the UNIQUE paper_orders.client_request_id "ipo-listing-{alloc.id}"
    # this guarantees we can never double-credit. NULL paper_fill_id =
    # the credit was skipped (look at book_note); non-NULL = the BUY
    # PaperFill produced by execute_market_fill at issue price.
    book_credited = Column(Boolean, nullable=False, default=False)
    book_note = Column(String, nullable=True)
    paper_fill_id = Column(String(36), nullable=True)

    # SOFT references (no FK) — mirrors IPOApplication / ForwardIdea.
    conversation_id = Column(String(64), nullable=True)
    workflow_id = Column(String(36), nullable=True)

    source = Column(String(50), nullable=False)
    simulated = Column(Boolean, nullable=False, default=True)

    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(),
        onupdate=func.now(), nullable=False,
    )


# ─── F&O instrument master + dynamic universe (F&O P0) ───────────────
#
# THE single source of truth for every tradable derivative contract —
# strikes, expiries, LOT SIZES (which changed Dec'25/Jan'26: NIFTY
# 75→65, BANKNIFTY 35→30 — hardcoding a lot size anywhere is a bug),
# tick sizes and segments. Repopulated daily (~08:35 IST) from the Kite
# instruments dump (https://api.kite.trade/instruments — regenerated
# once a day); rows present yesterday but absent today are marked
# expired via ``last_seen`` rather than deleted, so backtests and audit
# trails can still resolve a contract that no longer trades.
#
# The dump is a CACHE of the exchange contract master, not a ledger —
# a full refresh may upsert every row. ``first_seen``/``last_seen``
# drive zero-code-change discovery: a brand-new weekly expiry, strike
# ladder extension, or newly-listed F&O underlying appears as rows with
# first_seen == today. NO symbol list is hardcoded anywhere.
#
# MCX-OPT rows are first-class here (research/screening), but execution
# for the MCX segment is hard-blocked at the strategy-registration gate
# — see OptionUniverse.research_only.
class InstrumentMaster(Base):
    """One row per tradable contract from the daily Kite instruments dump."""
    __tablename__ = "instrument_master"
    __table_args__ = (
        Index(
            "ix_instrument_master_chain",
            "underlying", "expiry", "instrument_type", "strike",
        ),
        Index("ix_instrument_master_segment_expiry", "segment", "expiry"),
    )

    # Kite's instrument_token — also the WebSocket subscription key.
    instrument_token = Column(BigInteger, primary_key=True, autoincrement=False)
    exchange_token = Column(BigInteger, nullable=True)
    tradingsymbol = Column(String(64), nullable=False, index=True)
    # Kite's ``name`` column — the underlying root (NIFTY, CRUDEOIL,
    # RELIANCE…). More robust than parsing tradingsymbol formats, which
    # differ across NSE/BSE/MCX and changed in 2025.
    name = Column(String(64), nullable=True)
    underlying = Column(String(40), nullable=False, index=True)
    exchange = Column(String(8), nullable=False)       # NSE / BSE / NFO / BFO / MCX
    segment = Column(String(16), nullable=False)       # NFO-OPT / BFO-OPT / MCX-OPT / NFO-FUT / …
    instrument_type = Column(String(4), nullable=False)  # CE / PE / FUT / EQ
    strike = Column(Numeric(14, 4), nullable=True)
    expiry = Column(Date, nullable=True, index=True)
    # weekly | monthly — DERIVED from expiry-date spacing per underlying,
    # never from symbol parsing or hardcoded weekday rules (NSE moved
    # weeklies to Tuesday-and-NIFTY-only in Sep 2025; BSE differs).
    expiry_kind = Column(String(12), nullable=True)
    lot_size = Column(Integer, nullable=True)
    tick_size = Column(Float, nullable=True)
    # Stale snapshot price from the dump — NEVER a live quote. Kept only
    # for ATM-centering before the first real quote of the day.
    last_price = Column(Float, nullable=True)

    first_seen = Column(Date, nullable=False)
    last_seen = Column(Date, nullable=False, index=True)
    refreshed_on = Column(Date, nullable=False, index=True)


# Dynamic, liquidity-selected F&O universe — replaces every "list of
# underlyings" constant. One row per (underlying, as_of) with the
# liquidity evidence that drove the selection; percentile thresholds
# (not absolute constants) so the universe self-adjusts as markets grow.
class OptionUniverse(Base):
    __tablename__ = "option_universe"
    __table_args__ = (
        UniqueConstraint(
            "underlying", "as_of", name="uq_option_universe_underlying_asof",
        ),
        Index("ix_option_universe_asof_selected", "as_of", "selected"),
    )

    id = Column(Integer, primary_key=True, index=True)
    underlying = Column(String(40), nullable=False, index=True)
    as_of = Column(Date, nullable=False)
    segment = Column(String(16), nullable=False)
    exchange = Column(String(8), nullable=False)

    # Liquidity evidence (front-expiry ATM±N sample at selection time).
    avg_oi = Column(Float, nullable=True)
    avg_volume = Column(Float, nullable=True)
    spread_pct_atm = Column(Float, nullable=True)
    liquidity_score = Column(Float, nullable=True)

    # selected      → surfaced in chat suggestions / screeners.
    # research_only → quotable + screenable but EXECUTION-BLOCKED
    #                 (all MCX-OPT rows in v1 — commodities are research
    #                 only by product decision).
    selected = Column(Boolean, nullable=False, default=False)
    research_only = Column(Boolean, nullable=False, default=False)
    reason = Column(String(120), nullable=True)

    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )


# ─── Option strategies (F&O P1: register-not-execute, paper-first) ───
#
# Mirrors IPOApplication's posture: a row per user-registered multi-leg
# option strategy INTENT. ``book`` picks the destination:
#   paper → P2's paper broker auto-executes the legs (simulated fills).
#   live  → REGISTER-NOT-EXECUTE, forever: Pivot never places a live
#           F&O order; the user executes in their broker app. Status
#           stays 'registered' until withdrawn/closed by the user.
# Legs are CHILD ROWS (not JSONB): each leg is an independent per-symbol
# position once filled — a short straddle holds a CE and a PE position
# marked/squared separately, so the legs must be addressable rows that
# paper fills can reference (P2). Decision documented in the F&O plan.
#
# Soft refs for conversation_id / workflow_id — same cross-domain
# pattern as IPOApplication / PaperIpoAllocation.
OPTION_STRATEGY_STATUSES: frozenset[str] = frozenset({
    "registered", "withdrawn",
    # Reserved for P2/P3 lifecycle — never written by P1 code.
    "intent_armed", "active", "closed", "rejected", "blocked",
})

OPTION_STRATEGY_BOOKS: frozenset[str] = frozenset({"paper", "live"})


class OptionStrategy(Base):
    """One row per registered multi-leg option strategy intent."""
    __tablename__ = "option_strategies"
    __table_args__ = (
        CheckConstraint(
            "book IN ('paper', 'live')",
            name="ck_option_strategies_book",
        ),
        CheckConstraint(
            "status IN ('registered', 'withdrawn', 'intent_armed', "
            "'active', 'closed', 'rejected', 'blocked')",
            name="ck_option_strategies_status",
        ),
        Index("ix_option_strategies_user_status", "user_id", "status"),
    )

    id = Column(String(36), primary_key=True, default=_uuid_str)
    user_id = Column(
        Integer, ForeignKey("users.id"), nullable=False, index=True,
    )
    underlying = Column(String(40), nullable=False, index=True)
    segment = Column(String(16), nullable=False)
    exchange = Column(String(8), nullable=False)
    template = Column(String(40), nullable=False)   # bull_call_spread / custom / …
    expiry = Column(Date, nullable=False)
    book = Column(String(8), nullable=False, default="paper")
    status = Column(String(16), nullable=False, default="registered")
    qty_lots = Column(Integer, nullable=False, default=1)
    lot_size = Column(Integer, nullable=False)      # snapshot from master at register

    # Decision-quad snapshot at registration (server-recomputed, never
    # client-supplied). max_loss/max_profit NULL = unlimited.
    net_premium = Column(Numeric(18, 4), nullable=True)
    max_loss = Column(Numeric(18, 4), nullable=True)
    max_profit = Column(Numeric(18, 4), nullable=True)
    pop = Column(Float, nullable=True)
    capital_required = Column(Numeric(18, 4), nullable=True)
    margin_estimate = Column(Numeric(18, 4), nullable=True)
    net_greeks_json = Column(JSON, nullable=True)
    critique_verdict = Column(String(12), nullable=True)  # ok|caution|risky

    # SOFT references — cross-domain, no FK (see header).
    conversation_id = Column(String(64), nullable=True)
    workflow_id = Column(String(36), nullable=True)

    source = Column(String(50), nullable=True)
    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(),
        onupdate=func.now(), nullable=False,
    )

    legs = relationship(
        "OptionLeg", back_populates="strategy",
        cascade="all, delete-orphan", order_by="OptionLeg.strike",
    )


# F&O P2: daily portfolio-Greeks snapshot — written at market close
# alongside the NAV snapshot. One row per (account, date). The
# delta-equivalent (FutEq) notional is SEBI's intraday position-limit
# accounting basis and the right internal exposure representation.
class PaperGreeksSnapshot(Base):
    __tablename__ = "paper_greeks_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "account_id", "as_of",
            name="uq_paper_greeks_snapshots_account_asof",
        ),
    )

    id = Column(String(36), primary_key=True, default=_uuid_str)
    account_id = Column(
        String(36), ForeignKey("paper_accounts.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    user_id = Column(
        Integer, ForeignKey("users.id"), nullable=False, index=True,
    )
    as_of = Column(Date, nullable=False)
    net_delta = Column(Float, nullable=False, default=0.0)   # units of underlying
    net_gamma = Column(Float, nullable=False, default=0.0)
    net_theta = Column(Float, nullable=False, default=0.0)   # ₹/day
    net_vega = Column(Float, nullable=False, default=0.0)    # ₹ per vol point
    delta_notional = Column(Numeric(18, 2), nullable=True)   # FutEq ₹
    position_count = Column(Integer, nullable=False, default=0)
    # Per-underlying breakdown {underlying: {delta, gamma, theta, vega,
    # delta_notional, positions}} — display payload, not a query target.
    breakdown_json = Column(JSON, nullable=True)
    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )


class OptionLeg(Base):
    """One leg of an OptionStrategy — addressable so P2 paper fills can
    reference it (client_request_id "optstrat:{strategy_id}:leg{n}")."""
    __tablename__ = "option_legs"
    __table_args__ = (
        CheckConstraint(
            "option_type IN ('CE', 'PE')", name="ck_option_legs_type",
        ),
        CheckConstraint(
            "side IN ('BUY', 'SELL')", name="ck_option_legs_side",
        ),
    )

    id = Column(String(36), primary_key=True, default=_uuid_str)
    strategy_id = Column(
        String(36),
        ForeignKey("option_strategies.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    leg_index = Column(Integer, nullable=False, default=0)
    instrument_token = Column(BigInteger, nullable=True)
    tradingsymbol = Column(String(64), nullable=True)
    option_type = Column(String(2), nullable=False)
    side = Column(String(4), nullable=False)
    strike = Column(Numeric(14, 4), nullable=False)
    qty_lots = Column(Integer, nullable=False, default=1)
    lot_size = Column(Integer, nullable=False)
    # Entry snapshot at registration — feeds P&L attribution later.
    entry_mid = Column(Float, nullable=True)
    entry_iv = Column(Float, nullable=True)
    entry_delta = Column(Float, nullable=True)

    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )

    strategy = relationship("OptionStrategy", back_populates="legs")
