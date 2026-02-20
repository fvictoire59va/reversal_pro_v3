"""
Signal Evaluator Mixin — signal retrieval, staleness checks, duplicate
detection, pivot-momentum filters, HTF trend confirmation, and EMA
trend filter.
"""

import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ...models import Agent
from .constants import TIMEFRAME_SECONDS, HTF_MAP

logger = logging.getLogger(__name__)


class SignalEvaluatorMixin:
    """Methods that evaluate whether a signal is actionable."""

    # ── Signal retrieval ─────────────────────────────────────

    async def _get_latest_signal(
        self, db: AsyncSession, symbol: str, timeframe: str
    ) -> Optional[tuple]:
        """Get the most recent confirmed signal."""
        result = await db.execute(text("""
            SELECT time, is_bullish, price, id, bar_index
            FROM signals
            WHERE symbol = :symbol AND timeframe = :timeframe
              AND is_preview = FALSE
            ORDER BY time DESC
            LIMIT 1
        """), {"symbol": symbol, "timeframe": timeframe})
        return result.fetchone()

    async def _get_latest_signal_for_direction(
        self, db: AsyncSession, symbol: str, timeframe: str, is_bullish: bool
    ) -> Optional[tuple]:
        """Get the most recent confirmed signal for a specific direction."""
        result = await db.execute(text("""
            SELECT time, is_bullish, price, id, bar_index
            FROM signals
            WHERE symbol = :symbol AND timeframe = :timeframe
              AND is_preview = FALSE AND is_bullish = :is_bullish
            ORDER BY time DESC
            LIMIT 1
        """), {"symbol": symbol, "timeframe": timeframe, "is_bullish": is_bullish})
        return result.fetchone()

    # ── Staleness ────────────────────────────────────────────

    async def _is_signal_stale(
        self, db: AsyncSession, agent: Agent, signal_id: int, lenient: bool = False
    ) -> bool:
        """
        Check if a signal was detected too long ago to still be actionable.

        Uses ``detected_at`` rather than bar_index to avoid the false-stale
        problem on fast timeframes where pivots confirm many bars back.
        """
        result = await db.execute(text(
            "SELECT detected_at FROM signals WHERE id = :signal_id"
        ), {"signal_id": signal_id})
        row = result.fetchone()

        if not row or not row[0]:
            return False  # Cannot determine, assume fresh

        detected_at = row[0]
        now = datetime.now(timezone.utc)

        if detected_at.tzinfo is None:
            detected_at = detected_at.replace(tzinfo=timezone.utc)

        elapsed_seconds = (now - detected_at).total_seconds()

        tf_seconds = TIMEFRAME_SECONDS.get(agent.timeframe, 60)
        tf_minutes = tf_seconds // 60

        if tf_minutes <= 1:
            max_candles = 15
        elif tf_minutes <= 5:
            max_candles = 10
        elif tf_minutes <= 15:
            max_candles = 8
        elif tf_minutes <= 60:
            max_candles = 6
        else:
            max_candles = 4

        max_seconds = max_candles * tf_seconds
        if lenient:
            max_seconds *= 2

        if elapsed_seconds > max_seconds:
            logger.info(
                f"[{agent.name}] Signal {signal_id} is stale "
                f"(detected {elapsed_seconds:.0f}s ago, "
                f"max {max_seconds}s = {max_candles} candles"
                f"{' lenient' if lenient else ''}), skipping"
            )
            return True

        logger.debug(
            f"[{agent.name}] Signal {signal_id} freshness OK "
            f"(detected {elapsed_seconds:.0f}s ago, max {max_seconds}s)"
        )
        return False

    # ── Duplicate / already-processed ────────────────────────

    async def _is_signal_processed(
        self, db: AsyncSession, agent_id: int, signal_id: int
    ) -> bool:
        """Check if this signal was already used to open/close a position.

        Uses the stable signal key (time + direction) stored directly in
        ``agent_positions``, instead of JOINing on volatile signal IDs.
        """
        signal_result = await db.execute(text(
            "SELECT time, is_bullish FROM signals WHERE id = :signal_id"
        ), {"signal_id": signal_id})
        signal_row = signal_result.fetchone()
        if not signal_row:
            return False

        sig_time, sig_bullish = signal_row

        dup_result = await db.execute(text("""
            SELECT COUNT(*) FROM agent_positions
            WHERE agent_id = :agent_id
              AND entry_signal_time = :sig_time
              AND entry_signal_is_bullish = :sig_bullish
        """), {"agent_id": agent_id, "sig_time": sig_time, "sig_bullish": sig_bullish})
        return dup_result.scalar() > 0

    # ── EMA trend filter ─────────────────────────────────────

    async def _is_ema_trend_against(
        self, db: AsyncSession, agent_name: str,
        symbol: str, timeframe: str, side: str,
    ) -> bool:
        """
        Block trade if the EMA trend on *timeframe* opposes *side*.

        LONG  → trend must NOT be BEARISH.
        SHORT → trend must NOT be BULLISH.
        """
        result = await db.execute(text("""
            SELECT current_trend FROM analysis_runs
            WHERE symbol = :symbol AND timeframe = :timeframe
            ORDER BY created_at DESC
            LIMIT 1
        """), {"symbol": symbol, "timeframe": timeframe})
        row = result.fetchone()

        if not row or not row[0]:
            return False

        trend = row[0]

        if side == "LONG" and trend == "BEARISH":
            logger.info(
                f"[{agent_name}] SKIPPING LONG: EMA trend is BEARISH "
                f"on {timeframe} → trading against the trend"
            )
            return True

        if side == "SHORT" and trend == "BULLISH":
            logger.info(
                f"[{agent_name}] SKIPPING SHORT: EMA trend is BULLISH "
                f"on {timeframe} → trading against the trend"
            )
            return True

        logger.debug(f"[{agent_name}] EMA trend {trend} compatible with {side}")
        return False

    # ── Pivot momentum filter (same TF) ─────────────────────

    async def _is_pivot_momentum_against(
        self, db: AsyncSession, agent_name: str,
        symbol: str, timeframe: str, side: str,
    ) -> bool:
        """
        Skip LONG if the last 3 bearish pivots form lower highs (downtrend).
        Skip SHORT if the last 3 bullish pivots form higher lows (uptrend).
        """
        check_bullish = (side == "SHORT")

        result = await db.execute(text("""
            SELECT price FROM signals
            WHERE symbol = :symbol AND timeframe = :timeframe
              AND is_preview = FALSE AND is_bullish = :is_bullish
            ORDER BY time DESC
            LIMIT 3
        """), {"symbol": symbol, "timeframe": timeframe, "is_bullish": check_bullish})
        rows = result.fetchall()

        if len(rows) < 3:
            return False

        prices = [r[0] for r in rows]
        p_newest, p_middle, p_oldest = prices[0], prices[1], prices[2]

        if side == "LONG":
            if p_newest < p_middle < p_oldest:
                logger.info(
                    f"[{agent_name}] SKIPPING LONG: 3 consecutive lower highs "
                    f"({p_oldest:.2f} > {p_middle:.2f} > {p_newest:.2f}) → downtrend"
                )
                return True
        else:
            if p_newest > p_middle > p_oldest:
                logger.info(
                    f"[{agent_name}] SKIPPING SHORT: 3 consecutive higher lows "
                    f"({p_oldest:.2f} < {p_middle:.2f} < {p_newest:.2f}) → uptrend"
                )
                return True

        return False

    # ── Higher-timeframe trend confirmation ──────────────────

    async def _is_htf_trend_against(
        self, db: AsyncSession, agent_name: str,
        symbol: str, timeframe: str, side: str,
    ) -> bool:
        """
        Relaxed HTF trend confirmation: require at least 1/2 pivot pairs
        to confirm direction; fall back to EMA trend when data is sparse.
        """
        htf_list = HTF_MAP.get(timeframe, [])
        if not htf_list:
            return False

        for htf in htf_list:
            if side == "LONG":
                result = await db.execute(text("""
                    SELECT price FROM signals
                    WHERE symbol = :symbol AND timeframe = :timeframe
                      AND is_preview = FALSE AND is_bullish = TRUE
                    ORDER BY time DESC
                    LIMIT 3
                """), {"symbol": symbol, "timeframe": htf})
                rows = result.fetchall()

                if len(rows) >= 3:
                    prices = [r[0] for r in rows]
                    p_newest, p_middle, p_oldest = prices[0], prices[1], prices[2]
                    confirms = int(p_newest > p_middle) + int(p_middle > p_oldest)
                    if confirms >= 1:
                        logger.info(
                            f"[{agent_name}] LONG OK: HTF {htf} pivots "
                            f"({p_oldest:.2f}, {p_middle:.2f}, {p_newest:.2f}) "
                            f"— {confirms}/2 pairs confirm higher lows ✓"
                        )
                    else:
                        logger.info(
                            f"[{agent_name}] SKIPPING LONG: HTF {htf} showing lower lows "
                            f"({p_oldest:.2f}, {p_middle:.2f}, {p_newest:.2f}) "
                            f"— 0/2 pairs confirm → HTF downtrend"
                        )
                        return True
                elif len(rows) >= 2:
                    p_newest, p_oldest = rows[0][0], rows[1][0]
                    if p_newest < p_oldest:
                        if await self._is_ema_trend_against(db, agent_name, symbol, htf, side):
                            return True
                else:
                    if await self._is_ema_trend_against(db, agent_name, symbol, htf, side):
                        return True

            else:  # SHORT
                result = await db.execute(text("""
                    SELECT price FROM signals
                    WHERE symbol = :symbol AND timeframe = :timeframe
                      AND is_preview = FALSE AND is_bullish = FALSE
                    ORDER BY time DESC
                    LIMIT 3
                """), {"symbol": symbol, "timeframe": htf})
                rows = result.fetchall()

                if len(rows) >= 3:
                    prices = [r[0] for r in rows]
                    p_newest, p_middle, p_oldest = prices[0], prices[1], prices[2]
                    confirms = int(p_newest < p_middle) + int(p_middle < p_oldest)
                    if confirms >= 1:
                        logger.info(
                            f"[{agent_name}] SHORT OK: HTF {htf} pivots "
                            f"({p_oldest:.2f}, {p_middle:.2f}, {p_newest:.2f}) "
                            f"— {confirms}/2 pairs confirm lower highs ✓"
                        )
                    else:
                        logger.info(
                            f"[{agent_name}] SKIPPING SHORT: HTF {htf} showing higher highs "
                            f"({p_oldest:.2f}, {p_middle:.2f}, {p_newest:.2f}) "
                            f"— 0/2 pairs confirm → HTF uptrend"
                        )
                        return True
                elif len(rows) >= 2:
                    p_newest, p_oldest = rows[0][0], rows[1][0]
                    if p_newest > p_oldest:
                        if await self._is_ema_trend_against(db, agent_name, symbol, htf, side):
                            return True
                else:
                    if await self._is_ema_trend_against(db, agent_name, symbol, htf, side):
                        return True

        return False
