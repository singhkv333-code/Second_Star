from sqlalchemy import (
    Column, Integer, String, Boolean, DateTime,
    Text, Float, ForeignKey, Enum as SQLEnum
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import enum
from backend.database import Base


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
