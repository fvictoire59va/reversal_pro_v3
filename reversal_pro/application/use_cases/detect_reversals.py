"""
Use case: Detect Reversals
Orchestrates all services to produce a complete AnalysisResult.
"""

import numpy as np
from typing import List, Optional

from ...domain.entities import AnalysisResult, ReversalSignal, Pivot, SupplyDemandZone
from ...domain.enums import (
    SignalMode,
    SensitivityPreset,
    CalculationMethod,
)
from ...domain.value_objects import SensitivityConfig, OHLCVBar

from ..services.atr_service import ATRService
from ..services.ema_service import EMAService
from ..services.zigzag_service import ZigZagService
from ..services.reversal_detector import ReversalDetector
from ..services.supply_demand_service import SupplyDemandService


class DetectReversalsUseCase:
    """
    Main use case: analyze OHLCV bars and return signals, pivots, zones, trend.

    This orchestrates the entire pipeline:
    1. Compute ATR
    2. Compute reversal thresholds
    3. Run zigzag to find pivots
    4. Detect reversal signals from pivots
    5. Generate supply/demand zones
    6. Compute EMA trend
    """

    def __init__(
        self,
        signal_mode: SignalMode = SignalMode.CONFIRMED_ONLY,
        sensitivity: SensitivityPreset = SensitivityPreset.MEDIUM,
        custom_config: Optional[SensitivityConfig] = None,
        calculation_method: CalculationMethod = CalculationMethod.AVERAGE,
        atr_length: int = 5,
        average_length: int = 5,
        confirmation_bars: int = 0,
        absolute_reversal: float = 0.05,
        # Zone params
        zone_thickness_pct: float = 0.02,
        zone_extension_bars: int = 20,
        max_zones: int = 3,
        generate_zones: bool = False,
        # EMA params
        ema_fast: int = 9,
        ema_mid: int = 14,
        ema_slow: int = 21,
        # Timeframe for ATR scaling
        timeframe: str = "1h",
    ):
        # Resolve sensitivity config (with timeframe-adaptive ATR scaling)
        if sensitivity == SensitivityPreset.CUSTOM:
            if custom_config:
                self.sensitivity_config = custom_config
            else:
                # Fallback to Medium when CUSTOM is requested without a config
                self.sensitivity_config = SensitivityConfig.from_preset(
                    SensitivityPreset.MEDIUM, timeframe=timeframe
                )
        else:
            self.sensitivity_config = SensitivityConfig.from_preset(sensitivity, timeframe=timeframe)

        self.signal_mode = signal_mode
        self.calculation_method = calculation_method
        self.atr_length = atr_length
        self.average_length = average_length
        self.confirmation_bars = confirmation_bars
        self.absolute_reversal = absolute_reversal
        self.ema_fast = ema_fast
        self.ema_mid = ema_mid
        self.ema_slow = ema_slow
        self.generate_zones_flag = generate_zones

        # Services
        self.atr_service = ATRService()
        self.ema_service = EMAService()
        self.zigzag_service = ZigZagService(
            use_ema=(calculation_method == CalculationMethod.AVERAGE),
            ema_length=average_length,
            confirmation_bars=confirmation_bars,
        )
        self.reversal_detector = ReversalDetector()
        self.supply_demand_service = SupplyDemandService(
            zone_thickness_pct=zone_thickness_pct,
            zone_extension_bars=zone_extension_bars,
            max_zones=max_zones,
        )

    def execute(self, bars: List[OHLCVBar]) -> AnalysisResult:
        """
        Run the full analysis pipeline on a list of OHLCV bars.

        Parameters
        ----------
        bars : list of OHLCVBar, ordered chronologically

        Returns
        -------
        AnalysisResult with signals, pivots, zones, trend
        """
        if not bars:
            return AnalysisResult()

        n = len(bars)
        highs = np.array([b.high for b in bars], dtype=float)
        lows = np.array([b.low for b in bars], dtype=float)
        closes = np.array([b.close for b in bars], dtype=float)

        # ── Step 1: ATR ──────────────────────────────────────────────
        atr_values = self.atr_service.atr(
            highs, lows, closes, self.atr_length
        )

        # ── Step 2: Reversal thresholds ──────────────────────────────
        reversal_amounts = np.array([
            ATRService.compute_reversal_threshold(
                close=closes[i],
                percent_threshold=self.sensitivity_config.percent_threshold,
                absolute_reversal=self.absolute_reversal,
                atr_multiplier=self.sensitivity_config.atr_multiplier,
                atr_value=atr_values[i] if not np.isnan(atr_values[i]) else 0.0,
            )
            for i in range(n)
        ])

        # ── Step 3: ZigZag pivots ────────────────────────────────────
        confirmed_pivots: List[Pivot] = []
        preview_pivots: List[Pivot] = []

        if self.signal_mode != SignalMode.PREVIEW_ONLY:
            confirmed_pivots = self.zigzag_service.compute_pivots(
                highs, lows, reversal_amounts
            )

        if self.signal_mode != SignalMode.CONFIRMED_ONLY:
            preview_pivots = self.zigzag_service.compute_preview_pivots(
                highs, lows, reversal_amounts
            )

        all_pivots = confirmed_pivots + preview_pivots

        # ── Step 4: Reversal signals ─────────────────────────────────
        # Prepare confirmed prices for signal detection
        use_ema = self.calculation_method == CalculationMethod.AVERAGE
        if use_ema:
            price_h = ZigZagService._ema(highs, self.average_length)
            price_l = ZigZagService._ema(lows, self.average_length)
        else:
            price_h = highs.copy()
            price_l = lows.copy()

        # Apply confirmation bar delay
        if self.confirmation_bars > 0:
            cb = self.confirmation_bars
            ph_conf = np.full(n, np.nan)
            pl_conf = np.full(n, np.nan)
            ph_conf[cb:] = price_h[:n - cb]
            pl_conf[cb:] = price_l[:n - cb]
        else:
            ph_conf = price_h
            pl_conf = price_l

        confirmed_signals = self.reversal_detector.detect(
            confirmed_pivots, n, ph_conf, pl_conf
        )

        # Preview signals (using non-delayed prices)
        preview_signals = []
        if self.signal_mode != SignalMode.CONFIRMED_ONLY and preview_pivots:
            preview_signals = self.reversal_detector.detect(
                preview_pivots, n, price_h, price_l
            )
            for s in preview_signals:
                s.is_preview = True

        all_signals = confirmed_signals + preview_signals

        # ── Step 5: Supply/Demand zones ──────────────────────────────
        zones: List[SupplyDemandZone] = []
        if self.generate_zones_flag:
            zones = self.supply_demand_service.generate_zones(confirmed_pivots)

        # ── Step 6: EMA trend ────────────────────────────────────────
        trends, ema_state = self.ema_service.compute_trend(
            closes, highs, lows,
            self.ema_fast, self.ema_mid, self.ema_slow,
        )

        # ── Build result ─────────────────────────────────────────────
        last_atr = atr_values[-1] if not np.isnan(atr_values[-1]) else 0.0
        last_threshold = reversal_amounts[-1]

        return AnalysisResult(
            signals=all_signals,
            pivots=all_pivots,
            zones=zones,
            trend_history=trends,
            current_trend=trends[-1] if trends else None,
            current_atr=last_atr,
            current_threshold=last_threshold,
            atr_multiplier=self.sensitivity_config.atr_multiplier,
        )
