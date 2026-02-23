"""
Agent Orchestrator Mixin — core execution cycle (``run_agent_cycle``)
and the scheduler entry-point (``run_all_active_agents``).
"""

import logging
from datetime import datetime, timezone
from typing import Dict

from sqlalchemy.ext.asyncio import AsyncSession

from ...models import Agent
from .constants import TIMEFRAME_SECONDS, HTF_MAP

logger = logging.getLogger(__name__)


class AgentOrchestratorMixin:
    """Scheduler-driven agent execution loop."""

    # Throttle: minimum seconds between consecutive runs per timeframe
    AGENT_CYCLE_SECONDS: Dict[str, int] = {
        '1m': 55, '5m': 55, '15m': 240, '1h': 240, '4h': 840, '1d': 3540,
    }

    # ── Core cycle ───────────────────────────────────────────

    async def run_agent_cycle(self, db: AsyncSession, agent: Agent):
        """
        Execute one cycle of the agent's trading logic:
        1. Fetch fresh OHLCV data for the agent's symbol / timeframe
        2. Refresh analysis (signals)
        3. Get the latest signal
        4. Decide whether to open / close positions
        """
        lock_key = f"agent_cycle_lock:{agent.id}"
        lock = self._redis.lock(lock_key, timeout=120, blocking=False)
        acquired = await lock.acquire(blocking=False)

        if not acquired:
            logger.debug(f"[{agent.name}] Cycle already running in another worker, skipping")
            return

        try:
            logger.info(f"[{agent.name}] Running cycle for {agent.symbol} {agent.timeframe}")

            # 0. Fetch fresh data for agent's own timeframe -----------
            #    Throttled via Redis to match the candle interval.
            fetch_throttle_key = f"agent_fetch:{agent.id}:{agent.timeframe}"
            tf_seconds = TIMEFRAME_SECONDS.get(agent.timeframe, 300)
            fetch_ttl = max(tf_seconds - 15, 30)

            if not await self._redis.get(fetch_throttle_key):
                try:
                    from ..data_ingestion import ingestion_service
                    count = await ingestion_service.fetch_and_store(
                        db, symbol=agent.symbol, timeframe=agent.timeframe,
                        exchange_id="binance", limit=500,
                    )
                    await self._redis.setex(fetch_throttle_key, fetch_ttl, "1")
                    logger.info(
                        f"[{agent.name}] Fetched {count} bars for "
                        f"{agent.symbol} {agent.timeframe}"
                    )
                except Exception as e:
                    logger.warning(f"[{agent.name}] Data fetch failed: {e}")

            # 1. Run fresh analysis ----------------------------------
            from ...schemas import AnalysisRequest
            try:
                request = AnalysisRequest(
                    symbol=agent.symbol,
                    timeframe=agent.timeframe,
                    limit=agent.analysis_limit,
                    sensitivity=agent.sensitivity,
                    signal_mode=agent.signal_mode,
                    confirmation_bars=getattr(agent, 'confirmation_bars', 0),
                    method=getattr(agent, 'method', 'average'),
                    atr_length=getattr(agent, 'atr_length', 5),
                    average_length=getattr(agent, 'average_length', 5),
                )
                from ..analysis_service import analysis_service
                await analysis_service.run_analysis(db, request)
                logger.info(
                    f"[{agent.name}] Analysis refreshed with "
                    f"sensitivity={agent.sensitivity}, mode={agent.signal_mode}"
                )
            except Exception as e:
                logger.warning(f"[{agent.name}] Analysis refresh failed: {e}")

            # 1b. Refresh higher-timeframe analyses -------------------
            htf_list = HTF_MAP.get(agent.timeframe, [])
            if htf_list:
                from ..data_ingestion import ingestion_service
                from ..analysis_service import analysis_service
                for htf in htf_list:
                    try:
                        await ingestion_service.fetch_and_store(
                            db, symbol=agent.symbol, timeframe=htf,
                            exchange_id="binance", limit=500,
                        )
                        htf_request = AnalysisRequest(
                            symbol=agent.symbol, timeframe=htf,
                            limit=500, sensitivity=agent.sensitivity,
                            signal_mode=agent.signal_mode,
                            confirmation_bars=getattr(agent, 'confirmation_bars', 0),
                            method=getattr(agent, 'method', 'average'),
                            atr_length=getattr(agent, 'atr_length', 5),
                            average_length=getattr(agent, 'average_length', 5),
                        )
                        await analysis_service.run_analysis(db, htf_request)
                        logger.debug(f"[{agent.name}] HTF {htf} data fetched & analysis refreshed")
                    except Exception as e:
                        logger.debug(f"[{agent.name}] HTF {htf} refresh failed (non-blocking): {e}")

            # 2. Open positions & current price -----------------------
            open_positions = await self._get_open_positions(db, agent.id)

            current_price = await self._get_current_price(db, agent.symbol, agent.timeframe)
            if not current_price:
                logger.warning(f"[{agent.name}] Cannot determine current price")
                return

            candle_range = await self._get_latest_candle_range(db, agent.symbol, agent.timeframe)
            candle_high = candle_range["high"] if candle_range else current_price
            candle_low = candle_range["low"] if candle_range else current_price

            # 3. Check SL / breakeven / trailing / TP -----------------
            for pos in open_positions:
                if await self._check_stop_loss(db, agent, pos, current_price, candle_low, candle_high):
                    continue
                if await self._check_breakeven(db, agent, pos, current_price):
                    pass
                if await self._check_trailing_stop(db, agent, pos, current_price, candle_low, candle_high):
                    pass
                if await self._check_take_profit(db, agent, pos, current_price, candle_low, candle_high):
                    continue
                await self._update_unrealized_pnl(db, pos, current_price)

            open_positions = await self._get_open_positions(db, agent.id)

            # 5. Signal-based logic -----------------------------------
            has_position = len(open_positions) > 0

            if has_position:
                await self._handle_open_position(db, agent, open_positions[0], current_price)
            else:
                await self._handle_no_position(db, agent, current_price)

        except Exception as e:
            logger.error(f"[{agent.name}] Cycle error: {e}", exc_info=True)
            await self._log(db, agent.id, "CYCLE_ERROR", {"error": str(e)})
        finally:
            try:
                await lock.release()
            except Exception:
                pass

    # ── Sub-routines of run_agent_cycle ───────────────────────

    async def _handle_open_position(
        self, db: AsyncSession, agent: Agent,
        current_pos, current_price: float,
    ):
        """When a position is open, look for the latest OPPOSITE signal."""
        opposite_is_bullish = (current_pos.side == "SHORT")

        opposite_signal = await self._get_latest_signal_for_direction(
            db, agent.symbol, agent.timeframe, opposite_is_bullish
        )
        if not opposite_signal:
            logger.debug(f"[{agent.name}] No opposite signal found, keeping {current_pos.side}")
            return

        opp_time, opp_bullish, opp_price, opp_id, opp_bar_index = opposite_signal

        if await self._is_signal_stale(db, agent, opp_id, lenient=True):
            logger.debug(f"[{agent.name}] Opposite signal {opp_id} is stale, keeping {current_pos.side}")
            await self._log(db, agent.id, "TRADE_SKIPPED", {
                "side": "LONG" if opp_bullish else "SHORT",
                "reason": "signal_stale",
                "signal_time": opp_time.isoformat() if opp_time else None,
                "signal_price": opp_price, "entry_price": current_price,
            })
            return

        entry_time = current_pos.entry_signal_time
        if entry_time and opp_time <= entry_time:
            logger.debug(
                f"[{agent.name}] Opposite signal {opp_id} at {opp_time} "
                f"is older than entry at {entry_time}, ignoring"
            )
            return

        if await self._is_signal_processed(db, agent.id, opp_id):
            logger.debug(f"[{agent.name}] Opposite signal {opp_id} already processed")
            return

        current_price_now = await self._get_current_price(db, agent.symbol, agent.timeframe)
        if not current_price_now:
            current_price_now = current_price

        reason = "BULLISH_REVERSAL" if opp_bullish else "BEARISH_REVERSAL"
        await self._close_position_internal(
            db, current_pos, exit_price=current_price_now,
            exit_signal_id=opp_id, reason=reason,
        )
        logger.info(f"[{agent.name}] Closed {current_pos.side} on {reason}")

        # ── Cooldown ──
        min_gap_bars = 3
        tf_seconds = TIMEFRAME_SECONDS.get(agent.timeframe, 60)
        min_gap_seconds = min_gap_bars * tf_seconds
        if current_pos.opened_at:
            position_duration = (datetime.now(timezone.utc) - current_pos.opened_at).total_seconds()
            if position_duration < min_gap_seconds:
                logger.info(
                    f"[{agent.name}] Position lasted only {position_duration:.0f}s "
                    f"(< {min_gap_seconds}s = {min_gap_bars} bars), "
                    f"skipping immediate re-open to avoid whipsaw"
                )
                await self._log(db, agent.id, "TRADE_SKIPPED", {
                    "side": "LONG" if opp_bullish else "SHORT",
                    "reason": "whipsaw_cooldown",
                    "signal_time": opp_time.isoformat() if opp_time else None,
                    "signal_price": opp_price,
                    "entry_price": current_price_now,
                    "position_duration_s": round(position_duration),
                    "min_gap_s": min_gap_seconds,
                })
                return

        # Open new position in opposite direction
        new_side = "LONG" if opp_bullish else "SHORT"
        if agent.balance <= 0:
            logger.info(f"[{agent.name}] Balance is {agent.balance:.2f}, cannot open {new_side}")
            await self._log(db, agent.id, "TRADE_SKIPPED", {
                "side": new_side, "reason": "no_balance",
                "signal_time": opp_time.isoformat() if opp_time else None,
                "signal_price": opp_price, "entry_price": current_price_now,
                "balance": agent.balance,
            })
            return

        await self._open_position(db, agent, new_side, current_price_now, opp_id)
        logger.info(f"[{agent.name}] Opened {new_side} with {agent.balance:.2f}€ on {reason}")

    async def _handle_no_position(
        self, db: AsyncSession, agent: Agent, current_price: float,
    ):
        """When no position is open, use the latest signal of any direction."""
        latest_signal = await self._get_latest_signal(db, agent.symbol, agent.timeframe)
        if not latest_signal:
            logger.info(f"[{agent.name}] No signals found, skipping")
            return

        signal_time, is_bullish, signal_price, signal_id, signal_bar_index = latest_signal

        if await self._is_signal_stale(db, agent, signal_id, lenient=False):
            await self._log(db, agent.id, "TRADE_SKIPPED", {
                "side": "LONG" if is_bullish else "SHORT",
                "reason": "signal_stale",
                "signal_time": signal_time.isoformat() if signal_time else None,
                "signal_price": signal_price, "entry_price": current_price,
            })
            return

        if await self._is_signal_processed(db, agent.id, signal_id):
            logger.debug(f"[{agent.name}] Signal {signal_id} already processed")
            return

        new_side = "LONG" if is_bullish else "SHORT"
        if agent.balance <= 0:
            logger.info(f"[{agent.name}] Balance is {agent.balance:.2f}, cannot open position")
            await self._log(db, agent.id, "TRADE_SKIPPED", {
                "side": new_side, "reason": "no_balance",
                "signal_time": signal_time.isoformat() if signal_time else None,
                "signal_price": signal_price, "entry_price": current_price,
                "balance": agent.balance,
            })
            return

        await self._open_position(db, agent, new_side, current_price, signal_id)
        logger.info(
            f"[{agent.name}] Opened {new_side} with {agent.balance:.2f}€ "
            f"on {'bullish' if is_bullish else 'bearish'} reversal"
        )

    # ── Scheduler entry-point ────────────────────────────────

    async def run_all_active_agents(self, db: AsyncSession):
        """Run one cycle for all active agents (called by scheduler)."""
        agents = await self.get_all_agents(db)
        active = [a for a in agents if a.is_active]
        if not active:
            return

        from ...database import async_session

        ran = 0
        for agent in active:
            try:
                throttle_key = f"agent_throttle:{agent.id}"
                min_gap = self.AGENT_CYCLE_SECONDS.get(agent.timeframe, 240)
                if await self._redis.get(throttle_key):
                    continue

                await self._redis.setex(throttle_key, min_gap, "1")

                async with async_session() as agent_db:
                    await self.run_agent_cycle(agent_db, agent)
                ran += 1
            except Exception as e:
                logger.error(f"Agent {agent.name} failed: {e}", exc_info=True)

        if ran:
            logger.info(f"Agent cycle: {ran}/{len(active)} agents executed")
