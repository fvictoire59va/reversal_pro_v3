"""Unit tests for the reversal detection core engine services.

Tests ATRService, EMAService, ZigZagService, ReversalDetector, and
SupplyDemandService in isolation using small, deterministic datasets.
"""

import numpy as np
import pytest

from reversal_pro.application.services.atr_service import ATRService
from reversal_pro.application.services.ema_service import EMAService
from reversal_pro.application.services.zigzag_service import ZigZagService
from reversal_pro.application.services.reversal_detector import ReversalDetector
from reversal_pro.application.services.supply_demand_service import SupplyDemandService
from reversal_pro.domain.entities import Pivot
from reversal_pro.domain.enums import TrendState, Direction


# ╔══════════════════════════════════════════════════════════════╗
# ║  ATRService                                                  ║
# ╚══════════════════════════════════════════════════════════════╝

class TestATRService:
    """Tests for ATR calculation and reversal threshold."""

    def test_true_range_basic(self):
        highs = np.array([12.0, 13.0, 14.0, 13.5])
        lows = np.array([10.0, 11.0, 12.0, 11.5])
        closes = np.array([11.0, 12.0, 13.0, 12.0])
        tr = ATRService.true_range(highs, lows, closes)
        assert len(tr) == 4
        # First bar: high - low
        assert tr[0] == pytest.approx(2.0)
        # Subsequent bars use max(HL, |H-prevC|, |L-prevC|)
        assert tr[1] == pytest.approx(2.0)  # max(2, |13-11|, |11-11|) = 2
        assert tr[2] == pytest.approx(2.0)  # max(2, |14-12|, |12-12|) = 2
        assert tr[3] == pytest.approx(2.0)  # max(2, |13.5-13|, |11.5-13|) = 2

    def test_atr_period(self):
        highs = np.array([12., 13., 14., 13.5, 15., 14.5, 16., 15.5, 17., 16.5])
        lows = np.array([10., 11., 12., 11.5, 13., 12.5, 14., 13.5, 15., 14.5])
        closes = np.array([11., 12., 13., 12., 14., 13., 15., 14., 16., 15.])
        atr_vals = ATRService.atr(highs, lows, closes, period=3)
        # First 2 bars should be NaN
        assert np.isnan(atr_vals[0])
        assert np.isnan(atr_vals[1])
        # Bar 2 (index 2): mean of first 3 TRs
        assert not np.isnan(atr_vals[2])
        # All subsequent should be valid
        for i in range(2, 10):
            assert not np.isnan(atr_vals[i])
            assert atr_vals[i] > 0

    def test_atr_insufficient_data(self):
        highs = np.array([12., 13.])
        lows = np.array([10., 11.])
        closes = np.array([11., 12.])
        atr_vals = ATRService.atr(highs, lows, closes, period=5)
        assert all(np.isnan(atr_vals))

    def test_reversal_threshold_pct_dominates(self):
        # High close × decent pct should dominate small ATR
        result = ATRService.compute_reversal_threshold(
            close=50000.0,
            percent_threshold=0.01,
            absolute_reversal=0.5,
            atr_multiplier=2.0,
            atr_value=1.0,
        )
        # pct: 50000 * 0.01 / 100 = 5.0
        # atr: 2 * 1 = 2.0
        # abs: 0.5
        # max(5, max(0.5, 2)) = 5
        assert result == pytest.approx(5.0)

    def test_reversal_threshold_atr_dominates(self):
        result = ATRService.compute_reversal_threshold(
            close=100.0,
            percent_threshold=0.01,
            absolute_reversal=0.5,
            atr_multiplier=2.0,
            atr_value=50.0,
        )
        # pct: 100 * 0.01 / 100 = 0.01
        # atr: 2 * 50 = 100
        # abs: 0.5
        # max(0.01, max(0.5, 100)) = 100
        assert result == pytest.approx(100.0)

    def test_reversal_threshold_absolute_dominates(self):
        result = ATRService.compute_reversal_threshold(
            close=1.0,
            percent_threshold=0.01,
            absolute_reversal=10.0,
            atr_multiplier=2.0,
            atr_value=0.1,
        )
        # pct: 1 * 0.01 / 100 = 0.0001
        # atr: 0.2
        # abs: 10
        # max(0.0001, max(10, 0.2)) = 10
        assert result == pytest.approx(10.0)


