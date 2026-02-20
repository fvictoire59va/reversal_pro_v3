"""Unit tests for AgentBrokerService pure calculation methods.

These tests do NOT require a database — they exercise the deterministic
`_calculate_sl_tp` and `_is_risk_too_small` helpers.
"""

import pytest


# ============================================================================
# _calculate_sl_tp
# ============================================================================

class TestCalculateSlTp:
    """Test SL/TP calculation for various scenarios."""

    # ---- LONG side ---------------------------------------------------------

    def test_long_pivot_below_entry(self, broker_service):
        """LONG: SL = pivot when pivot < entry."""
        sl, tp1, tp2 = broker_service._calculate_sl_tp(
            side="LONG",
            entry_price=100.0,
            pivot_price=95.0,
            atr=2.0,
            timeframe="1h",
        )
        assert sl == 95.0, "SL should be at pivot price"
        assert tp1 > 100.0, "TP1 must be above entry"
        assert tp2 > tp1, "TP2 must be further than TP1"

    def test_long_no_pivot_uses_atr(self, broker_service):
        """LONG without pivot: SL = entry - ATR * multiplier."""
        sl, tp1, tp2 = broker_service._calculate_sl_tp(
            side="LONG",
            entry_price=100.0,
            pivot_price=None,
            atr=3.0,
            timeframe="1h",
        )
        assert sl < 100.0
        assert tp1 > 100.0

    def test_long_no_atr_no_pivot_fallback(self, broker_service):
        """LONG with neither pivot nor ATR → fallback SL %."""
        sl, tp1, tp2 = broker_service._calculate_sl_tp(
            side="LONG",
            entry_price=100.0,
            pivot_price=None,
            atr=None,
            timeframe="1h",
        )
        assert sl < 100.0
        assert tp1 > 100.0

    def test_long_sl_capped_at_max_pct(self, broker_service):
        """LONG: SL distance should not exceed max_sl_pct of entry."""
        sl, tp1, tp2 = broker_service._calculate_sl_tp(
            side="LONG",
            entry_price=100.0,
            pivot_price=50.0,  # very far pivot → should be capped
            atr=None,
            timeframe="1h",
        )
        _, _, max_sl_pct, _ = broker_service._get_tf_params("1h")
        max_sl_dist = 100.0 * (max_sl_pct / 100)
        assert (100.0 - sl) <= max_sl_dist + 1e-9

    def test_long_zone_tp_used_when_good_rr(self, broker_service):
        """LONG: zone_tp replaces default TP1 if R:R >= 1.0."""
        sl, tp1, tp2 = broker_service._calculate_sl_tp(
            side="LONG",
            entry_price=100.0,
            pivot_price=95.0,
            atr=2.0,
            timeframe="1h",
            zone_tp=115.0,  # 15 reward / 5 risk = 3:1
        )
        assert tp1 == 115.0, "Zone TP should be used when R:R is good"

    def test_long_zone_tp_ignored_when_bad_rr(self, broker_service):
        """LONG: zone_tp ignored if R:R < 1.0."""
        sl, tp1_with_zone, tp2 = broker_service._calculate_sl_tp(
            side="LONG",
            entry_price=100.0,
            pivot_price=95.0,
            atr=2.0,
            timeframe="1h",
            zone_tp=102.0,  # 2 reward / 5 risk = 0.4 → rejected
        )
        _, tp1_without, _ = broker_service._calculate_sl_tp(
            side="LONG",
            entry_price=100.0,
            pivot_price=95.0,
            atr=2.0,
            timeframe="1h",
            zone_tp=None,
        )
        assert tp1_with_zone == tp1_without, "Zone TP should be ignored when R:R < 1"

    # ---- SHORT side --------------------------------------------------------

    def test_short_pivot_above_entry(self, broker_service):
        """SHORT: SL = pivot when pivot > entry."""
        sl, tp1, tp2 = broker_service._calculate_sl_tp(
            side="SHORT",
            entry_price=100.0,
            pivot_price=105.0,
            atr=2.0,
            timeframe="1h",
        )
        assert sl == 105.0, "SL should be at pivot price"
        assert tp1 < 100.0, "TP1 must be below entry"
        assert tp2 < tp1, "TP2 must be further below than TP1"

    def test_short_no_pivot_uses_atr(self, broker_service):
        """SHORT without pivot: SL = entry + ATR * multiplier."""
        sl, tp1, tp2 = broker_service._calculate_sl_tp(
            side="SHORT",
            entry_price=100.0,
            pivot_price=None,
            atr=3.0,
            timeframe="1h",
        )
        assert sl > 100.0
        assert tp1 < 100.0

    # ---- TP2 relationship ---------------------------------------------------

    def test_tp2_always_1_5x_tp1_distance(self, broker_service):
        """TP2 = entry ± 1.5 × (TP1 − entry)."""
        for side, entry, pivot in [("LONG", 100, 95), ("SHORT", 100, 105)]:
            sl, tp1, tp2 = broker_service._calculate_sl_tp(
                side=side,
                entry_price=entry,
                pivot_price=pivot,
                atr=2.0,
                timeframe="1h",
            )
            tp1_dist = abs(tp1 - entry)
            expected_tp2_dist = 1.5 * tp1_dist
            actual_tp2_dist = abs(tp2 - entry)
            assert abs(actual_tp2_dist - expected_tp2_dist) < 1e-6

    # ---- Timeframe adaptive --------------------------------------------------

    def test_different_timeframes_produce_different_params(self, broker_service):
        """Shorter timeframes should use different R:R / ATR multipliers."""
        sl_1h, tp_1h, _ = broker_service._calculate_sl_tp(
            side="LONG", entry_price=100.0, pivot_price=None,
            atr=2.0, timeframe="1h"
        )
        sl_15m, tp_15m, _ = broker_service._calculate_sl_tp(
            side="LONG", entry_price=100.0, pivot_price=None,
            atr=2.0, timeframe="15m"
        )
        # SL/TP should differ between timeframes (different params)
        assert (sl_1h != sl_15m) or (tp_1h != tp_15m), (
            "1h and 15m should produce different SL/TP"
        )


