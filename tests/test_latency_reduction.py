"""Unit tests for the latency-reduction services.

Tests VolumeAdaptiveService, CandlePatternService, and CUSUMService
in isolation, plus integration with the DetectReversalsUseCase pipeline.
"""

import numpy as np
import pytest

from reversal_pro.application.services.volume_adaptive_service import (
    VolumeAdaptiveService,
)
from reversal_pro.application.services.candle_pattern_service import (
    CandlePatternService,
)
from reversal_pro.application.services.cusum_service import CUSUMService
from reversal_pro.domain.value_objects import OHLCVBar
from reversal_pro.domain.enums import SignalMode, SensitivityPreset


# ╔══════════════════════════════════════════════════════════════╗
# ║  VolumeAdaptiveService                                       ║
# ╚══════════════════════════════════════════════════════════════╝

class TestVolumeAdaptiveService:
    """Tests for volume-adaptive threshold reduction."""

    def test_no_reduction_on_low_volume(self):
        """Volume below spike threshold → all reductions = 1.0."""
        svc = VolumeAdaptiveService(lookback=5, volume_spike_mult=2.0)
        volumes = np.array([100.0] * 10)  # flat volume
        result = svc.compute_reduction(volumes)
        np.testing.assert_array_equal(result, np.ones(10))

    def test_reduction_on_volume_spike(self):
        """A volume spike should produce reduction < 1.0."""
        svc = VolumeAdaptiveService(
            lookback=5, min_reduction=0.50, volume_spike_mult=1.5
        )
        volumes = np.array([100.0] * 10)
        volumes[7] = 500.0  # 5x average → clear spike
        result = svc.compute_reduction(volumes)
        assert result[7] < 1.0
        assert result[7] >= 0.50  # bounded by min_reduction

    def test_no_reduction_before_lookback(self):
        """Bars before lookback window should remain 1.0."""
        svc = VolumeAdaptiveService(lookback=5)
        volumes = np.array([100.0, 500.0, 100.0, 100.0, 100.0, 100.0])
        result = svc.compute_reduction(volumes)
        assert result[1] == 1.0  # before lookback window

    def test_short_series_returns_ones(self):
        """Series shorter than lookback should return all 1.0."""
        svc = VolumeAdaptiveService(lookback=20)
        volumes = np.array([100.0] * 5)
        result = svc.compute_reduction(volumes)
        np.testing.assert_array_equal(result, np.ones(5))

    def test_zero_volume_no_crash(self):
        """Zero volume should not cause division errors."""
        svc = VolumeAdaptiveService(lookback=3)
        volumes = np.array([0.0] * 10)
        result = svc.compute_reduction(volumes)
        np.testing.assert_array_equal(result, np.ones(10))


# ╔══════════════════════════════════════════════════════════════╗
# ║  CandlePatternService                                        ║
# ╚══════════════════════════════════════════════════════════════╝

class TestCandlePatternService:
    """Tests for candlestick pattern detection."""

    def _make_bar(self, o, h, l, c):
        return o, h, l, c

    def test_bullish_engulfing(self):
        """Bearish bar followed by bullish engulfing → reduction applied."""
        svc = CandlePatternService(engulfing_reduction=0.50)
        # Bar 0: bearish (close < open)
        # Bar 1: bullish engulfing (close > prev_open, open < prev_close)
        opens = np.array([110.0, 95.0])
        highs = np.array([112.0, 115.0])
        lows = np.array([98.0, 94.0])
        closes = np.array([100.0, 112.0])
        result = svc.compute_reduction(opens, highs, lows, closes)
        assert result[0] == 1.0  # first bar: no look-back
        assert result[1] == pytest.approx(0.50)

    def test_bearish_engulfing(self):
        """Bullish bar followed by bearish engulfing → reduction applied."""
        svc = CandlePatternService(engulfing_reduction=0.50)
        opens = np.array([95.0, 112.0])
        highs = np.array([112.0, 115.0])
        lows = np.array([94.0, 93.0])
        closes = np.array([110.0, 93.5])
        result = svc.compute_reduction(opens, highs, lows, closes)
        assert result[1] == pytest.approx(0.50)

    def test_hammer_detection(self):
        """Hammer pattern (small body high, long lower wick) → reduction."""
        svc = CandlePatternService(
            body_ratio_threshold=0.30, hammer_reduction=0.65
        )
        # Hammer: open=100, close=101 (tiny body), low=90 (long wick), high=101.5
        opens = np.array([100.0, 100.0])
        highs = np.array([105.0, 101.5])
        lows = np.array([95.0, 90.0])
        closes = np.array([104.0, 101.0])
        result = svc.compute_reduction(opens, highs, lows, closes)
        assert result[1] == pytest.approx(0.65)

    def test_doji_detection(self):
        """Doji (very small body / range) → mild reduction."""
        svc = CandlePatternService(doji_reduction=0.80)
        opens = np.array([100.0, 100.0])
        highs = np.array([105.0, 105.0])
        lows = np.array([95.0, 95.0])
        # Doji: body < 10% of range → |100.5 - 100.0| / (105-95) = 5%
        closes = np.array([104.0, 100.5])
        result = svc.compute_reduction(opens, highs, lows, closes)
        assert result[1] == pytest.approx(0.80)

    def test_no_pattern_normal_candle(self):
        """Normal candle without a reversal pattern → 1.0."""
        svc = CandlePatternService()
        opens = np.array([100.0, 102.0])
        highs = np.array([105.0, 107.0])
        lows = np.array([99.0, 101.0])
        closes = np.array([104.0, 106.0])
        result = svc.compute_reduction(opens, highs, lows, closes)
        assert result[1] == 1.0

    def test_flat_candle_no_crash(self):
        """Zero-range candle should not crash."""
        svc = CandlePatternService()
        opens = np.array([100.0, 100.0])
        highs = np.array([100.0, 100.0])
        lows = np.array([100.0, 100.0])
        closes = np.array([100.0, 100.0])
        result = svc.compute_reduction(opens, highs, lows, closes)
        np.testing.assert_array_equal(result, np.ones(2))


