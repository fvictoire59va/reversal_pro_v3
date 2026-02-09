"""
Agent Broker Service — autonomous trading agents that monitor signals
and manage positions on Hyperliquid.

Each agent:
  1. Polls signals from DB at its configured timeframe interval
  2. Opens LONG on bullish reversal, closes on bearish reversal
  3. Opens SHORT on bearish reversal, closes on bullish reversal
  4. Calculates SL from previous pivot, TP with R:R = 3:1
  5. Works in paper (simulation) or live mode
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional, List, Dict

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


class AgentBrokerService:
    """Manages all trading agents and their autonomous execution."""

    def __init__(self):
        self._running_agents: Dict[int, bool] = {}

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
        
        Uses PostgreSQL advisory lock to prevent concurrent execution
        across multiple uvicorn workers.
        """
        # Use PostgreSQL advisory lock (works across workers/processes)
        # pg_try_advisory_xact_lock returns true if lock acquired, false if already held
        lock_id = 100000 + agent.id  # unique lock ID per agent
        lock_result = await db.execute(
            text("SELECT pg_try_advisory_xact_lock(:lock_id)"),
            {"lock_id": lock_id}
        )
        acquired = lock_result.scalar()
        
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

            # 2. Get latest confirmed signal
            latest_signal = await self._get_latest_signal(db, agent.symbol, agent.timeframe)
            if not latest_signal:
                logger.info(f"[{agent.name}] No signals found, skipping")
                return

            # 3. Check if this signal was already processed
            open_positions = await self._get_open_positions(db, agent.id)

            # Get current price
            current_price = await self._get_current_price(db, agent.symbol, agent.timeframe)
            if not current_price:
                logger.warning(f"[{agent.name}] Cannot determine current price")
                return

            # 4. Check stop losses on open positions
            for pos in open_positions:
                if await self._check_stop_loss(db, agent, pos, current_price):
                    continue  # Position was stopped out
                # Update unrealized PnL for surviving open positions
                await self._update_unrealized_pnl(db, pos, current_price)

            # Refresh open positions after SL checks
            open_positions = await self._get_open_positions(db, agent.id)

            # 5. Signal-based logic
            signal_time = latest_signal[0]
            is_bullish = latest_signal[1]
            signal_price = latest_signal[2]
            signal_id = latest_signal[3]
            signal_bar_index = latest_signal[4]

            # 5a. Check signal freshness using bar_index position in the
            # analysis window. Confirmed signals have their time set to the
            # PIVOT bar (the reversal extreme), which is naturally many bars
            # before the confirmation bar. Instead of time-based staleness,
            # we check how far the signal is from the end of the analysis.
            analysis_run_result = await db.execute(text("""
                SELECT bars_analyzed FROM analysis_runs
                WHERE symbol = :symbol AND timeframe = :timeframe
                ORDER BY created_at DESC LIMIT 1
            """), {"symbol": agent.symbol, "timeframe": agent.timeframe})
            analysis_run_row = analysis_run_result.fetchone()

            if analysis_run_row and signal_bar_index is not None:
                bars_analyzed = analysis_run_row[0]
                bars_from_end = bars_analyzed - signal_bar_index
                # Adaptive threshold based on timeframe.
                # Confirmed signals appear at the PIVOT bar, typically 3-10
                # bars from the end when first detected. The threshold defines
                # how many minutes/candles we tolerate before considering
                # the signal stale.  Typical signal-to-signal gap on 1m ≈12 bars.
                tf_minutes = self._timeframe_to_minutes(agent.timeframe)
                if tf_minutes <= 1:
                    max_bars_back = 15   # 15 min window for 1m
                elif tf_minutes <= 5:
                    max_bars_back = 20   # ~1h40 for 5m
                elif tf_minutes <= 15:
                    max_bars_back = 15   # ~3h45 for 15m
                else:
                    max_bars_back = 10   # ~10h for 1h
                if bars_from_end > max_bars_back:
                    logger.info(
                        f"[{agent.name}] Signal {signal_id} is stale "
                        f"(bar {signal_bar_index}/{bars_analyzed}, {bars_from_end} bars from end, max {max_bars_back}), skipping"
                    )
                    return
                logger.debug(
                    f"[{agent.name}] Signal {signal_id} freshness OK "
                    f"(bar {signal_bar_index}/{bars_analyzed}, {bars_from_end} bars from end)"
                )

            # Check if already processed this signal
            already_processed = await self._is_signal_processed(db, agent.id, signal_id)
            if already_processed:
                logger.debug(f"[{agent.name}] Signal {signal_id} already processed")
                return

            # Agent uses ALL capital in one position at a time.
            # If a position is already open, only close it on inverse signal.
            has_position = len(open_positions) > 0

            if is_bullish:
                # Bullish reversal: close SHORT if any, then open LONG
                if has_position:
                    short_pos = next((p for p in open_positions if p.side == "SHORT"), None)
                    if short_pos:
                        await self._close_position_internal(
                            db, short_pos, exit_price=current_price,
                            exit_signal_id=signal_id, reason="BULLISH_REVERSAL"
                        )
                        logger.info(f"[{agent.name}] Closed SHORT on bullish reversal")
                    else:
                        # Already have a LONG open — do nothing
                        logger.debug(f"[{agent.name}] Already in LONG position, skipping")
                        return

                # Open LONG with available balance
                if agent.balance <= 0:
                    logger.info(f"[{agent.name}] Balance is {agent.balance:.2f}, cannot open position")
                    return
                await self._open_position(
                    db, agent, "LONG", current_price, signal_id
                )
                logger.info(f"[{agent.name}] Opened LONG with {agent.balance:.2f}€ on bullish reversal")
            else:
                # Bearish reversal: close LONG if any, then open SHORT
                if has_position:
                    long_pos = next((p for p in open_positions if p.side == "LONG"), None)
                    if long_pos:
                        await self._close_position_internal(
                            db, long_pos, exit_price=current_price,
                            exit_signal_id=signal_id, reason="BEARISH_REVERSAL"
                        )
                        logger.info(f"[{agent.name}] Closed LONG on bearish reversal")
                    else:
                        # Already have a SHORT open — do nothing
                        logger.debug(f"[{agent.name}] Already in SHORT position, skipping")
                        return

                # Open SHORT with available balance
                if agent.balance <= 0:
                    logger.info(f"[{agent.name}] Balance is {agent.balance:.2f}, cannot open position")
                    return
                await self._open_position(
                    db, agent, "SHORT", current_price, signal_id
                )
                logger.info(f"[{agent.name}] Opened SHORT with {agent.balance:.2f}€ on bearish reversal")

        except Exception as e:
            logger.error(f"[{agent.name}] Cycle error: {e}", exc_info=True)
            await self._log(db, agent.id, "CYCLE_ERROR", {"error": str(e)})

    async def run_all_active_agents(self, db: AsyncSession):
        """Run one cycle for all active agents. Called by the scheduler."""
        agents = await self.get_all_agents(db)
        active = [a for a in agents if a.is_active]

        if not active:
            return

        logger.info(f"Running {len(active)} active agent(s)...")

        for agent in active:
            try:
                await self.run_agent_cycle(db, agent)
            except Exception as e:
                logger.error(f"Agent {agent.name} failed: {e}")

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

    def _calculate_sl_tp(self, side: str, entry_price: float,
                         pivot_price: Optional[float], atr: Optional[float]) -> tuple:
        """
        Calculate Stop Loss and Take Profit for R:R = 3:1.
        
        LONG:  SL = previous bearish pivot (or entry - 1.5*ATR fallback)
               TP = entry + 3 * (entry - SL)
        SHORT: SL = previous bullish pivot (or entry + 1.5*ATR fallback)
               TP = entry - 3 * (SL - entry)
        """
        if side == "LONG":
            if pivot_price and pivot_price < entry_price:
                sl = pivot_price
            elif atr:
                sl = entry_price - (1.5 * atr)
            else:
                sl = entry_price * 0.98  # 2% fallback

            risk = entry_price - sl
            tp = entry_price + (3.0 * risk)
        else:  # SHORT
            if pivot_price and pivot_price > entry_price:
                sl = pivot_price
            elif atr:
                sl = entry_price + (1.5 * atr)
            else:
                sl = entry_price * 1.02  # 2% fallback

            risk = sl - entry_price
            tp = entry_price - (3.0 * risk)

        return round(sl, 2), round(tp, 2)

    async def _get_available_capital(self, db: AsyncSession, agent: Agent) -> float:
        """Return agent's current balance."""
        return agent.balance

    async def _open_position(self, db: AsyncSession, agent: Agent,
                             side: str, current_price: float, signal_id: int,
                             amount: Optional[float] = None):
        """Open a new position using agent's full balance."""
        settings = get_settings()
        trade_amount = agent.balance

        # Get previous pivot for SL calculation
        now = datetime.now(timezone.utc)
        is_bullish = (side == "LONG")
        pivot_price = await self._get_previous_pivot(
            db, agent.symbol, agent.timeframe, is_bullish, now
        )
        atr = await self._get_current_atr(db, agent.symbol, agent.timeframe)

        sl, tp = self._calculate_sl_tp(side, current_price, pivot_price, atr)

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

        # Create position record
        position = AgentPosition(
            agent_id=agent.id,
            symbol=agent.symbol,
            side=side,
            entry_price=order_result.filled_price or current_price,
            stop_loss=sl,
            take_profit=tp,
            quantity=order_result.quantity or (trade_amount / current_price),
            invested_eur=trade_amount,  # Store EUR amount invested for balance restoration
            status="OPEN",
            entry_signal_id=signal_id,
        )
        db.add(position)

        # Set balance to 0 — all capital is engaged in the position
        agent.balance = 0
        await db.commit()
        await db.refresh(position)

        risk = abs(current_price - sl)
        reward = abs(tp - current_price)

        await self._log(db, agent.id, "POSITION_OPENED", {
            "position_id": position.id,
            "side": side,
            "entry_price": current_price,
            "stop_loss": sl,
            "take_profit": tp,
            "quantity": position.quantity,
            "risk": round(risk, 2),
            "reward": round(reward, 2),
            "rr_ratio": round(reward / risk, 2) if risk > 0 else 0,
            "mode": agent.mode,
            "is_paper": order_result.is_paper,
        })

        # Send Telegram notification
        await telegram_service.notify_position_opened(
            agent.name, agent.symbol, side, current_price,
            sl, tp, position.quantity, agent.mode
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

        # Calculate PnL in USDT (prices are USDT on Hyperliquid)
        if pos.side == "LONG":
            pnl_usdt = (actual_exit - pos.entry_price) * pos.quantity
            pnl_pct = ((actual_exit - pos.entry_price) / pos.entry_price) * 100
        else:  # SHORT
            pnl_usdt = (pos.entry_price - actual_exit) * pos.quantity
            pnl_pct = ((pos.entry_price - actual_exit) / pos.entry_price) * 100

        # Convert PnL to EUR for storage
        pnl_eur = await hyperliquid_client.convert_usdt_to_eur(pnl_usdt)

        pos.exit_price = actual_exit
        pos.pnl = round(pnl_eur, 4)
        pos.pnl_percent = round(pnl_pct, 2)
        pos.status = "STOPPED" if reason == "STOP_LOSS" else "CLOSED"
        pos.exit_signal_id = exit_signal_id
        pos.closed_at = datetime.now(timezone.utc)

        # Restore balance to agent in EUR
        # Use invested_eur (stored at open time) + pnl_eur to avoid exchange rate drift
        invested_eur = pos.invested_eur or agent.trade_amount
        if agent:
            agent.balance = round(invested_eur + pnl_eur, 2)

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
                               pos: AgentPosition, current_price: float) -> bool:
        """Check if price has hit the stop loss."""
        triggered = False

        if pos.side == "LONG" and current_price <= pos.stop_loss:
            triggered = True
        elif pos.side == "SHORT" and current_price >= pos.stop_loss:
            triggered = True

        if triggered:
            logger.info(
                f"[{agent.name}] STOP LOSS triggered for {pos.side} "
                f"@ {current_price:.2f} (SL: {pos.stop_loss:.2f})"
            )
            await self._close_position_internal(
                db, pos, exit_price=pos.stop_loss, reason="STOP_LOSS"
            )
            return True

        return False

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
        
        Two checks:
        1. Direct signal_id match (same analysis run)
        2. Signal time+direction match (guards against signal ID changes
           after DELETE+INSERT re-analysis cycles)
        """
        # Check 1: exact signal_id match
        result = await db.execute(text("""
            SELECT COUNT(*) FROM agent_positions
            WHERE agent_id = :agent_id
              AND (entry_signal_id = :signal_id OR exit_signal_id = :signal_id)
        """), {"agent_id": agent_id, "signal_id": signal_id})
        if result.scalar() > 0:
            return True

        # Check 2: same signal time + direction already used
        # (prevents duplicates when signal IDs change after re-analysis)
        signal_result = await db.execute(text("""
            SELECT time, is_bullish FROM signals WHERE id = :signal_id
        """), {"signal_id": signal_id})
        signal_row = signal_result.fetchone()
        if not signal_row:
            return False

        sig_time, sig_bullish = signal_row
        side = "LONG" if sig_bullish else "SHORT"

        dup_result = await db.execute(text("""
            SELECT COUNT(*) FROM agent_positions ap
            JOIN signals s ON s.id = ap.entry_signal_id
            WHERE ap.agent_id = :agent_id
              AND s.time = :sig_time
              AND ap.side = :side
        """), {"agent_id": agent_id, "sig_time": sig_time, "side": side})
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


# Singleton
agent_broker_service = AgentBrokerService()
