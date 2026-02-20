"""
Telegram Bot Service — sends notifications about agents and positions.

Features:
  - Status updates on agent positions (open/close)
  - Agent summary with PnL statistics
  - Commands: /status, /agents, /positions, /help
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional, List, Dict
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text

import httpx

from ..models import Agent, AgentPosition
from ..config import get_settings

logger = logging.getLogger(__name__)


class TelegramService:
    """Handles Telegram Bot notifications and commands."""

    def __init__(self):
        self._client: Optional[httpx.AsyncClient] = None
        self._enabled = False
        self._bot_token = ""
        self._chat_id = ""
        self._base_url = ""
        self._initialize()

    def _initialize(self):
        """Initialize Telegram settings from config."""
        settings = get_settings()
        self._enabled = settings.telegram_enabled
        self._bot_token = settings.telegram_bot_token
        self._chat_id = settings.telegram_chat_id

        if self._enabled and self._bot_token:
            self._base_url = f"https://api.telegram.org/bot{self._bot_token}"
            logger.info(f"Telegram service initialized (chat_id: {self._chat_id})")
        elif self._enabled:
            logger.warning("Telegram enabled but bot token not configured")
            self._enabled = False
        else:
            logger.info("Telegram service disabled")

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=10.0)
        return self._client

    async def close(self):
        """Close HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def send_message(self, text: str, parse_mode: str = "Markdown") -> bool:
        """
        Send a message to the configured chat.
        
        Args:
            text: Message text (supports Markdown)
            parse_mode: "Markdown" or "HTML"
        
        Returns:
            True if sent successfully
        """
        if not self._enabled:
            logger.debug("Telegram service disabled, message not sent")
            return False

        try:
            client = await self._get_client()
            logger.debug(f"Sending Telegram message to {self._chat_id}")
            response = await client.post(
                f"{self._base_url}/sendMessage",
                json={
                    "chat_id": self._chat_id,
                    "text": text,
                    "parse_mode": parse_mode,
                    "disable_web_page_preview": True,
                }
            )

            if response.status_code == 200:
                logger.debug("Telegram message sent successfully")
                return True
            else:
                logger.error(f"Telegram API error: {response.status_code} - {response.text}")
                return False

        except Exception as e:
            logger.error(f"Failed to send Telegram message: {e}", exc_info=True)
            return False

    # ── Notification methods ─────────────────────────────────
    async def notify_position_opened(self, agent_name: str, symbol: str,
                                    side: str, entry_price: float,
                                    stop_loss: float, take_profit: float,
                                    quantity: float, mode: str):
        """Notify when a position is opened."""
        risk = abs(entry_price - stop_loss)
        reward = abs(take_profit - entry_price)
        rr_ratio = reward / risk if risk > 0 else 0

        emoji = "🟢" if side == "LONG" else "🔴"
        mode_emoji = "📝" if mode == "paper" else "💰"

        text = (
            f"{emoji} *Position Opened* {mode_emoji}\n\n"
            f"*Agent:* `{agent_name}`\n"
            f"*Symbol:* `{symbol}`\n"
            f"*Side:* `{side}`\n"
            f"*Entry:* `{entry_price:.2f}`\n"
            f"*Stop Loss:* `{stop_loss:.2f}`\n"
            f"*Take Profit:* `{take_profit:.2f}`\n"
            f"*Quantity:* `{quantity:.6f}`\n"
            f"*R:R Ratio:* `{rr_ratio:.2f}`\n"
            f"*Mode:* `{mode.upper()}`"
        )

        await self.send_message(text)

    async def notify_position_closed(self, agent_name: str, symbol: str,
                                    side: str, entry_price: float,
                                    exit_price: float, pnl: float,
                                    pnl_percent: float, reason: str, mode: str):
        """Notify when a position is closed."""
        emoji = "✅" if pnl > 0 else "❌"
        mode_emoji = "📝" if mode == "paper" else "💰"

        reason_labels = {
            "STOP_LOSS": "Stop Loss",
            "TRAILING_STOP": "Trailing Stop",
            "TAKE_PROFIT": "Take Profit",
            "TAKE_PROFIT_2": "Take Profit 2",
            "PARTIAL_TP1": "TP1 Partiel (50%)",
            "BULLISH_REVERSAL": "Reversal Bullish",
            "BEARISH_REVERSAL": "Reversal Bearish",
            "MANUAL_CLOSE": "Fermeture manuelle",
        }
        reason_display = reason_labels.get(reason, reason)

        text = (
            f"{emoji} *Position Closed* {mode_emoji}\n\n"
            f"*Agent:* `{agent_name}`\n"
            f"*Symbol:* `{symbol}`\n"
            f"*Side:* `{side}`\n"
            f"*Entry:* `{entry_price:.2f}`\n"
            f"*Exit:* `{exit_price:.2f}`\n"
            f"*PnL:* `{pnl:+.2f} ({pnl_percent:+.2f}%)`\n"
            f"*Reason:* `{reason_display}`\n"
            f"*Mode:* `{mode.upper()}`"
        )

        await self.send_message(text)

    async def notify_agent_activated(self, agent_name: str, symbol: str,
                                     timeframe: str, mode: str):
        """Notify when an agent is activated."""
        text = (
            f"🚀 *Agent Activated*\n\n"
            f"*Name:* `{agent_name}`\n"
            f"*Symbol:* `{symbol}`\n"
            f"*Timeframe:* `{timeframe}`\n"
            f"*Mode:* `{mode.upper()}`"
        )
        await self.send_message(text)

    async def notify_agent_deactivated(self, agent_name: str):
        """Notify when an agent is deactivated."""
        text = f"⏸️ *Agent Deactivated*\n\n*Name:* `{agent_name}`"
        await self.send_message(text)

    async def notify_new_signal(self, sig: dict):
        """
        Notify when a new reversal signal is detected (one per timeframe).
        
        Args:
            sig: dict with keys:
                symbol, timeframe, is_bullish, price, actual_price, signal_time
        """
        direction = "BULLISH ▲" if sig["is_bullish"] else "BEARISH ▼"
        emoji = "🟢" if sig["is_bullish"] else "🔴"
        sig_time = sig["signal_time"]
        time_str = sig_time.strftime("%d/%m/%Y %H:%M")

        text = (
            f"{emoji} *Signal Detected*\n\n"
            f"*Direction:* `{direction}`\n"
            f"*Symbol:* `{sig['symbol']}`\n"
            f"*Timeframe:* `{sig['timeframe']}`\n"
            f"*Price:* `{sig['price']:,.2f}`\n"
            f"*Candle:* `{time_str}`"
        )

        await self.send_message(text)

    # ── Command handlers ─────────────────────────────────────
    async def get_agents_summary(self, db: AsyncSession) -> str:
        """Generate agents summary for /agents command."""
        result = await db.execute(text("""
            SELECT a.id, a.name, a.symbol, a.timeframe, a.is_active, a.mode,
                   COUNT(DISTINCT CASE WHEN p.status = 'OPEN' THEN p.id END) as open_positions,
                   COALESCE(SUM(CASE WHEN p.status = 'CLOSED' THEN p.pnl ELSE 0 END), 0) as total_pnl,
                   COUNT(CASE WHEN p.status = 'CLOSED' AND p.pnl > 0 THEN 1 END) as wins,
                   COUNT(CASE WHEN p.status = 'CLOSED' AND p.pnl < 0 THEN 1 END) as losses
            FROM agents a
            LEFT JOIN agent_positions p ON a.id = p.agent_id
            GROUP BY a.id, a.name, a.symbol, a.timeframe, a.is_active, a.mode
            ORDER BY a.id
        """))
        agents = result.fetchall()

        if not agents:
            return "📊 *Agents Summary*\n\nNo agents configured."

        lines = ["📊 *Agents Summary*\n"]
        for row in agents:
            id_, name, symbol, tf, active, mode, open_pos, pnl, wins, losses = row
            status = "🟢 Active" if active else "⏸️ Inactive"
            mode_icon = "📝" if mode == "paper" else "💰"
            total_trades = wins + losses
            win_rate = (wins / total_trades * 100) if total_trades > 0 else 0

            lines.append(
                f"\n*{name}* {status} {mode_icon}\n"
                f"• Symbol: `{symbol}` | TF: `{tf}`\n"
                f"• Open: `{open_pos}` | PnL: `{pnl:+.2f}`\n"
                f"• W/L: `{wins}/{losses}` ({win_rate:.1f}%)"
            )

        return "\n".join(lines)

    async def get_positions_summary(self, db: AsyncSession) -> str:
        """Generate open positions summary for /positions command."""
        result = await db.execute(text("""
            SELECT p.id, a.name, p.symbol, p.side, p.entry_price, 
                   p.stop_loss, p.take_profit, p.quantity, p.opened_at
            FROM agent_positions p
            JOIN agents a ON p.agent_id = a.id
            WHERE p.status = 'OPEN'
            ORDER BY p.opened_at DESC
        """))
        positions = result.fetchall()

        if not positions:
            return "📈 *Open Positions*\n\nNo open positions."

        lines = ["📈 *Open Positions*\n"]
        for row in positions:
            id_, agent, symbol, side, entry, sl, tp, qty, opened = row
            emoji = "🟢" if side == "LONG" else "🔴"
            age = datetime.now(timezone.utc) - opened.replace(tzinfo=timezone.utc)
            hours = int(age.total_seconds() / 3600)

            lines.append(
                f"\n{emoji} *{agent}* - `{symbol}` {side}\n"
                f"• Entry: `{entry:.2f}` | SL: `{sl:.2f}` | TP: `{tp:.2f}`\n"
                f"• Qty: `{qty:.6f}` | Age: `{hours}h`"
            )

        return "\n".join(lines)

    async def get_help_text(self) -> str:
        """Generate help text for /help command."""
        return (
            "🤖 *Reversal Pro Bot*\n\n"
            "*Available Commands:*\n\n"
            "/status - Overall system status\n"
            "/agents - List all agents with stats\n"
            "/positions - Show open positions\n"
            "/help - Show this help message\n\n"
            "_Automatic notifications are sent for:_\n"
            "• New reversal signals detected\n"
            "• Positions opened/closed\n"
            "• Agent activation/deactivation"
        )

    async def process_command(self, db: AsyncSession, command: str) -> str:
        """
        Process a Telegram command and return response.
        
        Args:
            db: Database session
            command: Command text (e.g., "/status", "/agents")
        
        Returns:
            Response text to send back
        """
        command = command.strip().lower()

        if command == "/start" or command == "/help":
            return await self.get_help_text()

        elif command == "/agents":
            return await self.get_agents_summary(db)

        elif command == "/positions":
            return await self.get_positions_summary(db)

        elif command == "/status":
            agents_text = await self.get_agents_summary(db)
            positions_text = await self.get_positions_summary(db)
            return f"{agents_text}\n\n{positions_text}"

        else:
            return (
                f"Unknown command: `{command}`\n\n"
                "Use /help to see available commands."
            )


# Backward-compatible singleton — delegates to centralized dependencies
def __getattr__(name):
    if name == "telegram_service":
        from ..dependencies import get_telegram_service
        return get_telegram_service()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