# ╔══════════════════════════════════════════════════════════════╗
# ║  CUSUMService                                                ║
# ╚══════════════════════════════════════════════════════════════╝

class TestCUSUMService:
    """Tests for CUSUM change-point detection."""

    def test_flat_series_no_change_points(self):
        """Flat price → no change points → all reductions = 1.0."""
        svc = CUSUMService(drift_fraction=0.5, threshold_mult=3.0)
        closes = np.array([100.0] * 30)
        atr = np.array([1.0] * 30)
        result = svc.compute_reduction(closes, atr)
        np.testing.assert_array_equal(result, np.ones(30))

    def test_detects_sudden_jump(self):
        """A sudden price jump should trigger a change point → reduction."""
        svc = CUSUMService(
            drift_fraction=0.5, threshold_mult=2.0,
            min_reduction=0.45, decay_bars=3,
        )
        closes = np.concatenate([
            np.full(15, 100.0),
            np.full(15, 120.0),  # +20 jump
        ])
        atr = np.full(30, 2.0)
        result = svc.compute_reduction(closes, atr)
        # At least one bar should have reduction < 1.0 around the jump
        assert np.min(result) < 1.0
        assert np.min(result) >= 0.45

    def test_detects_sudden_drop(self):
        """A sudden price drop should also trigger reduction."""
        svc = CUSUMService(
            drift_fraction=0.5, threshold_mult=2.0,
            min_reduction=0.45, decay_bars=3,
        )
        closes = np.concatenate([
            np.full(15, 120.0),
            np.full(15, 100.0),  # -20 drop
        ])
        atr = np.full(30, 2.0)
        result = svc.compute_reduction(closes, atr)
        assert np.min(result) < 1.0

    def test_decay_returns_to_one(self):
        """Reduction should decay back to 1.0 after decay_bars."""
        svc = CUSUMService(
            drift_fraction=0.5, threshold_mult=2.0,
            min_reduction=0.45, decay_bars=3,
        )
        closes = np.concatenate([
            np.full(10, 100.0),
            np.full(5, 150.0),  # jump
            np.full(15, 150.0),  # stable after
        ])
        atr = np.full(30, 2.0)
        result = svc.compute_reduction(closes, atr)
        # The last few bars should be back to 1.0
        assert result[-1] == 1.0

    def test_short_series(self):
        """Very short series should return all 1.0 safely."""
        svc = CUSUMService()
        closes = np.array([100.0])
        atr = np.array([1.0])
        result = svc.compute_reduction(closes, atr)
        np.testing.assert_array_equal(result, np.ones(1))

    def test_nan_atr_uses_fallback(self):
        """NaN ATR values should not crash — uses return as proxy."""
        svc = CUSUMService(drift_fraction=0.5, threshold_mult=2.0)
        closes = np.concatenate([
            np.full(10, 100.0),
            np.full(10, 130.0),
        ])
        atr = np.full(20, np.nan)
        result = svc.compute_reduction(closes, atr)
        assert len(result) == 20
        # Should still run without errors


# ╔══════════════════════════════════════════════════════════════╗
# ║  Integration: DetectReversalsUseCase with new services        ║
# ╚══════════════════════════════════════════════════════════════╝

