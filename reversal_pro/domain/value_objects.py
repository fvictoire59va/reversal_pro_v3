"""Value objects for the Reversal Detection Pro system."""

from dataclasses import dataclass
from typing import Optional

from .enums import SensitivityPreset


# ============================================================================
# Sensitivity Configuration
# ============================================================================

# Preset mappings
SENSITIVITY_ATR_MULTIPLIERS = {
    SensitivityPreset.VERY_HIGH: 0.8,
    SensitivityPreset.HIGH: 1.2,
    SensitivityPreset.MEDIUM: 2.0,
    SensitivityPreset.LOW: 2.8,
    SensitivityPreset.VERY_LOW: 3.5,
}

SENSITIVITY_PERCENT_THRESHOLDS = {
    SensitivityPreset.VERY_HIGH: 0.005,
    SensitivityPreset.HIGH: 0.008,
    SensitivityPreset.MEDIUM: 0.01,
    SensitivityPreset.LOW: 0.015,
    SensitivityPreset.VERY_LOW: 0.02,
}


# Timeframe-based ATR multiplier scaling factors.
# Lower timeframes need lower multipliers because their ATR is small
# relative to the price movement needed for a meaningful reversal.
# The base multiplier (from the sensitivity preset) is multiplied by this factor.
TIMEFRAME_ATR_SCALE = {
    "1m":  0.40,   # Very fast — reduce threshold aggressively
    "3m":  0.50,
    "5m":  0.60,
    "15m": 0.75,
    "30m": 0.85,
    "1h":  1.00,   # Reference timeframe — no scaling
    "2h":  1.10,
    "4h":  1.20,
    "6h":  1.30,
    "8h":  1.35,
    "12h": 1.40,
    "1d":  1.50,
    "3d":  1.60,
    "1w":  1.70,
    "1M":  1.80,
}


@dataclass(frozen=True)
class SensitivityConfig:
    """Resolved sensitivity parameters."""
    atr_multiplier: float
    percent_threshold: float

    @classmethod
    def from_preset(
        cls, preset: SensitivityPreset, timeframe: str = "1h"
    ) -> "SensitivityConfig":
        if preset == SensitivityPreset.CUSTOM:
            raise ValueError("Use from_custom() for custom presets")
        base_mult = SENSITIVITY_ATR_MULTIPLIERS[preset]
        scale = TIMEFRAME_ATR_SCALE.get(timeframe, 1.0)
        return cls(
            atr_multiplier=round(base_mult * scale, 4),
            percent_threshold=SENSITIVITY_PERCENT_THRESHOLDS[preset],
        )

    @classmethod
    def from_custom(
        cls, atr_multiplier: float, percent_threshold: float
    ) -> "SensitivityConfig":
        return cls(
            atr_multiplier=atr_multiplier,
            percent_threshold=percent_threshold,
        )


@dataclass(frozen=True)
class OHLCVBar:
    """Single OHLCV price bar."""
    timestamp: object  # datetime, str, or numeric timestamp
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0



