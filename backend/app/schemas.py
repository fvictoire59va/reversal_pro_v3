"""Pydantic schemas for API request/response validation."""

from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, Field


# ── OHLCV ───────────────────────────────────────────────────────
class OHLCVBar(BaseModel):
    time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


class OHLCVResponse(BaseModel):
    symbol: str
    timeframe: str
    bars: List[OHLCVBar]
    count: int


# ── Indicators ──────────────────────────────────────────────────
class IndicatorBar(BaseModel):
    time: datetime
    ema_9: Optional[float] = None
    ema_14: Optional[float] = None
    ema_21: Optional[float] = None
    atr: Optional[float] = None
    trend: Optional[str] = None


# ── Signals ─────────────────────────────────────────────────────
class SignalResponse(BaseModel):
    time: datetime
    bar_index: int
    price: float
    actual_price: float
    is_bullish: bool
    is_preview: bool = False
    label: str = "REVERSAL"


# ── Zones ───────────────────────────────────────────────────────
class ZoneResponse(BaseModel):
    zone_type: str
    center_price: float
    top_price: float
    bottom_price: float
    start_bar: int
    end_bar: int


# ── Analysis ────────────────────────────────────────────────────
class AnalysisRequest(BaseModel):
    symbol: str = "BTC/USDT"
    timeframe: str = "1h"
    limit: int = Field(default=500, ge=50, le=5000)
    sensitivity: str = "Medium"
    signal_mode: str = "Confirmed Only"
    confirmation_bars: int = Field(default=0, ge=0, le=5)
    method: str = "average"
    atr_length: int = Field(default=5, ge=1, le=50)
    average_length: int = Field(default=5, ge=1, le=50)
    show_zones: bool = True


class AnalysisResponse(BaseModel):
    symbol: str
    timeframe: str
    sensitivity: str
    signal_mode: str
    atr_multiplier: float
    current_atr: float
    threshold: float
    current_trend: Optional[str] = None
    bars: List[OHLCVBar]
    indicators: List[IndicatorBar]
    signals: List[SignalResponse]
    zones: List[ZoneResponse]
    total_signals: int
    total_zones: int
    bars_analyzed: int
    analyzed_at: datetime


# ── Chart Data (lightweight-charts format) ──────────────────────
class CandlestickData(BaseModel):
    """TradingView lightweight-charts candlestick format."""
    time: int  # Unix timestamp in seconds
    open: float
    high: float
    low: float
    close: float


class LineData(BaseModel):
    """TradingView lightweight-charts line format."""
    time: int
    value: float


class MarkerData(BaseModel):
    """TradingView lightweight-charts marker format."""
    time: int
    position: str  # "aboveBar" or "belowBar"
    color: str
    shape: str  # "arrowUp", "arrowDown", "circle"
    text: str
    size: int = 2


class ChartDataResponse(BaseModel):
    """Complete data set formatted for TradingView lightweight-charts."""
    symbol: str
    timeframe: str
    candles: List[CandlestickData]
    ema_9: List[LineData]
    ema_14: List[LineData]
    ema_21: List[LineData]
    markers: List[MarkerData]
    zones: List[ZoneResponse]
    current_trend: Optional[str] = None
    current_atr: float = 0.0
    threshold: float = 0.0
    atr_multiplier: float = 0.0


# ── Watchlist ───────────────────────────────────────────────────
class WatchlistItem(BaseModel):
    symbol: str
    timeframe: str = "1h"
    exchange: str = "binance"
    is_active: bool = True


class WatchlistResponse(BaseModel):
    items: List[WatchlistItem]


# ── Agent Broker ────────────────────────────────────────────
class AgentCreate(BaseModel):
    symbol: str = "BTC/USDT"
    timeframe: str = "1h"
    trade_amount: float = Field(default=100.0, gt=0)
    mode: str = Field(default="paper", pattern="^(paper|live)$")
    sensitivity: str = "Medium"
    signal_mode: str = "Confirmed Only"
    analysis_limit: int = Field(default=500, ge=50, le=5000)


class AgentUpdate(BaseModel):
    trade_amount: Optional[float] = None
    mode: Optional[str] = None
    sensitivity: Optional[str] = None
    signal_mode: Optional[str] = None
    analysis_limit: Optional[int] = None


class AgentResponse(BaseModel):
    id: int
    name: str
    symbol: str
    timeframe: str
    trade_amount: float
    balance: float
    is_active: bool
    mode: str
    sensitivity: str = "Medium"
    signal_mode: str = "Confirmed Only"
    analysis_limit: int = 500
    created_at: datetime
    updated_at: datetime
    open_positions: int = 0
    total_pnl: float = 0.0
    total_unrealized_pnl: float = 0.0

    class Config:
        from_attributes = True


class PositionResponse(BaseModel):
    id: int
    agent_id: int
    agent_name: str = ""
    symbol: str
    side: str
    entry_price: float
    exit_price: Optional[float] = None
    stop_loss: float
    take_profit: Optional[float] = None
    quantity: float
    status: str
    pnl: Optional[float] = None
    pnl_percent: Optional[float] = None
    unrealized_pnl: Optional[float] = None
    unrealized_pnl_percent: Optional[float] = None
    current_price: Optional[float] = None
    pnl_updated_at: Optional[datetime] = None
    opened_at: datetime
    closed_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class AgentLogResponse(BaseModel):
    id: int
    agent_id: int
    action: str
    details: Optional[dict] = None
    created_at: datetime

    class Config:
        from_attributes = True


class AgentsOverview(BaseModel):
    agents: List[AgentResponse]
    open_positions: List[PositionResponse]
    total_agents: int
    active_agents: int
    total_open_positions: int
    total_realized_pnl: float
