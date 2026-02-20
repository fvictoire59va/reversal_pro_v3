"""
Agent Broker Service — autonomous trading agents that monitor signals
and manage positions on Hyperliquid.

Each agent:
  1. Polls signals from DB at its configured timeframe interval
  2. Opens LONG on bullish reversal, closes on bearish reversal
  3. Opens SHORT on bearish reversal, closes on bullish reversal
  4. Calculates SL from previous pivot, TP with TF-adaptive R:R (1.5–3:1)
  5. Works in paper (simulation) or live mode

Recommended future decomposition (when this file grows further):
  - AgentCrudService      — create / update / delete / list agents
  - PositionManager       — open / close / partial-TP / breakeven logic
  - RiskManager           — SL/TP calculation, trailing stop, R:R rules
  - SignalEvaluator       — duplicate-check, cooldown, signal scoring
  - AgentOrchestrator     — scheduler loop that ties the above together
  - agent_performance.py  — already extracted (performance tree computation)
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional, List, Dict

import redis.asyncio as aioredis
from sqlalchemy import text, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Agent, AgentPosition, AgentLog, Signal
from ..config import get_settings
from .hyperliquid_client import hyperliquid_client
from .analysis_service import analysis_service
from .telegram_service import telegram_service

logger = logging.getLogger(__name__)

# Timeframe → seconds mapping
TIMEFRAME_SECONDS = {
    "1m": 60, "5m": 300, "15m": 900, "30m": 1800,
    "1h": 3600, "4h": 14400, "1d": 86400,
}

# Higher-timeframe map: for each TF, which HTF to check for trend confirmation
# Only 1 level above to keep it simple and avoid over-filtering
HTF_MAP = {
    "1m":  ["5m"],
    "5m":  ["15m"],
    "15m": ["1h"],
    "30m": ["1h"],
    "1h":  ["4h"],
    "4h":  ["1d"],
    "1d":  [],           # No higher TF to check
}


class AgentBrokerService:
    """Manages all trading agents and their autonomous execution."""

    def __init__(self):
        self._running_agents: Dict[int, bool] = {}
        settings = get_settings()
        self._redis = aioredis.from_url(settings.redis_url)

    # ── Agent CRUD ───────────────────────────────────────────
    async def create_agent(self, db: AsyncSession, symbol: str, timeframe: str,
                           trade_amount: float = 100.0, mode: str = "paper",
                           sensitivity: str = "Medium", signal_mode: str = "Confirmed Only",
                           analysis_limit: int = 500) -> Agent:
        """Create a new agent with auto-generated name."""
        # Create agent with temporary name
        agent = Agent(
            name=f"agent_temp_{datetime.now(timezone.utc).timestamp()}",
            symbol=symbol,
            timeframe=timeframe,
            trade_amount=trade_amount,
            balance=trade_amount,
            is_active=False,
            mode=mode,
            sensitivity=sensitivity,
            signal_mode=signal_mode,
            analysis_limit=analysis_limit,
        )
        db.add(agent)
        await db.flush()  # Get the auto-generated ID
        
        # Update name with actual ID
        agent.name = f"agent_{agent.id}"
        await db.commit()
        await db.refresh(agent)

        await self._log(db, agent.id, "AGENT_CREATED", {
            "symbol": symbol, "timeframe": timeframe,
            "trade_amount": trade_amount, "mode": mode,
            "sensitivity": sensitivity, "signal_mode": signal_mode, "analysis_limit": analysis_limit,
        })

        logger.info(f"Agent created: {agent.name} ({symbol} {timeframe} {mode} {sensitivity})")
        return agent

    async def delete_agent(self, db: AsyncSession, agent_id: int) -> bool:
        """Delete agent - all positions and logs will be cascade deleted."""
        agent = await db.get(Agent, agent_id)
        if not agent:
            return False

        agent_name = agent.name
        
        # Stop agent if running
        self._running_agents.pop(agent_id, None)
        
        # Delete agent - cascade will automatically delete all positions and logs
        await db.delete(agent)
        await db.commit()

        logger.info(f"Agent deleted: {agent_name} (all positions and logs cascade deleted)")
        return True

    async def toggle_agent(self, db: AsyncSession, agent_id: int) -> Optional[Agent]:
        """Toggle agent active/inactive."""
        agent = await db.get(Agent, agent_id)
        if not agent:
            return None

        agent.is_active = not agent.is_active
        agent.updated_at = datetime.now(timezone.utc)
        await db.commit()
        await db.refresh(agent)

        status = "ACTIVATED" if agent.is_active else "DEACTIVATED"
        await self._log(db, agent.id, f"AGENT_{status}", {})

        if not agent.is_active:
            self._running_agents.pop(agent_id, None)

        # Send Telegram notification
        if agent.is_active:
            await telegram_service.notify_agent_activated(
                agent.name, agent.symbol, agent.timeframe, agent.mode
            )
        else:
            await telegram_service.notify_agent_deactivated(agent.name)

        logger.info(f"Agent {agent.name}: {status}")
        return agent

    async def get_all_agents(self, db: AsyncSession) -> List[Agent]:
        """Get all agents."""
        result = await db.execute(
            select(Agent).order_by(Agent.created_at.desc())
        )
        return list(result.scalars().all())

    async def get_agent(self, db: AsyncSession, agent_id: int) -> Optional[Agent]:
        return await db.get(Agent, agent_id)

    # ── Position management ──────────────────────────────────
    async def get_all_open_positions(self, db: AsyncSession) -> List[AgentPosition]:
        """Get all open positions across all agents."""
        result = await db.execute(
            select(AgentPosition)
            .where(AgentPosition.status == "OPEN")
            .order_by(AgentPosition.opened_at.desc())
        )
        return list(result.scalars().all())

    async def get_agent_positions(self, db: AsyncSession, agent_id: int,
                                  status: Optional[str] = None) -> List[AgentPosition]:
        """Get positions for a specific agent."""
        query = select(AgentPosition).where(AgentPosition.agent_id == agent_id)
        if status:
            query = query.where(AgentPosition.status == status)
        query = query.order_by(AgentPosition.opened_at.desc())

        result = await db.execute(query)
        return list(result.scalars().all())

    async def close_position_manually(self, db: AsyncSession, position_id: int) -> Optional[AgentPosition]:
        """Manually close a position from the web interface."""
        pos = await db.get(AgentPosition, position_id)
        if not pos or pos.status != "OPEN":
            return None

        return await self._close_position_internal(db, pos, reason="MANUAL_CLOSE")

    async def _get_open_positions(self, db: AsyncSession, agent_id: int) -> List[AgentPosition]:
        result = await db.execute(
            select(AgentPosition)
            .where(AgentPosition.agent_id == agent_id, AgentPosition.status == "OPEN")
        )
        return list(result.scalars().all())

    # ── Agent logs ───────────────────────────────────────────
    async def get_agent_logs(self, db: AsyncSession, agent_id: int,
                             limit: int = 50) -> List[AgentLog]:
        result = await db.execute(
            select(AgentLog)
            .where(AgentLog.agent_id == agent_id)
            .order_by(AgentLog.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    # ── Core agent execution loop ────────────────────────────
    async def run_agent_cycle(self, db: AsyncSession, agent: Agent):
        """
        Execute one cycle of the agent's trading logic:
        1. Refresh analysis (signals) for the agent's symbol/timeframe
        2. Get the latest signal
        3. Decide whether to open/close positions
        
        Uses Redis distributed lock to prevent concurrent execution
        across multiple uvicorn workers.
        """
        # Use Redis distributed lock (survives DB commits unlike pg_try_advisory_xact_lock)
        lock_key = f"agent_cycle_lock:{agent.id}"
        lock = self._redis.lock(lock_key, timeout=120, blocking=False)
        acquired = await lock.acquire(blocking=False)

        if not acquired:
            logger.debug(f"[{agent.name}] Cycle already running in another worker, skipping")
            return

        try:
            logger.info(f"[{agent.name}] Running cycle for {agent.symbol} {agent.timeframe}")

            # 1. Run fresh analysis to ensure signals are up-to-date
            from ..schemas import AnalysisRequest
            try:
                request = AnalysisRequest(
                    symbol=agent.symbol,
                    timeframe=agent.timeframe,
                    limit=agent.analysis_limit,
                    sensitivity=agent.sensitivity,
                    signal_mode=agent.signal_mode,
                )
                await analysis_service.run_analysis(db, request)
                logger.info(f"[{agent.name}] Analysis refreshed with sensitivity={agent.sensitivity}, mode={agent.signal_mode}")
            except Exception as e:
                logger.warning(f"[{agent.name}] Analysis refresh failed: {e}")

            # 1b. Also refresh higher-timeframe analyses for trend confirmation
            #     Fetch OHLCV data from exchange first, then run analysis
            htf_list = HTF_MAP.get(agent.timeframe, [])
            if htf_list:
                from .data_ingestion import ingestion_service
                for htf in htf_list:
                    try:
                        # Fetch OHLCV data for the HTF (may not be in watchlist)
                        await ingestion_service.fetch_and_store(
                            db, symbol=agent.symbol, timeframe=htf,
                            exchange_id="binance", limit=500,
                        )
                        htf_request = AnalysisRequest(
                            symbol=agent.symbol,
                            timeframe=htf,
                            limit=500,
                            sensitivity=agent.sensitivity,
                            signal_mode=agent.signal_mode,
                        )
                        await analysis_service.run_analysis(db, htf_request)
                        logger.debug(f"[{agent.name}] HTF {htf} data fetched & analysis refreshed")
                    except Exception as e:
                        logger.debug(f"[{agent.name}] HTF {htf} refresh failed (non-blocking): {e}")

            # 2. Get open positions and current price
            open_positions = await self._get_open_positions(db, agent.id)

            # Get current price + high/low for SL/TP checks
            current_price = await self._get_current_price(db, agent.symbol, agent.timeframe)
            if not current_price:
                logger.warning(f"[{agent.name}] Cannot determine current price")
                return

            candle_range = await self._get_latest_candle_range(db, agent.symbol, agent.timeframe)
            candle_high = candle_range["high"] if candle_range else current_price
            candle_low = candle_range["low"] if candle_range else current_price

            # 3. Check stop losses and take profits on open positions
            for pos in open_positions:
                if await self._check_stop_loss(db, agent, pos, current_price, candle_low, candle_high):
                    continue  # Position was stopped out
                if await self._check_breakeven(db, agent, pos, current_price):
                    pass  # SL moved to breakeven, position still open
                # Trailing stop: progressively lock in profits after breakeven
                if await self._check_trailing_stop(db, agent, pos, current_price, candle_low, candle_high):
                    pass  # SL trailed closer to current price
                if await self._check_take_profit(db, agent, pos, current_price, candle_low, candle_high):
                    continue  # Position hit take profit (full or partial)
                # Update unrealized PnL for surviving open positions
                await self._update_unrealized_pnl(db, pos, current_price)

            # Refresh open positions after SL checks
            open_positions = await self._get_open_positions(db, agent.id)

            # 5. Signal-based logic
            #
            # KEY DESIGN: When a position is open, the agent actively
            # looks for the latest OPPOSITE-direction signal, not just
            # the overall latest signal.  This prevents the scenario
            # where a newer same-direction signal masks an intermediate
            # opposite signal that should have closed the position.

            has_position = len(open_positions) > 0

            if has_position:
                # ── Position is open: look for the latest OPPOSITE signal ──
                current_pos = open_positions[0]
                opposite_is_bullish = (current_pos.side == "SHORT")  # LONG→bearish, SHORT→bullish

                # Get the latest signal in the opposite direction
                opposite_signal = await self._get_latest_signal_for_direction(
                    db, agent.symbol, agent.timeframe, opposite_is_bullish
                )

                if not opposite_signal:
                    logger.debug(f"[{agent.name}] No opposite signal found, keeping {current_pos.side}")
                    return

                opp_time, opp_bullish, opp_price, opp_id, opp_bar_index = opposite_signal

                # Check staleness (relaxed for closing signals: 2x normal threshold)
                if await self._is_signal_stale(db, agent, opp_id, lenient=True):
                    logger.debug(f"[{agent.name}] Opposite signal {opp_id} is stale, keeping {current_pos.side}")
                    await self._log(db, agent.id, "TRADE_SKIPPED", {
                        "side": "LONG" if opp_bullish else "SHORT",
                        "reason": "signal_stale",
                        "signal_time": opp_time.isoformat() if opp_time else None,
                        "signal_price": opp_price,
                        "entry_price": current_price,
                    })
                    return

                # The opposite signal must be NEWER than the entry signal
                # (so we don't close on an old opposite signal that preceded the entry)
                entry_time = current_pos.entry_signal_time
                if entry_time and opp_time <= entry_time:
                    logger.debug(
                        f"[{agent.name}] Opposite signal {opp_id} at {opp_time} "
                        f"is older than entry at {entry_time}, ignoring"
                    )
                    return

                # Check if already processed
                if await self._is_signal_processed(db, agent.id, opp_id):
                    logger.debug(f"[{agent.name}] Opposite signal {opp_id} already processed")
                    return

                # Get current price for closing/opening
                current_price_now = await self._get_current_price(db, agent.symbol, agent.timeframe)
                if not current_price_now:
                    current_price_now = current_price

                # Close current position on opposite reversal
                reason = "BULLISH_REVERSAL" if opp_bullish else "BEARISH_REVERSAL"
                await self._close_position_internal(
                    db, current_pos, exit_price=current_price_now,
                    exit_signal_id=opp_id, reason=reason
                )
                logger.info(f"[{agent.name}] Closed {current_pos.side} on {reason}")

                # ── Cooldown: check minimum time between close and re-open ──
                # Avoids whipsaw when reversals flip-flop too fast
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
                        "side": new_side,
                        "reason": "no_balance",
                        "signal_time": opp_time.isoformat() if opp_time else None,
                        "signal_price": opp_price,
                        "entry_price": current_price_now,
                        "balance": agent.balance,
                    })
                    return

                await self._open_position(db, agent, new_side, current_price_now, opp_id)
                logger.info(f"[{agent.name}] Opened {new_side} with {agent.balance:.2f}€ on {reason}")

            else:
                # ── No position open: use the latest signal of any direction ──
                latest_signal = await self._get_latest_signal(db, agent.symbol, agent.timeframe)
                if not latest_signal:
                    logger.info(f"[{agent.name}] No signals found, skipping")
                    return

                signal_time = latest_signal[0]
                is_bullish = latest_signal[1]
                signal_price = latest_signal[2]
                signal_id = latest_signal[3]
                signal_bar_index = latest_signal[4]

                # Staleness check (strict)
                if await self._is_signal_stale(db, agent, signal_id, lenient=False):
                    await self._log(db, agent.id, "TRADE_SKIPPED", {
                        "side": "LONG" if is_bullish else "SHORT",
                        "reason": "signal_stale",
                        "signal_time": signal_time.isoformat() if signal_time else None,
                        "signal_price": signal_price,
                        "entry_price": current_price,
                    })
                    return

                # Already processed?
                if await self._is_signal_processed(db, agent.id, signal_id):
                    logger.debug(f"[{agent.name}] Signal {signal_id} already processed")
                    return

                # Open new position
                new_side = "LONG" if is_bullish else "SHORT"
                if agent.balance <= 0:
                    logger.info(f"[{agent.name}] Balance is {agent.balance:.2f}, cannot open position")
                    await self._log(db, agent.id, "TRADE_SKIPPED", {
                        "side": new_side,
                        "reason": "no_balance",
                        "signal_time": signal_time.isoformat() if signal_time else None,
                        "signal_price": signal_price,
                        "entry_price": current_price,
                        "balance": agent.balance,
                    })
                    return

                await self._open_position(db, agent, new_side, current_price, signal_id)
                logger.info(f"[{agent.name}] Opened {new_side} with {agent.balance:.2f}€ on {'bullish' if is_bullish else 'bearish'} reversal")

        except Exception as e:
            logger.error(f"[{agent.name}] Cycle error: {e}", exc_info=True)
            await self._log(db, agent.id, "CYCLE_ERROR", {"error": str(e)})
        finally:
            try:
                await lock.release()
            except Exception:
                pass  # Lock may have expired

    # Throttle: minimum seconds between consecutive runs per timeframe
    AGENT_CYCLE_SECONDS: Dict[str, int] = {
        '1m': 55, '5m': 55, '15m': 240, '1h': 240, '4h': 840, '1d': 3540,
    }

    async def run_all_active_agents(self, db: AsyncSession):
        """Run one cycle for all active agents. Called by the scheduler.

        Each agent gets its own DB session so that a failed commit in one
        agent cannot corrupt the session for subsequent agents.
        """
        agents = await self.get_all_agents(db)
        active = [a for a in agents if a.is_active]

        if not active:
            return

        from ..database import async_session

        ran = 0
        for agent in active:
            try:
                # Throttle: skip agent if not enough time since last run
                throttle_key = f"agent_throttle:{agent.id}"
                min_gap = self.AGENT_CYCLE_SECONDS.get(agent.timeframe, 240)
                if await self._redis.get(throttle_key):
                    continue  # Still within cooldown period

                # Set throttle with TTL (expires automatically)
                await self._redis.setex(throttle_key, min_gap, "1")

                # Each agent gets its own DB session to prevent
                # one agent's error from corrupting other agents
                async with async_session() as agent_db:
                    await self.run_agent_cycle(agent_db, agent)
                ran += 1
            except Exception as e:
                logger.error(f"Agent {agent.name} failed: {e}", exc_info=True)

        if ran:
            logger.info(f"Agent cycle: {ran}/{len(active)} agents executed")

    # ── Internal helpers ─────────────────────────────────────
    async def _get_latest_signal(self, db: AsyncSession, symbol: str,
                                 timeframe: str) -> Optional[tuple]:
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

    async def _get_latest_signal_for_direction(self, db: AsyncSession, symbol: str,
                                                timeframe: str, is_bullish: bool) -> Optional[tuple]:
        """Get the most recent confirmed signal for a specific direction (bullish/bearish)."""
        result = await db.execute(text("""
            SELECT time, is_bullish, price, id, bar_index
            FROM signals
            WHERE symbol = :symbol AND timeframe = :timeframe
              AND is_preview = FALSE AND is_bullish = :is_bullish
            ORDER BY time DESC
            LIMIT 1
        """), {"symbol": symbol, "timeframe": timeframe, "is_bullish": is_bullish})
        return result.fetchone()

    async def _is_signal_stale(self, db: AsyncSession, agent: Agent,
                               signal_id: int, lenient: bool = False) -> bool:
        """
        Check if a signal was detected too long ago to still be actionable.

        Uses the signal's `detected_at` timestamp (when the analysis engine
        first discovered this signal) rather than the pivot's bar position.

        The old bar_index approach was flawed for fast timeframes: a pivot
        naturally forms N bars before confirmation, so a freshly-confirmed
        signal could appear "30 bars old" purely because the pivot was far
        back — even though detection just happened.

        Parameters
        ----------
        lenient : if True, doubles the threshold. Used for closing signals
                  where we are more tolerant.
        """
        # Get the signal's detected_at timestamp
        result = await db.execute(text("""
            SELECT detected_at FROM signals WHERE id = :signal_id
        """), {"signal_id": signal_id})
        row = result.fetchone()

        if not row or not row[0]:
            return False  # Cannot determine, assume fresh

        detected_at = row[0]
        now = datetime.now(timezone.utc)

        # Normalize timezone
        if detected_at.tzinfo is None:
            detected_at = detected_at.replace(tzinfo=timezone.utc)

        elapsed_seconds = (now - detected_at).total_seconds()

        # Threshold = max_candles × candle_interval
        # Lower TFs get more candles because:
        #  - zigzag confirmation takes more bars relative to price structure
        #  - scheduler runs every N minutes, so 1m signals need headroom
        #  - intraday signals are time-sensitive but still need multiple
        #    agent cycles to be picked up
        tf_seconds = TIMEFRAME_SECONDS.get(agent.timeframe, 60)
        tf_minutes = tf_seconds // 60

        if tf_minutes <= 1:        # 1m
            max_candles = 15       # 15 min — 3 agent cycles
        elif tf_minutes <= 5:      # 5m
            max_candles = 10       # 50 min
        elif tf_minutes <= 15:     # 15m
            max_candles = 8        # 2h
        elif tf_minutes <= 60:     # 30m–1h
            max_candles = 6        # 3–6h
        else:                      # 4h+
            max_candles = 4

        max_seconds = max_candles * tf_seconds

        # Double threshold for closing signals (lenient mode)
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

    async def _get_previous_pivot(self, db: AsyncSession, symbol: str,
                                  timeframe: str, is_bullish: bool,
                                  before_time: datetime) -> Optional[float]:
        """Get the previous opposite pivot price for SL calculation."""
        result = await db.execute(text("""
            SELECT price
            FROM signals
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

    async def _get_latest_candle_range(self, db: AsyncSession, symbol: str, timeframe: str) -> Optional[dict]:
        """Get high and low from the latest OHLCV candle (for SL/TP wick detection)."""
        result = await db.execute(text("""
            SELECT high, low, close FROM ohlcv
            WHERE symbol = :symbol AND timeframe = :timeframe
            ORDER BY time DESC LIMIT 1
        """), {"symbol": symbol, "timeframe": timeframe})
        row = result.fetchone()
        if row:
            return {"high": row[0], "low": row[1], "close": row[2]}
        return None

    async def _get_current_price(self, db: AsyncSession, symbol: str, timeframe: str) -> Optional[float]:
        """Get current price from the latest OHLCV candle, or from Hyperliquid."""
        # Try DB first (latest candle close)
        result = await db.execute(text("""
            SELECT close FROM ohlcv
            WHERE symbol = :symbol AND timeframe = :timeframe
            ORDER BY time DESC LIMIT 1
        """), {"symbol": symbol, "timeframe": timeframe})
        row = result.fetchone()
        if row:
            return row[0]

        # Fallback: Hyperliquid mid price
        return await hyperliquid_client.get_mid_price(symbol)

    async def _get_current_atr(self, db: AsyncSession, symbol: str, timeframe: str) -> Optional[float]:
        """Get current ATR from the latest analysis run."""
        result = await db.execute(text("""
            SELECT current_atr FROM analysis_runs
            WHERE symbol = :symbol AND timeframe = :timeframe
            ORDER BY created_at DESC LIMIT 1
        """), {"symbol": symbol, "timeframe": timeframe})
        row = result.fetchone()
        return row[0] if row else None

    # ── Timeframe-adaptive parameters ──────────────────────
    # Smaller TFs → confirmation arrives late, so reduce TP target
    # and keep SL tighter for realistic risk/reward.
    TF_PARAMS = {
        # tf_minutes: (R:R ratio, ATR mult for SL fallback, max SL %, fallback SL %)
        1:    (1.5, 1.0, 0.30, 0.50),   # 1m  — fast scalp
        5:    (2.0, 1.2, 0.50, 0.80),   # 5m  — quick swing
        15:   (2.5, 1.3, 0.80, 1.20),   # 15m — intraday
        60:   (3.0, 1.5, 1.50, 2.00),   # 1h  — swing
        240:  (3.0, 1.5, 3.00, 3.00),   # 4h  — position
        1440: (3.0, 1.5, 5.00, 5.00),   # 1d  — long-term
    }

    def _get_tf_params(self, timeframe: str) -> tuple:
        """Return (rr_ratio, atr_mult, max_sl_pct, fallback_sl_pct) for a TF."""
        tf_min = self._timeframe_to_minutes(timeframe)
        # Find the closest matching TF bucket (<=)
        best = (3.0, 1.5, 5.0, 5.0)
        for minutes in sorted(self.TF_PARAMS.keys()):
            if tf_min <= minutes:
                best = self.TF_PARAMS[minutes]
                break
        else:
            best = self.TF_PARAMS[1440]  # largest bucket
        return best

    def _calculate_sl_tp(self, side: str, entry_price: float,
                         pivot_price: Optional[float], atr: Optional[float],
                         timeframe: str = "1h",
                         zone_tp: Optional[float] = None) -> tuple:
        """
        Calculate Stop Loss, TP1, and TP2 with timeframe-adaptive R:R.

        TP1 (take profit 1): target for first partial close (50%)
        TP2 (take profit 2): extended target for remaining 50%

        If a Supply/Demand zone target is provided (zone_tp), it is used
        as TP1 when it offers a better R:R than the default fixed ratio.
        TP2 is always set to 1.5× the TP1 distance.

        Returns (sl, tp1, tp2)
        """
        rr_ratio, atr_mult, max_sl_pct, fallback_sl_pct = self._get_tf_params(timeframe)

        if side == "LONG":
            if pivot_price and pivot_price < entry_price:
                sl = pivot_price
            elif atr:
                sl = entry_price - (atr_mult * atr)
            else:
                sl = entry_price * (1 - fallback_sl_pct / 100)

            # Cap SL distance to max_sl_pct of entry price
            max_sl_dist = entry_price * (max_sl_pct / 100)
            if (entry_price - sl) > max_sl_dist:
                sl = entry_price - max_sl_dist

            risk = entry_price - sl
            default_tp = entry_price + (rr_ratio * risk)

            # Use zone-based TP if it's above entry and offers reasonable R:R
            if zone_tp and zone_tp > entry_price:
                zone_reward = zone_tp - entry_price
                zone_rr = zone_reward / risk if risk > 0 else 0
                # Accept zone TP if R:R >= 1.0 (at least 1:1)
                if zone_rr >= 1.0:
                    tp1 = zone_tp
                else:
                    tp1 = default_tp
            else:
                tp1 = default_tp

            # TP2 = 1.5× the TP1 distance beyond entry
            tp1_dist = tp1 - entry_price
            tp2 = entry_price + (1.5 * tp1_dist)

        else:  # SHORT
            if pivot_price and pivot_price > entry_price:
                sl = pivot_price
            elif atr:
                sl = entry_price + (atr_mult * atr)
            else:
                sl = entry_price * (1 + fallback_sl_pct / 100)

            # Cap SL distance to max_sl_pct of entry price
            max_sl_dist = entry_price * (max_sl_pct / 100)
            if (sl - entry_price) > max_sl_dist:
                sl = entry_price + max_sl_dist

            risk = sl - entry_price
            default_tp = entry_price - (rr_ratio * risk)

            # Use zone-based TP if it's below entry and offers reasonable R:R
            if zone_tp and zone_tp < entry_price:
                zone_reward = entry_price - zone_tp
                zone_rr = zone_reward / risk if risk > 0 else 0
                if zone_rr >= 1.0:
                    tp1 = zone_tp
                else:
                    tp1 = default_tp
            else:
                tp1 = default_tp

            # TP2 = 1.5× the TP1 distance beyond entry
            tp1_dist = entry_price - tp1
            tp2 = entry_price - (1.5 * tp1_dist)

        return round(sl, 2), round(tp1, 2), round(tp2, 2)

    # ── P6: Zone-based TP target ──────────────────────────────
    async def _get_zone_tp(self, db: AsyncSession, symbol: str,
                           timeframe: str, side: str,
                           entry_price: float) -> Optional[float]:
        """
        Query persisted S/D zones to find a TP target based on market structure.

        For LONG: find the nearest Supply zone above entry → TP = zone bottom_price
        For SHORT: find the nearest Demand zone below entry → TP = zone top_price

        Returns the zone-derived TP price, or None if no suitable zone is found.
        """
        if side == "LONG":
            result = await db.execute(text("""
                SELECT bottom_price FROM zones
                WHERE symbol = :symbol AND timeframe = :timeframe
                  AND zone_type = 'SUPPLY' AND center_price > :entry_price
                ORDER BY center_price ASC
                LIMIT 1
            """), {"symbol": symbol, "timeframe": timeframe, "entry_price": entry_price})
        else:  # SHORT
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

    # ── P7: EMA Trend Filter ─────────────────────────────────
    async def _is_ema_trend_against(self, db: AsyncSession, agent_name: str,
                                     symbol: str, timeframe: str,
                                     side: str) -> bool:
        """
        Check the current EMA trend from the latest analysis run.

        Rules:
        - LONG  → trend must NOT be BEARISH (BULLISH or NEUTRAL allowed)
        - SHORT → trend must NOT be BULLISH (BEARISH or NEUTRAL allowed)

        This prevents trading against the established EMA trend direction
        while allowing entries during neutral/transitional phases.
        """
        result = await db.execute(text("""
            SELECT current_trend FROM analysis_runs
            WHERE symbol = :symbol AND timeframe = :timeframe
            ORDER BY created_at DESC
            LIMIT 1
        """), {"symbol": symbol, "timeframe": timeframe})
        row = result.fetchone()

        if not row or not row[0]:
            return False  # No trend data → allow trade

        trend = row[0]  # "BULLISH", "BEARISH", or "NEUTRAL"

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

    # ── Trailing Stop Parameters ─────────────────────────────
    # trail_atr_mult: how many ATRs behind the best price the trailing SL sits
    # trail_activation_mult: how many × risk the price must move before trailing starts
    TRAIL_PARAMS = {
        #  tf_min: (trail_atr_mult, activation_risk_mult)
        1:    (0.8,  1.0),   # 1m  — tight trail, activates at 1× risk
        5:    (1.0,  1.0),   # 5m  — trail at 1× ATR after 1× risk
        15:   (1.2,  1.5),   # 15m — slightly wider
        60:   (1.5,  1.5),   # 1h  — standard
        240:  (1.5,  2.0),   # 4h  — wider trail
        1440: (2.0,  2.0),   # 1d  — widest
    }

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

    # ── P15: Trailing Stop Mechanism ─────────────────────────
    async def _check_trailing_stop(self, db: AsyncSession, agent: Agent,
                                   pos: AgentPosition,
                                   current_price: float,
                                   candle_low: float = None,
                                   candle_high: float = None) -> bool:
        """
        Trailing stop: progressively moves the SL to lock in profits
        as the price moves in the position's direction.

        Activation: after breakeven has been triggered (SL >= entry for LONG,
        SL <= entry for SHORT), i.e. the position is already protected.

        Trail distance: ATR × trail_atr_mult (timeframe-adaptive).
        The SL never moves backward — it only ratchets in the profit direction.

        Returns True if the trailing stop was updated, False otherwise.
        """
        # Only trail if breakeven is already active
        if pos.side == "LONG" and pos.stop_loss < pos.entry_price:
            return False
        if pos.side == "SHORT" and pos.stop_loss > pos.entry_price:
            return False

        # Get ATR for trail distance calculation
        atr = await self._get_current_atr(db, agent.symbol, agent.timeframe)
        if not atr or atr <= 0:
            return False

        trail_atr_mult, _ = self._get_trail_params(agent.timeframe)
        trail_distance = atr * trail_atr_mult

        # Use candle extreme for best-price tracking (catches wicks)
        if pos.side == "LONG":
            extreme = candle_high if candle_high is not None else current_price
            new_best = max(pos.best_price or pos.entry_price, extreme)
        else:  # SHORT
            extreme = candle_low if candle_low is not None else current_price
            new_best = min(pos.best_price or pos.entry_price, extreme)

        # Update best price if improved
        if new_best != (pos.best_price or pos.entry_price):
            pos.best_price = new_best

        # Calculate new trailing SL
        if pos.side == "LONG":
            new_sl = round(new_best - trail_distance, 2)
            # SL must be above current SL (never move backward)
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
                    "position_id": pos.id,
                    "side": pos.side,
                    "old_sl": old_sl,
                    "new_sl": new_sl,
                    "best_price": new_best,
                    "trail_distance": round(trail_distance, 2),
                    "atr": round(atr, 2),
                    "current_price": current_price,
                })
                return True
        else:  # SHORT
            new_sl = round(new_best + trail_distance, 2)
            # SL must be below current SL (never move backward for SHORT)
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
                    "position_id": pos.id,
                    "side": pos.side,
                    "old_sl": old_sl,
                    "new_sl": new_sl,
                    "best_price": new_best,
                    "trail_distance": round(trail_distance, 2),
                    "atr": round(atr, 2),
                    "current_price": current_price,
                })
                return True

        # Persist best_price even if SL didn't change
        await db.commit()
        return False

    # ── P10: Breakeven Mechanism ──────────────────────────────
    async def _check_breakeven(self, db: AsyncSession, agent: Agent,
                               pos: AgentPosition,
                               current_price: float) -> bool:
        """
        Move SL to breakeven (entry price) when the position has moved
        >= 1× risk in profit direction.

        This protects accumulated profit and eliminates downside risk
        on winning trades.

        Returns True if breakeven was activated, False otherwise.
        """
        # Skip if already at breakeven or better
        if pos.side == "LONG" and pos.stop_loss >= pos.entry_price:
            return False
        if pos.side == "SHORT" and pos.stop_loss <= pos.entry_price:
            return False

        # Calculate the original risk distance
        original_sl = pos.original_stop_loss or pos.stop_loss
        risk = abs(pos.entry_price - original_sl)

        if risk <= 0:
            return False

        # Check if price has moved >= 1× risk in our favor
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
                    "position_id": pos.id,
                    "side": pos.side,
                    "old_sl": old_sl,
                    "new_sl": pos.entry_price,
                    "current_price": current_price,
                    "risk": round(risk, 2),
                })
                return True

        else:  # SHORT
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
                    "position_id": pos.id,
                    "side": pos.side,
                    "old_sl": old_sl,
                    "new_sl": pos.entry_price,
                    "current_price": current_price,
                    "risk": round(risk, 2),
                })
                return True

        return False

    def _is_risk_too_small(self, agent_name: str, side: str, entry_price: float,
                           sl: float, timeframe: str) -> bool:
        """
        Reject the trade if the risk (distance entry→SL) is too small
        relative to the entry price.  When two opposite reversals are
        very close in price the SL sits right next to the entry, making
        a profitable trade virtually impossible.

        Minimum risk thresholds (% of entry price):
          1m–5m  : 0.15%
          15m    : 0.25%
          1h+    : 0.40%
        """
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

    async def _is_pivot_momentum_against(self, db: AsyncSession, agent_name: str,
                                         symbol: str, timeframe: str,
                                         side: str) -> bool:
        """
        Check if the last 3 pivots signal momentum AGAINST the intended trade.

        ── LONG filter ──
        Query the last 3 BEARISH reversals (swing highs).
        If each successive high is LOWER than the previous one (lower highs),
        the market is in a downtrend → skip the LONG.

        ── SHORT filter ──
        Query the last 3 BULLISH reversals (swing lows).
        If each successive low is HIGHER than the previous one (higher lows),
        the market is in an uptrend → skip the SHORT.
        """
        # For LONG: check bearish pivots (highs) for lower-highs
        # For SHORT: check bullish pivots (lows) for higher-lows
        check_bullish = (side == "SHORT")  # if SHORT, look at bullish pivots

        result = await db.execute(text("""
            SELECT price FROM signals
            WHERE symbol = :symbol AND timeframe = :timeframe
              AND is_preview = FALSE AND is_bullish = :is_bullish
            ORDER BY time DESC
            LIMIT 3
        """), {"symbol": symbol, "timeframe": timeframe, "is_bullish": check_bullish})
        rows = result.fetchall()

        if len(rows) < 3:
            return False  # Not enough data to decide

        # rows are ordered DESC (newest first): [newest, middle, oldest]
        prices = [r[0] for r in rows]
        p_newest, p_middle, p_oldest = prices[0], prices[1], prices[2]

        if side == "LONG":
            # Bearish pivots = swing highs
            # Lower highs → downtrend → skip LONG
            if p_newest < p_middle < p_oldest:
                logger.info(
                    f"[{agent_name}] SKIPPING LONG: 3 consecutive lower highs "
                    f"({p_oldest:.2f} > {p_middle:.2f} > {p_newest:.2f}) → downtrend"
                )
                return True
        else:  # SHORT
            # Bullish pivots = swing lows
            # Higher lows → uptrend → skip SHORT
            if p_newest > p_middle > p_oldest:
                logger.info(
                    f"[{agent_name}] SKIPPING SHORT: 3 consecutive higher lows "
                    f"({p_oldest:.2f} < {p_middle:.2f} < {p_newest:.2f}) → uptrend"
                )
                return True

        return False

    async def _is_htf_trend_against(self, db: AsyncSession, agent_name: str,
                                     symbol: str, timeframe: str,
                                     side: str) -> bool:
        """
        Check the higher-timeframe trend to CONFIRM the trade direction.

        Uses a RELAXED approach (2/3 pivots or EMA trend) instead of
        requiring 3/3 strictly monotone pivots. This avoids over-filtering
        in ranging/consolidating markets.

        Method:
        1. Check 3 most recent HTF pivots: if at least 2 out of 3
           consecutive pairs confirm the direction → allow.
        2. If not enough pivots (<3), fall back to HTF EMA trend.
        3. If EMA trend is neutral or confirms → allow.
        4. Only block if the HTF clearly opposes the trade.
        """
        htf_list = HTF_MAP.get(timeframe, [])
        if not htf_list:
            return False  # No HTF to check (e.g. 1d), allow trade

        for htf in htf_list:
            if side == "LONG":
                # For LONG: check HTF bullish pivots (lows) for higher lows
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

                    # Count how many pairs confirm higher lows (ascending)
                    confirms = 0
                    if p_newest > p_middle:
                        confirms += 1
                    if p_middle > p_oldest:
                        confirms += 1

                    if confirms >= 1:
                        # At least 1 out of 2 pairs confirms → allow
                        logger.info(
                            f"[{agent_name}] LONG OK: HTF {htf} pivots "
                            f"({p_oldest:.2f}, {p_middle:.2f}, {p_newest:.2f}) "
                            f"— {confirms}/2 pairs confirm higher lows ✓"
                        )
                    else:
                        # 0/2 pairs confirm → both pairs show lower lows → block
                        logger.info(
                            f"[{agent_name}] SKIPPING LONG: HTF {htf} showing lower lows "
                            f"({p_oldest:.2f}, {p_middle:.2f}, {p_newest:.2f}) "
                            f"— 0/2 pairs confirm → HTF downtrend"
                        )
                        return True

                elif len(rows) >= 2:
                    # Only 2 pivots: check if the pair confirms
                    p_newest, p_oldest = rows[0][0], rows[1][0]
                    if p_newest < p_oldest:
                        # Latest low is lower → downtrend → check EMA fallback
                        htf_ema_against = await self._is_ema_trend_against(
                            db, agent_name, symbol, htf, side
                        )
                        if htf_ema_against:
                            return True
                else:
                    # Not enough pivots: fall back to EMA trend on HTF
                    htf_ema_against = await self._is_ema_trend_against(
                        db, agent_name, symbol, htf, side
                    )
                    if htf_ema_against:
                        return True

            else:  # SHORT
                # For SHORT: check HTF bearish pivots (highs) for lower highs
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

                    # Count how many pairs confirm lower highs (descending)
                    confirms = 0
                    if p_newest < p_middle:
                        confirms += 1
                    if p_middle < p_oldest:
                        confirms += 1

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
                        htf_ema_against = await self._is_ema_trend_against(
                            db, agent_name, symbol, htf, side
                        )
                        if htf_ema_against:
                            return True
                else:
                    htf_ema_against = await self._is_ema_trend_against(
                        db, agent_name, symbol, htf, side
                    )
                    if htf_ema_against:
                        return True

        return False  # All HTFs confirmed or neutral → allow trade

    async def _get_available_capital(self, db: AsyncSession, agent: Agent) -> float:
        """Return agent's current balance."""
        return agent.balance

    async def _open_position(self, db: AsyncSession, agent: Agent,
                             side: str, current_price: float, signal_id: int,
                             amount: Optional[float] = None):
        """Open a new position using agent's full balance."""
        # ── Defensive guard: re-check DB state with row lock ──
        # Lock the agent row to prevent concurrent opens
        row = await db.execute(
            text("SELECT balance FROM agents WHERE id = :aid FOR UPDATE"),
            {"aid": agent.id}
        )
        db_balance = row.scalar()
        if db_balance is None or db_balance <= 0:
            logger.warning(f"[agent_{agent.id}] Balance is {db_balance} (race guard), skipping open")
            return

        # Also verify no open position already exists for this agent
        dup_check = await db.execute(text("""
            SELECT COUNT(*) FROM agent_positions
            WHERE agent_id = :aid AND status = 'OPEN'
        """), {"aid": agent.id})
        if dup_check.scalar() > 0:
            logger.warning(f"[agent_{agent.id}] Open position already exists (race guard), skipping")
            return

        settings = get_settings()
        trade_amount = db_balance  # Use the freshly-read balance

        # Sync in-memory agent object
        agent.balance = db_balance

        # Get previous pivot for SL calculation
        now = datetime.now(timezone.utc)
        is_bullish = (side == "LONG")
        pivot_price = await self._get_previous_pivot(
            db, agent.symbol, agent.timeframe, is_bullish, now
        )
        atr = await self._get_current_atr(db, agent.symbol, agent.timeframe)

        # P6: Get zone-based TP target from S/D zones
        zone_tp = await self._get_zone_tp(
            db, agent.symbol, agent.timeframe, side, current_price
        )

        sl, tp1, tp2 = self._calculate_sl_tp(
            side, current_price, pivot_price, atr, agent.timeframe, zone_tp=zone_tp
        )

        # Retrieve signal time for skip logging
        sig_time_row = await db.execute(
            text("SELECT time FROM signals WHERE id = :sid"), {"sid": signal_id}
        )
        _sig_time_val = sig_time_row.scalar()
        _signal_time_iso = _sig_time_val.isoformat() if _sig_time_val else None

        # ── Minimum risk filter ──
        # Skip trades where the SL is too close to entry (opposite reversals
        # too close in price → impossible to profit)
        if self._is_risk_too_small(f"agent_{agent.id}", side, current_price, sl, agent.timeframe):
            await self._log(db, agent.id, "TRADE_SKIPPED", {
                "side": side,
                "reason": "risk_too_small",
                "signal_time": _signal_time_iso,
                "entry_price": current_price,
                "stop_loss": sl,
                "risk_pct": round(abs(current_price - sl) / current_price * 100, 4),
            })
            return

        # ── Pivot momentum filter (same TF) ──
        # Lower highs → skip LONG (downtrend)
        # Higher lows → skip SHORT (uptrend)
        if await self._is_pivot_momentum_against(db, f"agent_{agent.id}",
                                                  agent.symbol, agent.timeframe, side):
            await self._log(db, agent.id, "TRADE_SKIPPED", {
                "side": side,
                "reason": "pivot_momentum_against",
                "signal_time": _signal_time_iso,
                "entry_price": current_price,
            })
            return

        # ── Higher-timeframe trend filter ──
        # Check pivots on HTFs (e.g. 1m→5m+15m, 5m→15m+1h)
        # Block the trade if the bigger-picture trend is opposite
        if await self._is_htf_trend_against(db, f"agent_{agent.id}",
                                             agent.symbol, agent.timeframe, side):
            await self._log(db, agent.id, "TRADE_SKIPPED", {
                "side": side,
                "reason": "htf_trend_against",
                "signal_time": _signal_time_iso,
                "entry_price": current_price,
                "htf_checked": HTF_MAP.get(agent.timeframe, []),
            })
            return

        # ── P7: EMA trend filter (same TF) ──
        # Block the trade if the EMA trend on the current timeframe opposes
        if await self._is_ema_trend_against(db, f"agent_{agent.id}",
                                             agent.symbol, agent.timeframe, side):
            await self._log(db, agent.id, "TRADE_SKIPPED", {
                "side": side,
                "reason": "ema_trend_against",
                "signal_time": _signal_time_iso,
                "entry_price": current_price,
            })
            return

        # Execute order
        order_result = await hyperliquid_client.market_open(
            symbol=agent.symbol,
            side=side,
            eur_amount=trade_amount,
            current_price=current_price,
            mode=agent.mode,
            wallet_address=settings.hyperliquid_wallet_address,
            api_secret=settings.hyperliquid_api_secret,
        )

        if not order_result.success:
            await self._log(db, agent.id, "ORDER_FAILED", {
                "side": side, "error": order_result.error,
            })
            return

        # Retrieve signal's stable key (time + direction) for tracking
        sig_row = await db.execute(
            text("SELECT time, is_bullish FROM signals WHERE id = :sid"),
            {"sid": signal_id}
        )
        sig_info = sig_row.fetchone()

        # Create position record
        qty = order_result.quantity or (trade_amount / current_price)
        position = AgentPosition(
            agent_id=agent.id,
            symbol=agent.symbol,
            side=side,
            entry_price=order_result.filled_price or current_price,
            stop_loss=sl,
            original_stop_loss=sl,
            take_profit=tp1,
            tp2=tp2,
            quantity=qty,
            original_quantity=qty,
            invested_eur=trade_amount,  # Store EUR amount invested for balance restoration
            best_price=order_result.filled_price or current_price,  # Initialize trailing stop tracker
            status="OPEN",
            partial_closed=False,
            entry_signal_id=signal_id,
            entry_signal_time=sig_info[0] if sig_info else None,
            entry_signal_is_bullish=sig_info[1] if sig_info else (side == "LONG"),
        )
        db.add(position)

        # Set balance to 0 — all capital is engaged in the position
        agent.balance = 0
        await db.commit()
        await db.refresh(position)

        risk = abs(current_price - sl)
        reward = abs(tp1 - current_price)
        reward2 = abs(tp2 - current_price)

        await self._log(db, agent.id, "POSITION_OPENED", {
            "position_id": position.id,
            "side": side,
            "entry_price": current_price,
            "stop_loss": sl,
            "take_profit_1": tp1,
            "take_profit_2": tp2,
            "zone_tp_used": zone_tp is not None,
            "quantity": position.quantity,
            "risk": round(risk, 2),
            "reward_tp1": round(reward, 2),
            "reward_tp2": round(reward2, 2),
            "rr_ratio_tp1": round(reward / risk, 2) if risk > 0 else 0,
            "rr_ratio_tp2": round(reward2 / risk, 2) if risk > 0 else 0,
            "mode": agent.mode,
            "is_paper": order_result.is_paper,
        })

        # Send Telegram notification
        await telegram_service.notify_position_opened(
            agent.name, agent.symbol, side, current_price,
            sl, tp1, position.quantity, agent.mode
        )

    async def _close_position_internal(self, db: AsyncSession, pos: AgentPosition,
                                       exit_price: Optional[float] = None,
                                       exit_signal_id: Optional[int] = None,
                                       reason: str = "SIGNAL") -> AgentPosition:
        """Close a position and calculate PnL."""
        if exit_price is None:
            exit_price = await self._get_current_price(db, pos.symbol, "1h")
            if exit_price is None:
                exit_price = pos.entry_price  # Last resort

        # Get agent for mode
        agent = await db.get(Agent, pos.agent_id)
        settings = get_settings()

        # Execute close order
        order_result = await hyperliquid_client.market_close(
            symbol=pos.symbol,
            side=pos.side,
            quantity=pos.quantity,
            current_price=exit_price,
            mode=agent.mode if agent else "paper",
            wallet_address=settings.hyperliquid_wallet_address,
            api_secret=settings.hyperliquid_api_secret,
        )

        actual_exit = order_result.filled_price if order_result.success else exit_price

        # Calculate PnL in USDT on the REMAINING quantity
        if pos.side == "LONG":
            pnl_usdt = (actual_exit - pos.entry_price) * pos.quantity
            pnl_pct = ((actual_exit - pos.entry_price) / pos.entry_price) * 100
        else:  # SHORT
            pnl_usdt = (pos.entry_price - actual_exit) * pos.quantity
            pnl_pct = ((pos.entry_price - actual_exit) / pos.entry_price) * 100

        # Convert PnL to EUR for storage
        pnl_eur = await hyperliquid_client.convert_usdt_to_eur(pnl_usdt)

        # Include partial PnL if a partial close has already occurred
        total_pnl_eur = pnl_eur + (pos.partial_pnl or 0.0)

        pos.exit_price = actual_exit
        pos.pnl = round(total_pnl_eur, 4)
        pos.pnl_percent = round(pnl_pct, 2)
        pos.status = "STOPPED" if reason == "STOP_LOSS" else "CLOSED"
        pos.exit_signal_id = exit_signal_id
        pos.closed_at = datetime.now(timezone.utc)

        # Restore balance to agent in EUR
        # Use invested_eur (stored at open time) + total_pnl_eur to avoid exchange rate drift
        invested_eur = pos.invested_eur or agent.trade_amount
        if agent:
            agent.balance = round(invested_eur + total_pnl_eur, 2)

        await db.commit()
        await db.refresh(pos)

        await self._log(db, pos.agent_id, f"POSITION_{pos.status}", {
            "position_id": pos.id,
            "side": pos.side,
            "entry_price": pos.entry_price,
            "exit_price": actual_exit,
            "pnl": pos.pnl,
            "pnl_percent": pos.pnl_percent,
            "reason": reason,
        })

        # Send Telegram notification
        await telegram_service.notify_position_closed(
            agent.name, pos.symbol, pos.side, pos.entry_price,
            actual_exit, pos.pnl, pos.pnl_percent, reason, agent.mode
        )

        return pos

    async def _check_stop_loss(self, db: AsyncSession, agent: Agent,
                               pos: AgentPosition, current_price: float,
                               candle_low: float = None, candle_high: float = None) -> bool:
        """Check if price has hit the stop loss (using candle high/low for wick detection)."""
        triggered = False

        low = candle_low if candle_low is not None else current_price
        high = candle_high if candle_high is not None else current_price

        if pos.side == "LONG" and low <= pos.stop_loss:
            triggered = True
            # Use actual candle low as exit (slippage-aware) — not the theoretical SL
            realistic_exit = min(low, pos.stop_loss)
        elif pos.side == "SHORT" and high >= pos.stop_loss:
            triggered = True
            realistic_exit = max(high, pos.stop_loss)
        else:
            realistic_exit = current_price

        if triggered:
            logger.info(
                f"[{agent.name}] STOP LOSS triggered for {pos.side} "
                f"@ {current_price:.2f} (SL: {pos.stop_loss:.2f}, "
                f"Low: {low:.2f}, High: {high:.2f}, exit: {realistic_exit:.2f})"
            )
            await self._close_position_internal(
                db, pos, exit_price=realistic_exit, reason="STOP_LOSS"
            )
            return True

        return False

    async def _check_take_profit(self, db: AsyncSession, agent: Agent,
                                pos: AgentPosition, current_price: float,
                                candle_low: float = None, candle_high: float = None) -> bool:
        """
        Check if price has hit take profit, with partial close support (P14).

        Two-stage take profit:
        1. TP1 (take_profit): close 50% of position, move SL to breakeven,
           set take_profit to TP2 for the remaining 50%.
        2. TP2: close the remaining 50%.

        Returns True if the position was fully closed, False otherwise.
        """
        if pos.take_profit is None:
            return False

        triggered = False

        low = candle_low if candle_low is not None else current_price
        high = candle_high if candle_high is not None else current_price

        if pos.side == "LONG" and high >= pos.take_profit:
            triggered = True
        elif pos.side == "SHORT" and low <= pos.take_profit:
            triggered = True

        if not triggered:
            return False

        # ── Stage 1: First partial close (50%) ──
        if not pos.partial_closed and pos.tp2:
            partial_qty = pos.quantity / 2.0

            logger.info(
                f"[{agent.name}] PARTIAL TP1 triggered for {pos.side} "
                f"@ {pos.take_profit:.2f} — closing 50% ({partial_qty:.6f}), "
                f"SL → breakeven, TP → TP2={pos.tp2:.2f}"
            )

            # Calculate PnL on the partial close
            if pos.side == "LONG":
                partial_pnl_usdt = (pos.take_profit - pos.entry_price) * partial_qty
            else:
                partial_pnl_usdt = (pos.entry_price - pos.take_profit) * partial_qty

            partial_pnl_eur = await hyperliquid_client.convert_usdt_to_eur(partial_pnl_usdt)

            # Execute partial close order
            settings = get_settings()
            await hyperliquid_client.market_close(
                symbol=pos.symbol,
                side=pos.side,
                quantity=partial_qty,
                current_price=pos.take_profit,
                mode=agent.mode,
                wallet_address=settings.hyperliquid_wallet_address,
                api_secret=settings.hyperliquid_api_secret,
            )

            # Update position: reduce quantity, move SL to breakeven, advance TP to TP2
            pos.quantity = pos.quantity - partial_qty
            pos.partial_closed = True
            pos.partial_pnl = round(partial_pnl_eur, 4)
            pos.stop_loss = pos.entry_price  # Move SL to breakeven
            pos.take_profit = pos.tp2         # Advance to TP2
            await db.commit()

            await self._log(db, agent.id, "PARTIAL_TP_CLOSED", {
                "position_id": pos.id,
                "side": pos.side,
                "tp1_price": pos.entry_price + (pos.take_profit - pos.entry_price),  # original TP1
                "partial_qty": round(partial_qty, 6),
                "remaining_qty": round(pos.quantity, 6),
                "partial_pnl_eur": round(partial_pnl_eur, 4),
                "new_sl": pos.entry_price,
                "new_tp": pos.tp2,
            })

            # Send Telegram notification for partial close
            await telegram_service.notify_position_closed(
                agent.name, pos.symbol, pos.side, pos.entry_price,
                pos.take_profit, partial_pnl_eur,
                round((pos.take_profit - pos.entry_price) / pos.entry_price * 100 if pos.side == "LONG"
                      else (pos.entry_price - pos.take_profit) / pos.entry_price * 100, 2),
                "PARTIAL_TP1", agent.mode
            )

            return False  # Position still open with remaining 50%

        # ── Stage 2: Full close at TP2 (or TP1 if no partial TP) ──
        logger.info(
            f"[{agent.name}] {'TP2' if pos.partial_closed else 'TAKE PROFIT'} "
            f"triggered for {pos.side} @ {current_price:.2f} "
            f"(TP: {pos.take_profit:.2f})"
        )
        await self._close_position_internal(
            db, pos, exit_price=pos.take_profit,
            reason="TAKE_PROFIT_2" if pos.partial_closed else "TAKE_PROFIT"
        )
        return True

    async def _update_unrealized_pnl(self, db: AsyncSession, pos: AgentPosition,
                                     current_price: float):
        """Update unrealized PnL on an open position (converted to EUR)."""
        if pos.side == "LONG":
            pnl_usdt = (current_price - pos.entry_price) * pos.quantity
            pnl_pct = ((current_price - pos.entry_price) / pos.entry_price) * 100
        else:  # SHORT
            pnl_usdt = (pos.entry_price - current_price) * pos.quantity
            pnl_pct = ((pos.entry_price - current_price) / pos.entry_price) * 100

        # Convert to EUR
        pnl_eur = await hyperliquid_client.convert_usdt_to_eur(pnl_usdt)

        pos.unrealized_pnl = round(pnl_eur, 4)
        pos.unrealized_pnl_percent = round(pnl_pct, 2)
        pos.current_price = current_price
        pos.pnl_updated_at = datetime.now(timezone.utc)
        await db.commit()

    async def _is_signal_processed(self, db: AsyncSession, agent_id: int,
                                   signal_id: int) -> bool:
        """Check if this signal was already used to open/close a position.
        
        Uses the stable signal key (time + direction) stored directly in
        agent_positions, instead of JOINing on volatile signal IDs that
        change after every DELETE+INSERT re-analysis cycle.
        """
        # Get the signal's natural key
        signal_result = await db.execute(text("""
            SELECT time, is_bullish FROM signals WHERE id = :signal_id
        """), {"signal_id": signal_id})
        signal_row = signal_result.fetchone()
        if not signal_row:
            return False

        sig_time, sig_bullish = signal_row

        # Check if any position for this agent was opened on the same
        # signal (same time + same direction)
        dup_result = await db.execute(text("""
            SELECT COUNT(*) FROM agent_positions
            WHERE agent_id = :agent_id
              AND entry_signal_time = :sig_time
              AND entry_signal_is_bullish = :sig_bullish
        """), {"agent_id": agent_id, "sig_time": sig_time, "sig_bullish": sig_bullish})
        return dup_result.scalar() > 0

    async def _log(self, db: AsyncSession, agent_id: int, action: str,
                   details: dict):
        """Write an agent activity log entry."""
        log = AgentLog(agent_id=agent_id, action=action, details=details)
        db.add(log)
        await db.commit()

    # ── Timeframe helpers ──────────────────────────────────────
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
        return 60  # default 1h

    # ── Statistics ───────────────────────────────────────────
    async def get_agent_stats(self, db: AsyncSession, agent_id: int) -> dict:
        """Get statistics for an agent."""
        agent = await db.get(Agent, agent_id)
        if not agent:
            return {"open_positions": 0, "total_pnl": 0, "total_unrealized_pnl": 0}
        
        # Open positions count
        open_result = await db.execute(text("""
            SELECT COUNT(*) FROM agent_positions
            WHERE agent_id = :id AND status = 'OPEN'
        """), {"id": agent_id})
        open_count = open_result.scalar()

        # Realized PnL = sum of pnl from closed/stopped positions
        realized_result = await db.execute(text("""
            SELECT COALESCE(SUM(pnl), 0) FROM agent_positions
            WHERE agent_id = :id AND status IN ('CLOSED', 'STOPPED')
        """), {"id": agent_id})
        total_pnl = realized_result.scalar()

        # Total unrealized PnL (open positions)
        unrealized_result = await db.execute(text("""
            SELECT COALESCE(SUM(unrealized_pnl), 0) FROM agent_positions
            WHERE agent_id = :id AND status = 'OPEN'
        """), {"id": agent_id})
        total_unrealized_pnl = unrealized_result.scalar()

        return {
            "open_positions": open_count,
            "total_pnl": round(float(total_pnl), 4),
            "total_unrealized_pnl": round(float(total_unrealized_pnl), 4),
        }

    async def get_total_realized_pnl(self, db: AsyncSession) -> float:
        """Get total realized PnL across all agents (sum of PnL from closed positions)."""
        result = await db.execute(text("""
            SELECT COALESCE(SUM(pnl), 0) FROM agent_positions
            WHERE status IN ('CLOSED', 'STOPPED')
        """))
        return round(float(result.scalar()), 4)

    async def get_all_agent_stats(self, db: AsyncSession) -> dict[int, dict]:
        """Get stats for ALL agents in a single query (avoids N+1).

        Returns ``{agent_id: {"open_positions": int, "total_pnl": float, "total_unrealized_pnl": float}}``.
        """
        result = await db.execute(text("""
            SELECT agent_id,
                   COUNT(*)    FILTER (WHERE status = 'OPEN')                         AS open_positions,
                   COALESCE(SUM(pnl), 0) FILTER (WHERE status IN ('CLOSED','STOPPED')) AS total_pnl,
                   COALESCE(SUM(unrealized_pnl), 0) FILTER (WHERE status = 'OPEN')     AS total_unrealized_pnl
            FROM agent_positions
            GROUP BY agent_id
        """))
        stats_map: dict[int, dict] = {}
        for row in result.fetchall():
            stats_map[row[0]] = {
                "open_positions": row[1],
                "total_pnl": round(float(row[2]), 4),
                "total_unrealized_pnl": round(float(row[3]), 4),
            }
        return stats_map


# Backward-compatible singleton — delegates to centralized dependencies
def __getattr__(name):
    if name == "agent_broker_service":
        from ..dependencies import get_agent_broker_service
        return get_agent_broker_service()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
