"""Domain enums for the Reversal Detection Pro system."""

from enum import Enum


class Direction(Enum):
    """ZigZag direction."""
    UP = 1
    DOWN = -1
    NONE = 0


class TrendState(Enum):
    """Triple EMA trend state."""
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    NEUTRAL = "NEUTRAL"


class SignalMode(Enum):
    """Reversal signal confirmation mode."""
    CONFIRMED_ONLY = "Confirmed Only"
    CONFIRMED_PREVIEW = "Confirmed + Preview"
    PREVIEW_ONLY = "Preview Only"


class SensitivityPreset(Enum):
    """ATR-based sensitivity preset."""
    VERY_HIGH = "Very High"
    HIGH = "High"
    MEDIUM = "Medium"
    LOW = "Low"
    VERY_LOW = "Very Low"
    CUSTOM = "Custom"


class CalculationMethod(Enum):
    """Price calculation method for zigzag."""
    AVERAGE = "average"
    HIGH_LOW = "high_low"


class SupplyDemandDisplay(Enum):
    """Supply/Demand zone display mode."""
    PIVOT = "Pivot"
    ARROW = "Arrow"
    NONE = "None"


class ZoneType(Enum):
    """Supply or Demand zone."""
    SUPPLY = "SUPPLY"
    DEMAND = "DEMAND"
