"""
Risk Manager Mixin — SL/TP calculation (including zone-based targets),
trailing stop, breakeven, minimum-risk filter, and shared price helpers.
"""

import logging
from typing import Optional, Dict

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ...models import Agent, AgentPosition
from ..hyperliquid_client import hyperliquid_client

logger = logging.getLogger(__name__)


class RiskManagerMixin:
    """Stop-loss / take-profit calculation, trailing stop, and breakeven."""

    # ── Timeframe-adaptive parameters ──────────────────────
    TF_PARAMS: Dict[int, tuple] = {
        # tf_minutes: (R:R ratio, ATR mult for SL fallback, max SL %, fallback SL %)
        1:    (1.5, 1.0, 0.30, 0.50),
        5:    (2.0, 1.2, 0.50, 0.80),
        15:   (2.5, 1.3, 0.80, 1.20),
        60:   (3.0, 1.5, 1.50, 2.00),
        240:  (3.0, 1.5, 3.00, 3.00),
        1440: (3.0, 1.5, 5.00, 5.00),
    }

    # trail_atr_mult: how many ATRs behind the best price the trailing SL sits
    # trail_activation_mult: how many × risk the price must move before trailing starts
    TRAIL_PARAMS: Dict[int, tuple] = {
        1:    (0.8,  1.0),
        5:    (1.0,  1.0),
        15:   (1.2,  1.5),
        60:   (1.5,  1.5),
        240:  (1.5,  2.0),
        1440: (2.0,  2.0),
    }

    # ── Price helpers ────────────────────────────────────────

    async def _get_current_price(
        self, db: AsyncSession, symbol: str, timeframe: str
    ) -> Optional[float]:
        """Get current price from the latest OHLCV candle, or from Hyperliquid."""
        result = await db.execute(text("""
            SELECT close FROM ohlcv
            WHERE symbol = :symbol AND timeframe = :timeframe
            ORDER BY time DESC LIMIT 1
        """), {"symbol": symbol, "timeframe": timeframe})
        row = result.fetchone()
        if row:
            return row[0]
        return await hyperliquid_client.get_mid_price(symbol)

    async def _get_latest_candle_range(
        self, db: AsyncSession, symbol: str, timeframe: str
    ) -> Optional[dict]:
        """Get high, low, close from the latest OHLCV candle."""
        result = await db.execute(text("""
            SELECT high, low, close FROM ohlcv
            WHERE symbol = :symbol AND timeframe = :timeframe
            ORDER BY time DESC LIMIT 1
        """), {"symbol": symbol, "timeframe": timeframe})
        row = result.fetchone()
        if row:
            return {"high": row[0], "low": row[1], "close": row[2]}
        return None

    async def _get_current_atr(
        self, db: AsyncSession, symbol: str, timeframe: str
    ) -> Optional[float]:
        """Get current ATR from the latest analysis run."""
        result = await db.execute(text("""
            SELECT current_atr FROM analysis_runs
            WHERE symbol = :symbol AND timeframe = :timeframe
            ORDER BY created_at DESC LIMIT 1
        """), {"symbol": symbol, "timeframe": timeframe})
        row = result.fetchone()
        return row[0] if row else None

    async def _get_previous_pivot(
        self, db: AsyncSession, symbol: str, timeframe: str,
        is_bullish: bool, before_time,
    ) -> Optional[float]:
        """Get the previous opposite pivot price for SL calculation."""
        result = await db.execute(text("""
            SELECT price FROM signals
            WHERE symbol = :symbol AND timeframe = :timeframe
              AND is_bullish = :opposite AND is_preview = FALSE
              AND time < :before_time
            ORDER BY time DESC
            LIMIT 1
        """), {
            "symbol": symbol,
            "timeframe": timeframe,
            "opposite": not is_bullish,
            "before_time": before_time,
        })
        row = result.fetchone()
        return row[0] if row else None

    # ── TF param helpers ─────────────────────────────────────

    def _get_tf_params(self, timeframe: str) -> tuple:
        """Return (rr_ratio, atr_mult, max_sl_pct, fallback_sl_pct) for a TF."""
        tf_min = self._timeframe_to_minutes(timeframe)
        best = (3.0, 1.5, 5.0, 5.0)
        for minutes in sorted(self.TF_PARAMS.keys()):
            if tf_min <= minutes:
                best = self.TF_PARAMS[minutes]
                break
        else:
            best = self.TF_PARAMS[1440]
        return best

    def _get_trail_params(self, timeframe: str) -> tuple:
        """Return (trail_atr_mult, activation_risk_mult) for a TF."""
        tf_min = self._timeframe_to_minutes(timeframe)
        best = (1.5, 1.5)
        for minutes in sorted(self.TRAIL_PARAMS.keys()):
            if tf_min <= minutes:
                best = self.TRAIL_PARAMS[minutes]
                break
        else:
            best = self.TRAIL_PARAMS[1440]
        return best

    # ── SL / TP calculation ──────────────────────────────────

    def _calculate_sl_tp(
        self, side: str, entry_price: float,
        pivot_price: Optional[float], atr: Optional[float],
        timeframe: str = "1h", zone_tp: Optional[float] = None,
    ) -> tuple:
        """
        Calculate Stop Loss, TP1, and TP2 with timeframe-adaptive R:R.

        Returns ``(sl, tp1, tp2)``.
        """
        rr_ratio, atr_mult, max_sl_pct, fallback_sl_pct = self._get_tf_params(timeframe)

        if side == "LONG":
            if pivot_price and pivot_price < entry_price:
                sl = pivot_price
            elif atr:
                sl = entry_price - (atr_mult * atr)
            else:
                sl = entry_price * (1 - fallback_sl_pct / 100)

            max_sl_dist = entry_price * (max_sl_pct / 100)
            if (entry_price - sl) > max_sl_dist:
                sl = entry_price - max_sl_dist

            risk = entry_price - sl
            default_tp = entry_price + (rr_ratio * risk)

            if zone_tp and zone_tp > entry_price:
                zone_reward = zone_tp - entry_price
                zone_rr = zone_reward / risk if risk > 0 else 0
                tp1 = zone_tp if zone_rr >= 1.0 else default_tp
            else:
                tp1 = default_tp

            tp1_dist = tp1 - entry_price
            tp2 = entry_price + (1.5 * tp1_dist)

        else:  # SHORT
            if pivot_price and pivot_price > entry_price:
                sl = pivot_price
            elif atr:
                sl = entry_price + (atr_mult * atr)
            else:
                sl = entry_price * (1 + fallback_sl_pct / 100)

            max_sl_dist = entry_price * (max_sl_pct / 100)
            if (sl - entry_price) > max_sl_dist:
                sl = entry_price + max_sl_dist

            risk = sl - entry_price
            default_tp = entry_price - (rr_ratio * risk)

            if zone_tp and zone_tp < entry_price:
                zone_reward = entry_price - zone_tp
                zone_rr = zone_reward / risk if risk > 0 else 0
                tp1 = zone_tp if zone_rr >= 1.0 else default_tp
            else:
                tp1 = default_tp

            tp1_dist = entry_price - tp1
            tp2 = entry_price - (1.5 * tp1_dist)

        return round(sl, 2), round(tp1, 2), round(tp2, 2)

    async def _get_zone_tp(
        self, db: AsyncSession, symbol: str, timeframe: str,
        side: str, entry_price: float,
    ) -> Optional[float]:
        """Find nearest S/D zone TP target from persisted zones."""
        if side == "LONG":
            result = await db.execute(text("""
                SELECT bottom_price FROM zones
                WHERE symbol = :symbol AND timeframe = :timeframe
                  AND zone_type = 'SUPPLY' AND center_price > :entry_price
                ORDER BY center_price ASC
                LIMIT 1
            """), {"symbol": symbol, "timeframe": timeframe, "entry_price": entry_price})
        else:
            result = await db.execute(text("""
                SELECT top_price FROM zones
                WHERE symbol = :symbol AND timeframe = :timeframe
                  AND zone_type = 'DEMAND' AND center_price < :entry_price
                ORDER BY center_price DESC
                LIMIT 1
            """), {"symbol": symbol, "timeframe": timeframe, "entry_price": entry_price})

        row = result.fetchone()
        if row:
            logger.info(f"Zone-based TP for {side}: {row[0]:.2f} (entry={entry_price:.2f})")
            return row[0]
        return None

    # ── Trailing stop ────────────────────────────────────────

    async def _check_trailing_stop(
        self, db: AsyncSession, agent: Agent, pos: AgentPosition,
        current_price: float, candle_low: float = None, candle_high: float = None,
    ) -> bool:
        """
        Ratchet SL in the profit direction using ATR-based trail distance.
        Activates only after breakeven is already set.
        """
        if pos.side == "LONG" and pos.stop_loss < pos.entry_price:
            return False
        if pos.side == "SHORT" and pos.stop_loss > pos.entry_price:
            return False

        atr = await self._get_current_atr(db, agent.symbol, agent.timeframe)
        if not atr or atr <= 0:
            return False

        trail_atr_mult, _ = self._get_trail_params(agent.timeframe)
        trail_distance = atr * trail_atr_mult

        if pos.side == "LONG":
            extreme = candle_high if candle_high is not None else current_price
            new_best = max(pos.best_price or pos.entry_price, extreme)
        else:
            extreme = candle_low if candle_low is not None else current_price
            new_best = min(pos.best_price or pos.entry_price, extreme)

        if new_best != (pos.best_price or pos.entry_price):
            pos.best_price = new_best

        if pos.side == "LONG":
            new_sl = round(new_best - trail_distance, 2)
            if new_sl > pos.stop_loss:
                old_sl = pos.stop_loss
                pos.stop_loss = new_sl
                await db.commit()
                logger.info(
                    f"[{agent.name}] TRAILING STOP updated for LONG: "
                    f"SL {old_sl:.2f} → {new_sl:.2f} "
                    f"(best={new_best:.2f}, trail={trail_distance:.2f}, "
                    f"ATR={atr:.2f} × {trail_atr_mult})"
                )
                await self._log(db, agent.id, "TRAILING_STOP_UPDATED", {
                    "position_id": pos.id, "side": pos.side,
                    "old_sl": old_sl, "new_sl": new_sl,
                    "best_price": new_best,
                    "trail_distance": round(trail_distance, 2),
                    "atr": round(atr, 2), "current_price": current_price,
                })
                return True
        else:
            new_sl = round(new_best + trail_distance, 2)
            if new_sl < pos.stop_loss:
                old_sl = pos.stop_loss
                pos.stop_loss = new_sl
                await db.commit()
                logger.info(
                    f"[{agent.name}] TRAILING STOP updated for SHORT: "
                    f"SL {old_sl:.2f} → {new_sl:.2f} "
                    f"(best={new_best:.2f}, trail={trail_distance:.2f}, "
                    f"ATR={atr:.2f} × {trail_atr_mult})"
                )
                await self._log(db, agent.id, "TRAILING_STOP_UPDATED", {
                    "position_id": pos.id, "side": pos.side,
                    "old_sl": old_sl, "new_sl": new_sl,
                    "best_price": new_best,
                    "trail_distance": round(trail_distance, 2),
                    "atr": round(atr, 2), "current_price": current_price,
                })
                return True

        await db.commit()
        return False

    # ── Breakeven ────────────────────────────────────────────

    async def _check_breakeven(
        self, db: AsyncSession, agent: Agent,
        pos: AgentPosition, current_price: float,
    ) -> bool:
        """Move SL to breakeven when position has moved >= 1× risk."""
        if pos.side == "LONG" and pos.stop_loss >= pos.entry_price:
            return False
        if pos.side == "SHORT" and pos.stop_loss <= pos.entry_price:
            return False

        original_sl = pos.original_stop_loss or pos.stop_loss
        risk = abs(pos.entry_price - original_sl)
        if risk <= 0:
            return False

        if pos.side == "LONG":
            profit_distance = current_price - pos.entry_price
            if profit_distance >= risk:
                old_sl = pos.stop_loss
                pos.stop_loss = pos.entry_price
                await db.commit()
                logger.info(
                    f"[{agent.name}] BREAKEVEN activated for LONG: "
                    f"SL moved {old_sl:.2f} → {pos.entry_price:.2f} "
                    f"(price={current_price:.2f}, risk={risk:.2f})"
                )
                await self._log(db, agent.id, "BREAKEVEN_ACTIVATED", {
                    "position_id": pos.id, "side": pos.side,
                    "old_sl": old_sl, "new_sl": pos.entry_price,
                    "current_price": current_price, "risk": round(risk, 2),
                })
                return True
        else:
            profit_distance = pos.entry_price - current_price
            if profit_distance >= risk:
                old_sl = pos.stop_loss
                pos.stop_loss = pos.entry_price
                await db.commit()
                logger.info(
                    f"[{agent.name}] BREAKEVEN activated for SHORT: "
                    f"SL moved {old_sl:.2f} → {pos.entry_price:.2f} "
                    f"(price={current_price:.2f}, risk={risk:.2f})"
                )
                await self._log(db, agent.id, "BREAKEVEN_ACTIVATED", {
                    "position_id": pos.id, "side": pos.side,
                    "old_sl": old_sl, "new_sl": pos.entry_price,
                    "current_price": current_price, "risk": round(risk, 2),
                })
                return True

        return False

    # ── Minimum risk filter ──────────────────────────────────

    def _is_risk_too_small(
        self, agent_name: str, side: str,
        entry_price: float, sl: float, timeframe: str,
    ) -> bool:
        """Reject trade if SL is too close to entry price."""
        risk = abs(entry_price - sl)
        risk_pct = (risk / entry_price) * 100 if entry_price > 0 else 0

        tf_minutes = self._timeframe_to_minutes(timeframe)
        if tf_minutes <= 5:
            min_risk_pct = 0.15
        elif tf_minutes <= 15:
            min_risk_pct = 0.25
        else:
            min_risk_pct = 0.40

        if risk_pct < min_risk_pct:
            logger.info(
                f"[{agent_name}] SKIPPING {side}: risk too small "
                f"({risk_pct:.3f}% < {min_risk_pct}% min). "
                f"Entry={entry_price:.2f}, SL={sl:.2f}, gap={risk:.2f}"
            )
            return True
        return False

    # ── Stop-loss check ──────────────────────────────────────

    async def _check_stop_loss(
        self, db: AsyncSession, agent: Agent, pos: AgentPosition,
        current_price: float, candle_low: float = None, candle_high: float = None,
    ) -> bool:
        """Check if price has hit the stop loss (wick-aware)."""
        triggered = False
        low = candle_low if candle_low is not None else current_price
        high = candle_high if candle_high is not None else current_price

        if pos.side == "LONG" and low <= pos.stop_loss:
            triggered = True
            # In paper mode, honour the SL level exactly (no simulated slippage).
            # In live mode, the actual fill may differ — but we don't know it here.
            realistic_exit = pos.stop_loss
        elif pos.side == "SHORT" and high >= pos.stop_loss:
            triggered = True
            realistic_exit = pos.stop_loss
        else:
            realistic_exit = current_price

        if triggered:
            # Distinguish trailing stop from original stop loss
            original_sl = pos.original_stop_loss or pos.stop_loss
            is_trailing = (
                (pos.side == "LONG" and pos.stop_loss > original_sl)
                or (pos.side == "SHORT" and pos.stop_loss < original_sl)
            )
            reason = "TRAILING_STOP" if is_trailing else "STOP_LOSS"
            label = "TRAILING STOP" if is_trailing else "STOP LOSS"

            logger.info(
                f"[{agent.name}] {label} triggered for {pos.side} "
                f"@ {current_price:.2f} (SL: {pos.stop_loss:.2f}, "
                f"original SL: {original_sl:.2f}, "
                f"Low: {low:.2f}, High: {high:.2f}, exit: {realistic_exit:.2f})"
            )
            await self._close_position_internal(
                db, pos, exit_price=realistic_exit, reason=reason
            )
            return True

        return False

    # ── Timeframe conversion ─────────────────────────────────

    @staticmethod
    def _timeframe_to_minutes(timeframe: str) -> int:
        """Convert timeframe string (e.g. '1m', '5m', '1h', '4h', '1d') to minutes."""
        tf = timeframe.strip().lower()
        if tf.endswith('m'):
            return int(tf[:-1])
        elif tf.endswith('h'):
            return int(tf[:-1]) * 60
        elif tf.endswith('d'):
            return int(tf[:-1]) * 1440
        elif tf.endswith('w'):
            return int(tf[:-1]) * 10080
        return 60