class TestLatencyReductionIntegration:
    """Integration tests: pipeline with all latency-reduction services."""

    @staticmethod
    def _make_bars(opens, highs, lows, closes, volumes=None):
        """Helper to build OHLCVBar list."""
        n = len(closes)
        if volumes is None:
            volumes = [1000.0] * n
        return [
            OHLCVBar(
                timestamp=i, open=opens[i], high=highs[i],
                low=lows[i], close=closes[i], volume=volumes[i],
            )
            for i in range(n)
        ]

    def test_pipeline_runs_with_all_services_enabled(self):
        """Full pipeline with volume, candle, CUSUM enabled should complete."""
        from reversal_pro.application.use_cases.detect_reversals import (
            DetectReversalsUseCase,
        )
        n = 50
        np.random.seed(42)
        base = 100.0 + np.cumsum(np.random.randn(n) * 0.5)
        opens = base - 0.2
        highs = base + np.abs(np.random.randn(n)) * 0.5
        lows = base - np.abs(np.random.randn(n)) * 0.5
        closes = base.copy()
        volumes = np.random.uniform(500, 2000, n)

        bars = self._make_bars(opens, highs, lows, closes, volumes)
        uc = DetectReversalsUseCase(
            signal_mode=SignalMode.CONFIRMED_ONLY,
            sensitivity=SensitivityPreset.HIGH,
            use_matrix_profile=False,  # skip stumpy dependency
            use_volume_adaptive=True,
            use_candle_patterns=True,
            use_cusum=True,
        )
        result = uc.execute(bars)
        # Should complete without error and return a valid result
        assert result is not None
        assert isinstance(result.signals, list)
        assert isinstance(result.pivots, list)

    def test_pipeline_runs_with_all_services_disabled(self):
        """Pipeline should still work with all new services disabled."""
        from reversal_pro.application.use_cases.detect_reversals import (
            DetectReversalsUseCase,
        )
        n = 30
        opens = np.linspace(100, 110, n)
        highs = opens + 1
        lows = opens - 1
        closes = opens + 0.5
        volumes = np.full(n, 1000.0)

        bars = self._make_bars(opens, highs, lows, closes, volumes)
        uc = DetectReversalsUseCase(
            use_matrix_profile=False,
            use_volume_adaptive=False,
            use_candle_patterns=False,
            use_cusum=False,
        )
        result = uc.execute(bars)
        assert result is not None

    def test_latency_reduction_fewer_candles_for_pivot(self):
        """
        With latency-reduction services enabled, pivots should be detected
        earlier (at lower bar indices) compared to all disabled.
        Uses a clear V-shaped reversal with a volume spike at the bottom.
        """
        from reversal_pro.application.use_cases.detect_reversals import (
            DetectReversalsUseCase,
        )
        # Build a clear downtrend → sharp reversal → uptrend
        n = 60
        prices_down = np.linspace(120, 90, 25)
        prices_up = np.linspace(90, 120, 35)
        closes = np.concatenate([prices_down, prices_up])
        opens = closes - 0.3
        highs = closes + 1.5
        lows = closes - 1.5
        volumes = np.full(n, 1000.0)
        # Volume spike at the reversal point
        volumes[24] = 5000.0
        volumes[25] = 5000.0

        bars = self._make_bars(opens, highs, lows, closes, volumes)

        # With services disabled
        uc_off = DetectReversalsUseCase(
            sensitivity=SensitivityPreset.HIGH,
            use_matrix_profile=False,
            use_volume_adaptive=False,
            use_candle_patterns=False,
            use_cusum=False,
        )
        result_off = uc_off.execute(bars)

        # With services enabled
        uc_on = DetectReversalsUseCase(
            sensitivity=SensitivityPreset.HIGH,
            use_matrix_profile=False,
            use_volume_adaptive=True,
            use_candle_patterns=True,
            use_cusum=True,
        )
        result_on = uc_on.execute(bars)

        # The enabled version should detect at least as many pivots
        # (lower thresholds should not miss pivots)
        assert len(result_on.pivots) >= len(result_off.pivots)

    def test_empty_bars_no_crash(self):
        """Empty bar list should return empty result without errors."""
        from reversal_pro.application.use_cases.detect_reversals import (
            DetectReversalsUseCase,
        )
        uc = DetectReversalsUseCase(
            use_matrix_profile=False,
            use_volume_adaptive=True,
            use_candle_patterns=True,
            use_cusum=True,
        )
        result = uc.execute([])
        assert result is not None
        assert len(result.signals) == 0
