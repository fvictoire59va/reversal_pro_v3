"""SQLAlchemy ORM models matching TimescaleDB schema."""

from datetime import datetime, timezone
from sqlalchemy import (
    Column, String, Float, Boolean, Integer, DateTime, Text,
    PrimaryKeyConstraint, Index, ForeignKey, JSON,
)
from sqlalchemy.orm import relationship
from .database import Base


def _utcnow():
    """Return a timezone-aware UTC datetime (replacement for datetime.utcnow)."""
    return datetime.now(timezone.utc)


class OHLCV(Base):
    __tablename__ = "ohlcv"

    time = Column(DateTime(timezone=True), nullable=False)
    symbol = Column(String, nullable=False)
    timeframe = Column(String, nullable=False, default="1h")
    open = Column(Float, nullable=False)
    high = Column(Float, nullable=False)
    low = Column(Float, nullable=False)
    close = Column(Float, nullable=False)
    volume = Column(Float, nullable=False, default=0)

    __table_args__ = (
        PrimaryKeyConstraint("time", "symbol", "timeframe"),
    )


class Indicator(Base):
    __tablename__ = "indicators"

    time = Column(DateTime(timezone=True), nullable=False)
    symbol = Column(String, nullable=False)
    timeframe = Column(String, nullable=False, default="1h")
    ema_9 = Column(Float)
    ema_14 = Column(Float)
    ema_21 = Column(Float)
    atr = Column(Float)
    trend = Column(String)

    __table_args__ = (
        PrimaryKeyConstraint("time", "symbol", "timeframe"),
    )


class Signal(Base):
    __tablename__ = "signals"

    id = Column(Integer, primary_key=True, autoincrement=True)
    time = Column(DateTime(timezone=True), nullable=False)
    symbol = Column(String, nullable=False)
    timeframe = Column(String, nullable=False, default="1h")
    bar_index = Column(Integer, nullable=False)
    price = Column(Float, nullable=False)
    actual_price = Column(Float, nullable=False)
    is_bullish = Column(Boolean, nullable=False)
    is_preview = Column(Boolean, nullable=False, default=False)
    signal_label = Column(String, nullable=False, default="REVERSAL")
    detected_at = Column(DateTime(timezone=True), default=_utcnow)
    created_at = Column(DateTime(timezone=True), default=_utcnow)


class Zone(Base):
    __tablename__ = "zones"

    id = Column(Integer, primary_key=True, autoincrement=True)
    time = Column(DateTime(timezone=True), nullable=False)
    symbol = Column(String, nullable=False)
    timeframe = Column(String, nullable=False, default="1h")
    zone_type = Column(String, nullable=False)
    center_price = Column(Float, nullable=False)
    top_price = Column(Float, nullable=False)
    bottom_price = Column(Float, nullable=False)
    start_bar = Column(Integer, nullable=False)
    end_bar = Column(Integer, nullable=False)
    created_at = Column(DateTime(timezone=True), default=_utcnow)


class AnalysisRun(Base):
    __tablename__ = "analysis_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String, nullable=False)
    timeframe = Column(String, nullable=False)
    sensitivity = Column(String, nullable=False, default="Medium")
    signal_mode = Column(String, nullable=False, default="Confirmed Only")
    atr_multiplier = Column(Float)
    current_atr = Column(Float)
    threshold = Column(Float)
    current_trend = Column(String)
    total_signals = Column(Integer, default=0)
    total_zones = Column(Integer, default=0)
    bars_analyzed = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), default=_utcnow)


class Watchlist(Base):
    __tablename__ = "watchlist"

    symbol = Column(String, nullable=False)
    timeframe = Column(String, nullable=False, default="1h")
    exchange = Column(String, nullable=False, default="binance")
    is_active = Column(Boolean, nullable=False, default=True)
    added_at = Column(DateTime(timezone=True), default=_utcnow)

    __table_args__ = (
        PrimaryKeyConstraint("symbol", "timeframe"),
    )


class Agent(Base):
    __tablename__ = "agents"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(50), unique=True, nullable=False)
    symbol = Column(String(20), nullable=False)
    timeframe = Column(String(10), nullable=False)
    trade_amount = Column(Float, nullable=False, default=100.0)
    balance = Column(Float, nullable=False, default=100.0)  # Current available balance
    is_active = Column(Boolean, nullable=False, default=False)
    mode = Column(String(10), nullable=False, default="paper")  # 'paper' or 'live'
    # Analysis parameters
    sensitivity = Column(String(20), nullable=False, default="Medium")
    signal_mode = Column(String(30), nullable=False, default="Confirmed Only")
    analysis_limit = Column(Integer, nullable=False, default=500)
    confirmation_bars = Column(Integer, nullable=False, default=0)
    method = Column(String(20), nullable=False, default="average")
    atr_length = Column(Integer, nullable=False, default=5)
    average_length = Column(Integer, nullable=False, default=5)
    absolute_reversal = Column(Float, nullable=False, default=0.5)
    created_at = Column(DateTime(timezone=True), default=_utcnow)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    positions = relationship("AgentPosition", back_populates="agent", cascade="all, delete-orphan")
    logs = relationship("AgentLog", back_populates="agent", cascade="all, delete-orphan")


class AgentPosition(Base):
    __tablename__ = "agent_positions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    agent_id = Column(Integer, ForeignKey("agents.id", ondelete="CASCADE"), nullable=False)
    symbol = Column(String(20), nullable=False)
    side = Column(String(5), nullable=False)  # LONG or SHORT
    entry_price = Column(Float, nullable=False)
    exit_price = Column(Float)
    stop_loss = Column(Float, nullable=False)
    original_stop_loss = Column(Float)           # Initial SL before breakeven/trailing
    take_profit = Column(Float)
    tp2 = Column(Float)                          # Second TP target for partial close
    quantity = Column(Float, nullable=False)
    original_quantity = Column(Float)             # Quantity at open before partial close
    invested_eur = Column(Float)  # EUR amount invested at open (to restore balance correctly)
    status = Column(String(10), nullable=False, default="OPEN")  # OPEN, CLOSED, STOPPED
    partial_closed = Column(Boolean, default=False)  # True after first partial TP taken
    partial_pnl = Column(Float)                      # PnL from partial close (EUR)
    best_price = Column(Float)                         # Best price reached (for trailing stop)
    entry_signal_id = Column(Integer)
    exit_signal_id = Column(Integer)
    entry_signal_time = Column(DateTime(timezone=True))      # Stable signal key (survives re-analysis)
    entry_signal_is_bullish = Column(Boolean)                 # Stable signal direction
    pnl = Column(Float)
    pnl_percent = Column(Float)
    unrealized_pnl = Column(Float)
    unrealized_pnl_percent = Column(Float)
    current_price = Column(Float)
    pnl_updated_at = Column(DateTime(timezone=True))
    opened_at = Column(DateTime(timezone=True), default=_utcnow)
    closed_at = Column(DateTime(timezone=True))

    agent = relationship("Agent", back_populates="positions")


class AgentLog(Base):
    __tablename__ = "agent_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    agent_id = Column(Integer, ForeignKey("agents.id", ondelete="CASCADE"), nullable=False)
    action = Column(String(50), nullable=False)
    details = Column(JSON)
    created_at = Column(DateTime(timezone=True), default=_utcnow)

    agent = relationship("Agent", back_populates="logs")
