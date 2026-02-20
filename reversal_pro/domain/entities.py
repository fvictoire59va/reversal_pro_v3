"""Domain entities for the Reversal Detection Pro system."""

from dataclasses import dataclass, field
from typing import List, Optional

from .enums import Direction, TrendState, ZoneType


@dataclass
class Pivot:
    """A confirmed or preview pivot point."""
    price: float
    actual_price: float
    bar_index: int
    is_high: bool
    is_preview: bool = False


@dataclass
class ReversalSignal:
    """A confirmed reversal signal (bullish or bearish)."""
    bar_index: int
    price: float
    actual_price: float
    is_bullish: bool
    is_preview: bool = False

    @property
    def label(self) -> str:
        return "REVERSAL" if not self.is_preview else "PREVIEW"

    @property
    def direction_text(self) -> str:
        return "Bullish" if self.is_bullish else "Bearish"


@dataclass
class SupplyDemandZone:
    """A supply or demand zone rectangle."""
    zone_type: ZoneType
    center_price: float
    top_price: float
    bottom_price: float
    start_bar: int
    end_bar: int


@dataclass
class TrendInfo:
    """Current EMA trend information."""
    state: TrendState
    ema_fast: float     # EMA 9
    ema_mid: float      # EMA 14
    ema_slow: float     # EMA 21
    buy_signal: bool = False
    sell_signal: bool = False
    trend_changed_to_bullish: bool = False
    trend_changed_to_bearish: bool = False


@dataclass
class ZigZagState:
    """Holds the running state of the zigzag algorithm."""
    zhigh: Optional[float] = None
    zlow: Optional[float] = None
    zhigh_actual: Optional[float] = None
    zlow_actual: Optional[float] = None
    zhigh_bar: int = 0
    zlow_bar: int = 0
    direction: Direction = Direction.NONE


@dataclass
class SignalState:
    """Holds the running state of signal detection."""
    eil: Optional[float] = None       # Extreme Inflection Low
    eih: Optional[float] = None       # Extreme Inflection High
    eil_actual: Optional[float] = None
    eih_actual: Optional[float] = None
    eil_bar: int = 0
    eih_bar: int = 0
    dir: int = 0
    signal: int = 0
    prev_signal: int = 0


@dataclass
class EMAState:
    """Holds the running state for EMA trend detection."""
    buy_signal: int = 0
    sell_signal: int = 0
    prev_buy: bool = False
    prev_sell: bool = False
    prev_buy_signal: int = 0
    prev_sell_signal: int = 0


@dataclass
class AnalysisResult:
    """Complete result of analyzing a set of bars."""
    signals: List[ReversalSignal] = field(default_factory=list)
    pivots: List[Pivot] = field(default_factory=list)
    zones: List[SupplyDemandZone] = field(default_factory=list)
    trend_history: List[TrendInfo] = field(default_factory=list)
    current_trend: Optional[TrendInfo] = None
    current_atr: float = 0.0
    current_threshold: float = 0.0
    atr_multiplier: float = 0.0