# ============================================================================
# _is_risk_too_small
# ============================================================================

class TestIsRiskTooSmall:
    """Test the minimum risk filter."""

    def test_tiny_risk_rejected_on_1h(self, broker_service):
        """Risk of 0.1% on 1h should be rejected (min is 0.40%)."""
        result = broker_service._is_risk_too_small(
            agent_name="test",
            side="LONG",
            entry_price=100.0,
            sl=99.9,  # 0.1% risk
            timeframe="1h",
        )
        assert result is True

    def test_adequate_risk_accepted_on_1h(self, broker_service):
        """Risk of 1% on 1h should be accepted."""
        result = broker_service._is_risk_too_small(
            agent_name="test",
            side="LONG",
            entry_price=100.0,
            sl=99.0,  # 1% risk
            timeframe="1h",
        )
        assert result is False

    def test_tiny_risk_rejected_on_15m(self, broker_service):
        """Risk of 0.1% on 15m should be rejected (min is 0.25%)."""
        result = broker_service._is_risk_too_small(
            agent_name="test",
            side="SHORT",
            entry_price=100.0,
            sl=100.1,  # 0.1%
            timeframe="15m",
        )
        assert result is True

    def test_adequate_risk_accepted_on_5m(self, broker_service):
        """Risk of 0.2% on 5m should be accepted (min is 0.15%)."""
        result = broker_service._is_risk_too_small(
            agent_name="test",
            side="LONG",
            entry_price=100.0,
            sl=99.8,  # 0.2%
            timeframe="5m",
        )
        assert result is False

    def test_zero_entry_price_handled(self, broker_service):
        """Entry price of 0 should not raise (edge case)."""
        result = broker_service._is_risk_too_small(
            agent_name="test",
            side="LONG",
            entry_price=0.0,
            sl=0.0,
            timeframe="1h",
        )
        # risk_pct will be 0 → too small → True
        assert result is True
