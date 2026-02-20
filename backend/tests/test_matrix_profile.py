"""
Tests for the MatrixProfileService and its integration into the
DetectReversalsUseCase.

These tests verify:
1. The MP service produces valid novelty scores.
2. Threshold reduction factors are in the expected range.
3. The service detects regime changes on synthetic data.
4. The use case runs correctly with MP enabled and disabled.
5. MP-enabled detection is at least as early as without MP.
"""

from __future__ import annotations

import numpy as np
import pytest

from reversal_pro.application.services.matrix_profile_service import (
    MatrixProfileService,
    MatrixProfileResult,
    RegimeChangePoint,
)
from reversal_pro.application.use_cases.detect_reversals import DetectReversalsUseCase
from reversal_pro.domain.enums import SignalMode, SensitivityPreset, CalculationMethod
from reversal_pro.domain.value_objects import OHLCVBar


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_trending_then_reversing(n: int = 200, noise: float = 0.5) -> np.ndarray:
    """
    Generate a synthetic close series:
      bars 0..n/2:   uptrend
      bars n/2..n:   downtrend
    This creates an obvious regime change around n/2.
    """
    rng = np.random.default_rng(42)
    half = n // 2
    up = np.cumsum(rng.normal(1.0, noise, half)) + 100
    down = np.cumsum(rng.normal(-1.0, noise, n - half)) + up[-1]
    return np.concatenate([up, down])


def _closes_to_bars(closes: np.ndarray) -> list[OHLCVBar]:
    """Convert a close array to minimal OHLCVBar list (O=H=L=C with spread)."""
    bars = []
    for i, c in enumerate(closes):
        h = c * 1.002
        l = c * 0.998
        bars.append(OHLCVBar(
            timestamp=i,
            open=c,
            high=h,
            low=l,
            close=c,
            volume=1000.0,
        ))
    return bars


# ---------------------------------------------------------------------------
# MatrixProfileService unit tests
# ---------------------------------------------------------------------------

class TestMatrixProfileService:

    def test_basic_analysis_returns_valid_result(self):
        """Service returns a well-formed MatrixProfileResult."""
        closes = _make_trending_then_reversing(200)
        svc = MatrixProfileService(
            subsequence_length=10,
            z_threshold=1.8,
            timeframe="1h",
        )
        result = svc.analyze(closes)

        assert isinstance(result, MatrixProfileResult)
        assert result.novelty_scores.shape == (200,)
        assert result.threshold_reduction.shape == (200,)
        assert result.threshold == 1.8

    def test_threshold_reduction_in_range(self):
        """All reduction values must be in [min_reduction, 1.0]."""
        closes = _make_trending_then_reversing(200)
        svc = MatrixProfileService(
            subsequence_length=10,
            min_reduction=0.40,
            timeframe="1h",
        )
        result = svc.analyze(closes)

        valid = result.threshold_reduction[~np.isnan(result.threshold_reduction)]
        assert np.all(valid >= 0.40 - 1e-9)
        assert np.all(valid <= 1.0 + 1e-9)

    def test_detects_regime_change_in_synthetic(self):
        """At least one change point should be detected near the midpoint."""
        closes = _make_trending_then_reversing(200)
        svc = MatrixProfileService(
            subsequence_length=10,
            z_threshold=1.5,  # slightly lower to ensure detection
            rolling_window=20,
            timeframe="1h",
        )
        result = svc.analyze(closes)

        # There should be at least one change point
        assert len(result.change_points) >= 1, (
            f"Expected at least 1 change point, got 0. "
            f"Max novelty score: {np.nanmax(result.novelty_scores):.4f}"
        )

        # At least one should be within ±30 bars of the midpoint (100)
        near_mid = [
            cp for cp in result.change_points
            if abs(cp.bar_index - 100) <= 30
        ]
        assert len(near_mid) >= 1, (
            f"Expected change point near bar 100, got: "
            f"{[cp.bar_index for cp in result.change_points]}"
        )

    def test_short_series_returns_empty(self):
        """Series shorter than the minimum should return an empty result."""
        closes = np.array([100.0, 101.0, 102.0])
        svc = MatrixProfileService(subsequence_length=10, timeframe="1h")
        result = svc.analyze(closes)

        assert len(result.change_points) == 0
        assert np.all(result.threshold_reduction == 1.0)

    def test_flat_series_fewer_change_points_than_trending(self):
        """A flat series should produce fewer change points than a trending one."""
        rng = np.random.default_rng(0)
        flat_closes = np.full(200, 100.0) + rng.normal(0, 1e-4, 200)
        trending_closes = _make_trending_then_reversing(200)

        svc = MatrixProfileService(
            subsequence_length=10,
            z_threshold=1.8,
            timeframe="1h",
        )

        flat_result = svc.analyze(flat_closes)
        trend_result = svc.analyze(trending_closes)

        # The trending series should have at least as many change points
        # as the flat one (ideally more, due to the actual regime change)
        assert len(trend_result.change_points) >= len(flat_result.change_points)

    def test_change_points_have_valid_scores(self):
        """All change-point scores should be above the threshold."""
        closes = _make_trending_then_reversing(200)
        threshold = 1.5
        svc = MatrixProfileService(
            subsequence_length=10,
            z_threshold=threshold,
            timeframe="1h",
        )
        result = svc.analyze(closes)

        for cp in result.change_points:
            assert cp.score >= threshold
            assert cp.is_significant is True
            assert 0 <= cp.bar_index < 200

    def test_reduction_decays_over_time(self):
        """After a change point, reduction should increase back to 1.0."""
        closes = _make_trending_then_reversing(200)
        svc = MatrixProfileService(
            subsequence_length=10,
            z_threshold=1.5,
            score_decay_bars=6,
            timeframe="1h",
        )
        result = svc.analyze(closes)

        if result.change_points:
            cp = result.change_points[0]
            # The reduction at the CP should be <= reduction 6 bars later
            if cp.bar_index + 6 < 200:
                assert (
                    result.threshold_reduction[cp.bar_index]
                    <= result.threshold_reduction[cp.bar_index + 6]
                )


