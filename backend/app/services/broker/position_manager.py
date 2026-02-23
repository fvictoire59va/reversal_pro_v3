"""
Position Manager Mixin — open, close (full & partial), manual close,
unrealised PnL tracking.
"""

import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ...models import Agent, AgentPosition
from ...config import get_settings
from ..hyperliquid_client import hyperliquid_client
from ..telegram_service import telegram_service
from .constants import HTF_MAP

logger = logging.getLogger(__name__)


class PositionManagerMixin:
    """Open / close / partial-TP / unrealised PnL management."""

    async def close_position_manually(
        self, db: AsyncSession, position_id: int,
    ) -> Optional[AgentPosition]:
        """Manually close a position from the web interface."""
        pos = await db.get(AgentPosition, position_id)
        if not pos or pos.status != "OPEN":
            return None
        return await self._close_position_internal(db, pos, reason="MANUAL_CLOSE")

    async def _get_available_capital(self, db: AsyncSession, agent: Agent) -> float:
        """Return agent's current balance."""
        return agent.balance

    # ── Open position ────────────────────────────────────────

    async def _open_position(
        self, db: AsyncSession, agent: Agent,
        side: str, current_price: float, signal_id: int,
        amount: Optional[float] = None,
    ):
        """Open a new position using agent's full balance."""
        # Defensive guard: re-check DB state with row lock
        row = await db.execute(
            text("SELECT balance FROM agents WHERE id = :aid FOR UPDATE"),
            {"aid": agent.id},
        )
        db_balance = row.scalar()
        if db_balance is None or db_balance <= 0:
            logger.warning(f"[agent_{agent.id}] Balance is {db_balance} (race guard), skipping open")
            return

        dup_check = await db.execute(text("""
            SELECT COUNT(*) FROM agent_positions
            WHERE agent_id = :aid AND status = 'OPEN'
        """), {"aid": agent.id})
        if dup_check.scalar() > 0:
            logger.warning(f"[agent_{agent.id}] Open position already exists (race guard), skipping")
            return

        settings = get_settings()
        trade_amount = db_balance
        agent.balance = db_balance

        now = datetime.now(timezone.utc)
        is_bullish = (side == "LONG")
        pivot_price = await self._get_previous_pivot(
            db, agent.symbol, agent.timeframe, is_bullish, now
        )
        atr = await self._get_current_atr(db, agent.symbol, agent.timeframe)
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
        if self._is_risk_too_small(f"agent_{agent.id}", side, current_price, sl, agent.timeframe):
            await self._log(db, agent.id, "TRADE_SKIPPED", {
                "side": side, "reason": "risk_too_small",
                "signal_time": _signal_time_iso,
                "entry_price": current_price, "stop_loss": sl,
                "risk_pct": round(abs(current_price - sl) / current_price * 100, 4),
            })
            return

        # ── Pivot momentum filter (same TF) ──
        if await self._is_pivot_momentum_against(
            db, f"agent_{agent.id}", agent.symbol, agent.timeframe, side
        ):
            await self._log(db, agent.id, "TRADE_SKIPPED", {
                "side": side, "reason": "pivot_momentum_against",
                "signal_time": _signal_time_iso, "entry_price": current_price,
            })
            return

        # ── Higher-timeframe trend filter ──
        if await self._is_htf_trend_against(
            db, f"agent_{agent.id}", agent.symbol, agent.timeframe, side
        ):
            await self._log(db, agent.id, "TRADE_SKIPPED", {
                "side": side, "reason": "htf_trend_against",
                "signal_time": _signal_time_iso, "entry_price": current_price,
                "htf_checked": HTF_MAP.get(agent.timeframe, []),
            })
            return

        # ── EMA trend filter (same TF) ──
        if await self._is_ema_trend_against(
            db, f"agent_{agent.id}", agent.symbol, agent.timeframe, side
        ):
            await self._log(db, agent.id, "TRADE_SKIPPED", {
                "side": side, "reason": "ema_trend_against",
                "signal_time": _signal_time_iso, "entry_price": current_price,
            })
            return

        # Execute order
        order_result = await hyperliquid_client.market_open(
            symbol=agent.symbol, side=side, eur_amount=trade_amount,
            current_price=current_price, mode=agent.mode,
            wallet_address=settings.hyperliquid_wallet_address,
            api_secret=settings.hyperliquid_api_secret,
        )

        if not order_result.success:
            await self._log(db, agent.id, "ORDER_FAILED", {
                "side": side, "error": order_result.error,
            })
            return

        sig_row = await db.execute(
            text("SELECT time, is_bullish FROM signals WHERE id = :sid"),
            {"sid": signal_id},
        )
        sig_info = sig_row.fetchone()

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
            invested_eur=trade_amount,
            best_price=order_result.filled_price or current_price,
            status="OPEN",
            partial_closed=False,
            entry_signal_id=signal_id,
            entry_signal_time=sig_info[0] if sig_info else None,
            entry_signal_is_bullish=sig_info[1] if sig_info else (side == "LONG"),
        )
        db.add(position)

        agent.balance = 0
        await db.commit()
        await db.refresh(position)

        risk = abs(current_price - sl)
        reward = abs(tp1 - current_price)
        reward2 = abs(tp2 - current_price)

        await self._log(db, agent.id, "POSITION_OPENED", {
            "position_id": position.id, "side": side,
            "entry_price": current_price, "stop_loss": sl,
            "take_profit_1": tp1, "take_profit_2": tp2,
            "zone_tp_used": zone_tp is not None,
            "quantity": position.quantity,
            "risk": round(risk, 2),
            "reward_tp1": round(reward, 2),
            "reward_tp2": round(reward2, 2),
            "rr_ratio_tp1": round(reward / risk, 2) if risk > 0 else 0,
            "rr_ratio_tp2": round(reward2 / risk, 2) if risk > 0 else 0,
            "mode": agent.mode, "is_paper": order_result.is_paper,
        })

        await telegram_service.notify_position_opened(
            agent.name, agent.symbol, side, current_price,
            sl, tp1, position.quantity, agent.mode
        )

    # ── Close position ───────────────────────────────────────

    async def _close_position_internal(
        self, db: AsyncSession, pos: AgentPosition,
        exit_price: Optional[float] = None,
        exit_signal_id: Optional[int] = None,
        reason: str = "SIGNAL",
    ) -> AgentPosition:
        """Close a position and calculate PnL."""
        if exit_price is None:
            exit_price = await self._get_current_price(db, pos.symbol, "1h")
            if exit_price is None:
                exit_price = pos.entry_price

        agent = await db.get(Agent, pos.agent_id)
        settings = get_settings()

        order_result = await hyperliquid_client.market_close(
            symbol=pos.symbol, side=pos.side, quantity=pos.quantity,
            current_price=exit_price,
            mode=agent.mode if agent else "paper",
            wallet_address=settings.hyperliquid_wallet_address,
            api_secret=settings.hyperliquid_api_secret,
        )

        if not order_result.success:
            mode = agent.mode if agent else "paper"
            if mode == "live":
                logger.error(
                    f"[agent_{pos.agent_id}] CLOSE ORDER FAILED in live mode: "
                    f"{order_result.error} — position {pos.id} stays OPEN"
                )
                await self._log(db, pos.agent_id, "ORDER_FAILED", {
                    "action": "close", "position_id": pos.id,
                    "side": pos.side, "error": order_result.error,
                })
                return pos  # Do NOT mark closed if real exchange order failed
            # Paper mode: proceed with estimated price
            logger.warning(
                f"[agent_{pos.agent_id}] Close order failed in paper mode, "
                f"using estimated exit price {exit_price}"
            )

        actual_exit = order_result.filled_price if order_result.success else exit_price

        if pos.side == "LONG":
            pnl_usdt = (actual_exit - pos.entry_price) * pos.quantity
            pnl_pct = ((actual_exit - pos.entry_price) / pos.entry_price) * 100
        else:
            pnl_usdt = (pos.entry_price - actual_exit) * pos.quantity
            pnl_pct = ((pos.entry_price - actual_exit) / pos.entry_price) * 100

        pnl_eur = await hyperliquid_client.convert_usdt_to_eur(pnl_usdt)
        total_pnl_eur = pnl_eur + (pos.partial_pnl or 0.0)

        pos.exit_price = actual_exit
        pos.pnl = round(total_pnl_eur, 4)
        pos.pnl_percent = round(pnl_pct, 2)
        pos.status = "STOPPED" if reason in ("STOP_LOSS", "TRAILING_STOP") else "CLOSED"
        pos.exit_signal_id = exit_signal_id
        pos.closed_at = datetime.now(timezone.utc)

        invested_eur = pos.invested_eur or agent.trade_amount
        if agent:
            agent.balance = round(invested_eur + total_pnl_eur, 2)

        await db.commit()
        await db.refresh(pos)

        await self._log(db, pos.agent_id, f"POSITION_{pos.status}", {
            "position_id": pos.id, "side": pos.side,
            "entry_price": pos.entry_price, "exit_price": actual_exit,
            "pnl": pos.pnl, "pnl_percent": pos.pnl_percent,
            "reason": reason,
        })

        await telegram_service.notify_position_closed(
            agent.name, pos.symbol, pos.side, pos.entry_price,
            actual_exit, pos.pnl, pos.pnl_percent, reason, agent.mode
        )

        return pos

    # ── Take-profit check (with partial close) ───────────────

    async def _check_take_profit(
        self, db: AsyncSession, agent: Agent, pos: AgentPosition,
        current_price: float, candle_low: float = None, candle_high: float = None,
    ) -> bool:
        """Two-stage TP: partial close at TP1 (50 %), full close at TP2."""
        if pos.take_profit is None:
            return False

        low = candle_low if candle_low is not None else current_price
        high = candle_high if candle_high is not None else current_price

        triggered = False
        if pos.side == "LONG" and high >= pos.take_profit:
            triggered = True
        elif pos.side == "SHORT" and low <= pos.take_profit:
            triggered = True

        if not triggered:
            return False

        # ── Stage 1: partial close (50 %) ──
        if not pos.partial_closed and pos.tp2:
            partial_qty = pos.quantity / 2.0

            logger.info(
                f"[{agent.name}] PARTIAL TP1 triggered for {pos.side} "
                f"@ {pos.take_profit:.2f} — closing 50% ({partial_qty:.6f}), "
                f"SL → breakeven, TP → TP2={pos.tp2:.2f}"
            )

            if pos.side == "LONG":
                partial_pnl_usdt = (pos.take_profit - pos.entry_price) * partial_qty
            else:
                partial_pnl_usdt = (pos.entry_price - pos.take_profit) * partial_qty

            partial_pnl_eur = await hyperliquid_client.convert_usdt_to_eur(partial_pnl_usdt)

            settings = get_settings()
            partial_order = await hyperliquid_client.market_close(
                symbol=pos.symbol, side=pos.side, quantity=partial_qty,
                current_price=pos.take_profit, mode=agent.mode,
                wallet_address=settings.hyperliquid_wallet_address,
                api_secret=settings.hyperliquid_api_secret,
            )

            if not partial_order.success and agent.mode == "live":
                logger.error(
                    f"[{agent.name}] PARTIAL TP close FAILED in live mode: "
                    f"{partial_order.error} — skipping partial TP"
                )
                await self._log(db, agent.id, "ORDER_FAILED", {
                    "action": "partial_tp", "position_id": pos.id,
                    "side": pos.side, "error": partial_order.error,
                })
                return False

            pos.quantity = pos.quantity - partial_qty
            pos.partial_closed = True
            pos.partial_pnl = round(partial_pnl_eur, 4)
            pos.stop_loss = pos.entry_price
            pos.take_profit = pos.tp2
            await db.commit()

            await self._log(db, agent.id, "PARTIAL_TP_CLOSED", {
                "position_id": pos.id, "side": pos.side,
                "tp1_price": pos.entry_price + (pos.take_profit - pos.entry_price),
                "partial_qty": round(partial_qty, 6),
                "remaining_qty": round(pos.quantity, 6),
                "partial_pnl_eur": round(partial_pnl_eur, 4),
                "new_sl": pos.entry_price, "new_tp": pos.tp2,
            })

            await telegram_service.notify_position_closed(
                agent.name, pos.symbol, pos.side, pos.entry_price,
                pos.take_profit, partial_pnl_eur,
                round(
                    (pos.take_profit - pos.entry_price) / pos.entry_price * 100
                    if pos.side == "LONG"
                    else (pos.entry_price - pos.take_profit) / pos.entry_price * 100,
                    2,
                ),
                "PARTIAL_TP1", agent.mode,
            )

            return False  # Position still open with remaining 50 %

        # ── Stage 2: full close at TP2 (or TP1 if no partial TP) ──
        logger.info(
            f"[{agent.name}] {'TP2' if pos.partial_closed else 'TAKE PROFIT'} "
            f"triggered for {pos.side} @ {current_price:.2f} "
            f"(TP: {pos.take_profit:.2f})"
        )
        await self._close_position_internal(
            db, pos, exit_price=pos.take_profit,
            reason="TAKE_PROFIT_2" if pos.partial_closed else "TAKE_PROFIT",
        )
        return True

    # ── Unrealised PnL update ────────────────────────────────

    async def _update_unrealized_pnl(
        self, db: AsyncSession, pos: AgentPosition, current_price: float,
    ):
        """Update unrealized PnL on an open position (converted to EUR)."""
        if pos.side == "LONG":
            pnl_usdt = (current_price - pos.entry_price) * pos.quantity
            pnl_pct = ((current_price - pos.entry_price) / pos.entry_price) * 100
        else:
            pnl_usdt = (pos.entry_price - current_price) * pos.quantity
            pnl_pct = ((pos.entry_price - current_price) / pos.entry_price) * 100

        pnl_eur = await hyperliquid_client.convert_usdt_to_eur(pnl_usdt)

        pos.unrealized_pnl = round(pnl_eur, 4)
        pos.unrealized_pnl_percent = round(pnl_pct, 2)
        pos.current_price = current_price
        pos.pnl_updated_at = datetime.now(timezone.utc)
        await db.commit()