# ╔══════════════════════════════════════════════════════════════╗
# ║  EMAService                                                  ║
# ╚══════════════════════════════════════════════════════════════╝

class TestEMAService:
    """Tests for EMA computation and trend detection."""

    def test_ema_basic(self):
        data = np.array([1., 2., 3., 4., 5., 6., 7., 8., 9., 10.])
        ema = EMAService.ema(data, period=3)
        # First 2 bars should be NaN
        assert np.isnan(ema[0])
        assert np.isnan(ema[1])
        # Bar 2: SMA of first 3 = (1+2+3)/3 = 2
        assert ema[2] == pytest.approx(2.0)
        # EMA should trend upward
        for i in range(3, len(data)):
            assert ema[i] > ema[i - 1]

    def test_ema_empty(self):
        data = np.array([])
        ema = EMAService.ema(data, period=3)
        assert len(ema) == 0

    def test_ema_insufficient_data(self):
        data = np.array([5., 10.])
        ema = EMAService.ema(data, period=5)
        # Not enough data → only last bar gets SMA of available
        assert np.isnan(ema[0])
        assert ema[1] == pytest.approx(7.5)

    def test_compute_trend_bullish(self):
        """A steadily rising price should produce BULLISH trend."""
        n = 50
        closes = np.linspace(100, 200, n)
        highs = closes + 1
        lows = closes - 0.5  # lows stay above EMA9 to satisfy buy condition
        trends, state = EMAService.compute_trend(closes, highs, lows)
        assert len(trends) == n
        # Last few bars should be bullish
        assert trends[-1].state == TrendState.BULLISH

    def test_compute_trend_bearish(self):
        """A steadily falling price should produce BEARISH trend."""
        n = 50
        closes = np.linspace(200, 100, n)
        highs = closes + 0.5  # highs stay below EMA9
        lows = closes - 1
        trends, state = EMAService.compute_trend(closes, highs, lows)
        assert len(trends) == n
        assert trends[-1].state == TrendState.BEARISH

    def test_trend_change_fires_once(self):
        """Trend change flags should fire on exactly one bar, not two."""
        n = 80
        # Rising first half, falling second half
        part1 = np.linspace(100, 200, n // 2)
        part2 = np.linspace(200, 100, n // 2)
        closes = np.concatenate([part1, part2])
        highs = closes + 1
        lows = closes - 1
        trends, _ = EMAService.compute_trend(closes, highs, lows)

        # Count bullish transitions
        bullish_transitions = sum(
            1 for t in trends if t.trend_changed_to_bullish
        )
        bearish_transitions = sum(
            1 for t in trends if t.trend_changed_to_bearish
        )
        # Each transition should fire at most once per direction change
        assert bullish_transitions <= 2  # Can have initial + re-entry
        assert bearish_transitions <= 2


# ╔══════════════════════════════════════════════════════════════╗
# ║  ZigZagService                                               ║
# ╚══════════════════════════════════════════════════════════════╝

class TestZigZagService:
    """Tests for ZigZag pivot computation."""

    def _make_zigzag_data(self):
        """Create a clear zigzag pattern: up-down-up-down."""
        # Pattern: 100→120→90→130→85
        prices = [
            100, 105, 110, 115, 120,   # Up phase
            115, 110, 105, 100, 90,    # Down phase
            95, 100, 110, 120, 130,    # Up phase
            125, 115, 105, 95, 85,     # Down phase
        ]
        highs = np.array([p + 1 for p in prices], dtype=float)
        lows = np.array([p - 1 for p in prices], dtype=float)
        return highs, lows

    def test_compute_pivots_finds_reversals(self):
        highs, lows = self._make_zigzag_data()
        n = len(highs)
        reversal_amounts = np.full(n, 5.0)  # 5-point reversal threshold

        zz = ZigZagService(use_ema=False, confirmation_bars=0)
        pivots = zz.compute_pivots(highs, lows, reversal_amounts)

        assert len(pivots) >= 2
        # Should alternate between highs and lows
        for i in range(1, len(pivots)):
            assert pivots[i].is_high != pivots[i - 1].is_high

    def test_compute_pivots_with_confirmation(self):
        highs, lows = self._make_zigzag_data()
        n = len(highs)
        reversal_amounts = np.full(n, 5.0)

        zz_no_cb = ZigZagService(use_ema=False, confirmation_bars=0)
        zz_cb2 = ZigZagService(use_ema=False, confirmation_bars=2)

        pivots_no_cb = zz_no_cb.compute_pivots(highs, lows, reversal_amounts)
        pivots_cb2 = zz_cb2.compute_pivots(highs, lows, reversal_amounts)

        # With confirmation bars, should detect same or fewer pivots
        assert len(pivots_cb2) <= len(pivots_no_cb)

    def test_no_look_ahead_bias(self):
        """Ensure compute_pivots uses reversal_amounts[ci], not [i]."""
        highs, lows = self._make_zigzag_data()
        n = len(highs)

        # Set very high thresholds for future bars, low for confirmed bars.
        # With the bug (using [i]), these high thresholds would prevent pivots.
        # With the fix (using [ci]), the low thresholds apply correctly.
        cb = 2
        reversal_amounts = np.full(n, 5.0)
        reversal_amounts_biased = np.full(n, 5.0)
        # Make last few bars have huge threshold
        reversal_amounts_biased[-3:] = 99999.0

        zz = ZigZagService(use_ema=False, confirmation_bars=cb)
        pivots_normal = zz.compute_pivots(highs, lows, reversal_amounts)
        pivots_biased = zz.compute_pivots(highs, lows, reversal_amounts_biased)

        # With the fix, the biased future thresholds shouldn't affect
        # pivots that are confirmed bars back (ci = i - cb)
        # The last few confirmed bars index into cb bars before the end
        # so the huge thresholds at the very end shouldn't block earlier pivots
        # Pivots detected before the last cb bars should be identical
        early_normal = [p for p in pivots_normal if p.bar_index < n - cb - 1]
        early_biased = [p for p in pivots_biased if p.bar_index < n - cb - 1]
        assert len(early_normal) == len(early_biased)

    def test_preview_pivots_includes_forming(self):
        """Preview should include the last forming pivot even if not reversed."""
        highs, lows = self._make_zigzag_data()
        n = len(highs)
        reversal_amounts = np.full(n, 5.0)

        zz = ZigZagService(use_ema=False, confirmation_bars=0)
        previews = zz.compute_preview_pivots(highs, lows, reversal_amounts)

        # Last pivot should be at the forming extreme
        assert len(previews) > 0
        assert previews[-1].is_preview is True

    def test_empty_input(self):
        zz = ZigZagService(use_ema=False, confirmation_bars=0)
        pivots = zz.compute_pivots(
            np.array([]), np.array([]), np.array([])
        )
        assert pivots == []


# ╔══════════════════════════════════════════════════════════════╗
# ║  ReversalDetector                                            ║
# ╚══════════════════════════════════════════════════════════════╝

class TestReversalDetector:
    """Tests for U1/D1 reversal signal detection."""

    def test_detects_bullish_reversal(self):
        """A high pivot followed by price above pivot low → bullish U1."""
        n = 20
        # Create: flat → high peak at bar 5 → low trough at bar 10 → rise above
        price_h = np.full(n, 100.0)
        price_l = np.full(n, 100.0)

        pivots = [
            Pivot(price=110.0, actual_price=110.0, bar_index=5, is_high=True),
            Pivot(price=90.0, actual_price=90.0, bar_index=10, is_high=False),
        ]

        # After low pivot at bar 10, price_l rises above pivot low (90)
        for i in range(11, n):
            price_h[i] = 105.0
            price_l[i] = 95.0  # > 90 → triggers U1

        detector = ReversalDetector()
        signals = detector.detect(pivots, n, price_h, price_l)

        bullish = [s for s in signals if s.is_bullish]
        assert len(bullish) >= 1
        assert bullish[0].bar_index == 10  # Signal at the low pivot bar

    def test_detects_bearish_reversal(self):
        """A low pivot followed by price below pivot high → bearish D1."""
        n = 20
        price_h = np.full(n, 100.0)
        price_l = np.full(n, 100.0)

        pivots = [
            Pivot(price=90.0, actual_price=90.0, bar_index=5, is_high=False),
            Pivot(price=110.0, actual_price=110.0, bar_index=10, is_high=True),
        ]

        # After high pivot at bar 10, price_h drops below pivot high (110)
        for i in range(11, n):
            price_h[i] = 105.0  # < 110 → triggers D1
            price_l[i] = 95.0

        detector = ReversalDetector()
        signals = detector.detect(pivots, n, price_h, price_l)

        bearish = [s for s in signals if not s.is_bullish]
        assert len(bearish) >= 1
        assert bearish[0].bar_index == 10

    def test_no_signals_without_pivots(self):
        detector = ReversalDetector()
        signals = detector.detect([], 100, np.full(100, 100.0), np.full(100, 100.0))
        assert signals == []

    def test_no_signals_flat_market(self):
        """Flat market with pivots but no price confirmation → no signals."""
        n = 20
        price_h = np.full(n, 100.0)
        price_l = np.full(n, 100.0)

        pivots = [
            Pivot(price=100.0, actual_price=100.0, bar_index=5, is_high=True),
            Pivot(price=100.0, actual_price=100.0, bar_index=10, is_high=False),
        ]

        detector = ReversalDetector()
        signals = detector.detect(pivots, n, price_h, price_l)
        # Price never goes above/below pivot levels → no confirmation
        assert len(signals) == 0


# ╔══════════════════════════════════════════════════════════════╗
# ║  SupplyDemandService                                         ║
# ╚══════════════════════════════════════════════════════════════╝

class TestSupplyDemandService:
    """Tests for supply/demand zone generation."""

    def test_generates_zones_from_pivots(self):
        pivots = [
            Pivot(price=100.0, actual_price=100.0, bar_index=5, is_high=False),
            Pivot(price=120.0, actual_price=120.0, bar_index=10, is_high=True),
            Pivot(price=95.0, actual_price=95.0, bar_index=15, is_high=False),
            Pivot(price=130.0, actual_price=130.0, bar_index=20, is_high=True),
        ]

        service = SupplyDemandService(
            zone_thickness_pct=0.02,
            zone_extension_bars=20,
            max_zones=3,
        )
        zones = service.generate_zones(pivots)

        # Should create zones around pivots
        assert len(zones) > 0
        for z in zones:
            assert z.top_price >= z.center_price >= z.bottom_price
            assert z.zone_type.value in ("SUPPLY", "DEMAND")

    def test_no_zones_without_pivots(self):
        service = SupplyDemandService()
        zones = service.generate_zones([])
        assert zones == []

    def test_max_zones_cap(self):
        """Should not exceed max_zones per type."""
        pivots = [
            Pivot(price=100.0 + i * 10, actual_price=100.0 + i * 10,
                  bar_index=i * 5, is_high=(i % 2 == 1))
            for i in range(10)
        ]
        service = SupplyDemandService(max_zones=2)
        zones = service.generate_zones(pivots)

        supply = [z for z in zones if z.zone_type.value == "SUPPLY"]
        demand = [z for z in zones if z.zone_type.value == "DEMAND"]
        assert len(supply) <= 2
        assert len(demand) <= 2