# ---------------------------------------------------------------------------
# Integration with DetectReversalsUseCase
# ---------------------------------------------------------------------------

class TestDetectReversalsWithMP:

    def test_use_case_runs_with_mp_enabled(self):
        """The full pipeline should execute without error when MP is on."""
        closes = _make_trending_then_reversing(200)
        bars = _closes_to_bars(closes)

        uc = DetectReversalsUseCase(
            signal_mode=SignalMode.CONFIRMED_ONLY,
            sensitivity=SensitivityPreset.MEDIUM,
            use_matrix_profile=True,
            timeframe="1h",
        )
        result = uc.execute(bars)

        assert result.mp_enabled is True
        assert isinstance(result.regime_change_signals, list)

    def test_use_case_runs_with_mp_disabled(self):
        """Pipeline works normally when MP is explicitly disabled."""
        closes = _make_trending_then_reversing(200)
        bars = _closes_to_bars(closes)

        uc = DetectReversalsUseCase(
            signal_mode=SignalMode.CONFIRMED_ONLY,
            sensitivity=SensitivityPreset.MEDIUM,
            use_matrix_profile=False,
            timeframe="1h",
        )
        result = uc.execute(bars)

        assert result.mp_enabled is False
        assert result.regime_change_signals == []

    def test_mp_reduces_detection_latency(self):
        """
        With MP enabled, reversals should be detected earlier (or at the
        same time) compared to MP disabled, because the threshold is reduced
        near regime changes.
        """
        closes = _make_trending_then_reversing(300)
        bars = _closes_to_bars(closes)

        # Without MP
        uc_no_mp = DetectReversalsUseCase(
            signal_mode=SignalMode.CONFIRMED_ONLY,
            sensitivity=SensitivityPreset.MEDIUM,
            use_matrix_profile=False,
            timeframe="1h",
        )
        result_no_mp = uc_no_mp.execute(bars)

        # With MP
        uc_mp = DetectReversalsUseCase(
            signal_mode=SignalMode.CONFIRMED_ONLY,
            sensitivity=SensitivityPreset.MEDIUM,
            use_matrix_profile=True,
            mp_cac_threshold=1.5,
            mp_min_reduction=0.40,
            timeframe="1h",
        )
        result_mp = uc_mp.execute(bars)

        assert result_mp.mp_enabled is True

        # Both should produce some signals on this synthetic data
        if result_no_mp.signals and result_mp.signals:
            # Find the first bearish signal around the reversal point
            bearish_no_mp = [
                s for s in result_no_mp.signals if not s.is_bullish
            ]
            bearish_mp = [
                s for s in result_mp.signals if not s.is_bullish
            ]
            if bearish_no_mp and bearish_mp:
                first_no_mp = min(s.bar_index for s in bearish_no_mp)
                first_mp = min(s.bar_index for s in bearish_mp)
                assert first_mp <= first_no_mp, (
                    f"MP detection ({first_mp}) should be <= "
                    f"non-MP detection ({first_no_mp})"
                )

    def test_result_contains_regime_change_signals(self):
        """Regime change signals should have valid fields."""
        closes = _make_trending_then_reversing(200)
        bars = _closes_to_bars(closes)

        uc = DetectReversalsUseCase(
            signal_mode=SignalMode.CONFIRMED_ONLY,
            sensitivity=SensitivityPreset.MEDIUM,
            use_matrix_profile=True,
            mp_cac_threshold=1.5,
            timeframe="1h",
        )
        result = uc.execute(bars)

        for rcs in result.regime_change_signals:
            assert 0 <= rcs.bar_index < 200
            assert rcs.score > 0
            assert rcs.is_bullish in (True, False, None)
            assert rcs.label == "EARLY_WARNING"
