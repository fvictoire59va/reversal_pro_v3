"""
Configuration / Settings for Reversal Detection Pro.
Centralizes all default parameters matching the Pine Script inputs.
"""

from dataclasses import dataclass, field
from typing import Optional

from ..domain.enums import (
    SignalMode,
    SensitivityPreset,
    CalculationMethod,
    SupplyDemandDisplay,
)


@dataclass
class SignalSettings:
    """Signal confirmation settings."""
    mode: SignalMode = SignalMode.CONFIRMED_ONLY
    confirmation_bars: int = 0  # 0–5 extra bars


@dataclass
class SensitivitySettings:
    """Sensitivity / ATR settings."""
    preset: SensitivityPreset = SensitivityPreset.MEDIUM
    # Custom overrides (used only when preset == CUSTOM)
    custom_atr_multiplier: float = 2.0
    custom_percent_threshold: float = 0.01


@dataclass
class AdvancedSettings:
    """Advanced calculation settings."""
    calculation_method: CalculationMethod = CalculationMethod.AVERAGE
    percent_reversal: float = 0.01
    absolute_reversal: float = 0.05
    atr_length: int = 5
    average_length: int = 5


@dataclass
class ZoneSettings:
    """Supply / Demand zone settings."""
    display: SupplyDemandDisplay = SupplyDemandDisplay.PIVOT
    num_zones: int = 3
    show_cloud: bool = False
    zone_extension_bars: int = 20
    zone_thickness_pct: float = 0.02  # % of price


@dataclass
class LabelSettings:
    """Label and line display settings."""
    line_extension: int = 5
    max_lines: int = 10
    label_size: str = "Normal"  # Small, Normal, Large


@dataclass
class TableSettings:
    """Info table display settings."""
    show: bool = True
    position: str = "Top Right"
    size: str = "Normal"  # Tiny, Small, Normal, Large, Huge


@dataclass
class EMASettings:
    """Triple EMA parameters."""
    superfast_length: int = 9
    fast_length: int = 14
    slow_length: int = 21


@dataclass
class DataSettings:
    """Data source configuration."""
    source: str = "csv"           # "csv" or "ccxt"
    file_path: str = ""           # CSV file path
    exchange: str = "binance"     # ccxt exchange id
    symbol: str = "BTC/USDT"
    timeframe: str = "1h"
    limit: int = 500
    api_key: str = ""
    secret: str = ""


@dataclass
class AppConfig:
    """Root configuration — aggregates all settings."""
    signal: SignalSettings = field(default_factory=SignalSettings)
    sensitivity: SensitivitySettings = field(default_factory=SensitivitySettings)
    advanced: AdvancedSettings = field(default_factory=AdvancedSettings)
    zones: ZoneSettings = field(default_factory=ZoneSettings)
    labels: LabelSettings = field(default_factory=LabelSettings)
    table: TableSettings = field(default_factory=TableSettings)
    ema: EMASettings = field(default_factory=EMASettings)
    data: DataSettings = field(default_factory=DataSettings)

    # Output options
    show_chart: bool = True
    save_chart: str = ""         # path to save chart image
    save_signals: bool = False   # save signals to JSON
    output_dir: str = "output"
