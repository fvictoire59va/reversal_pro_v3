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


@dataclass(frozen=True)
class SensitivityConfig:
    """Resolved sensitivity parameters."""
    atr_multiplier: float
    percent_threshold: float

    @classmethod
    def from_preset(cls, preset: SensitivityPreset) -> "SensitivityConfig":
        if preset == SensitivityPreset.CUSTOM:
            raise ValueError("Use from_custom() for custom presets")
        return cls(
            atr_multiplier=SENSITIVITY_ATR_MULTIPLIERS[preset],
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
    timestamp: object  # datetime or any timestamp
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


@dataclass(frozen=True)
class PriceLevel:
    """A price level with bar index."""
    price: float
    bar_index: int
    actual_price: Optional[float] = None  # raw high/low vs smoothed
