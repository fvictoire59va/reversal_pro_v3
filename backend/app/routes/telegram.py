"""Telegram bot webhooks and manual commands."""

import logging
from fastapi import APIRouter, Depends, Body
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..services.telegram_service import telegram_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/telegram", tags=["telegram"])


@router.post("/webhook")
async def telegram_webhook(
    update: dict = Body(...),
    db: AsyncSession = Depends(get_db)
):
    """
    Handle incoming Telegram bot updates (webhook endpoint).
    
    To set up webhook:
    https://api.telegram.org/bot<TOKEN>/setWebhook?url=https://yourdomain.com/api/telegram/webhook
    """
    try:
        # Extract message
        if "message" not in update:
            return {"ok": True}

        message = update["message"]
        text = message.get("text", "")
        
        if not text.startswith("/"):
            return {"ok": True}

        # Process command
        response = await telegram_service.process_command(db, text)
        
        # Send response
        await telegram_service.send_message(response)
        
        return {"ok": True}

    except Exception as e:
        logger.error(f"Telegram webhook error: {e}", exc_info=True)
        return {"ok": False, "error": str(e)}


@router.post("/send-status")
async def send_status(db: AsyncSession = Depends(get_db)):
    """Manually trigger a status update to Telegram."""
    try:
        status_text = await telegram_service.process_command(db, "/status")
        success = await telegram_service.send_message(status_text)
        
        return {
            "success": success,
            "message": "Status sent to Telegram" if success else "Failed to send"
        }
    except Exception as e:
        logger.error(f"Failed to send status: {e}")
        return {"success": False, "error": str(e)}


@router.post("/send-agents")
async def send_agents(db: AsyncSession = Depends(get_db)):
    """Manually trigger agents summary to Telegram."""
    try:
        agents_text = await telegram_service.process_command(db, "/agents")
        success = await telegram_service.send_message(agents_text)
        
        return {
            "success": success,
            "message": "Agents summary sent" if success else "Failed to send"
        }
    except Exception as e:
        logger.error(f"Failed to send agents: {e}")
        return {"success": False, "error": str(e)}


@router.post("/send-positions")
async def send_positions(db: AsyncSession = Depends(get_db)):
    """Manually trigger positions summary to Telegram."""
    try:
        positions_text = await telegram_service.process_command(db, "/positions")
        success = await telegram_service.send_message(positions_text)
        
        return {
            "success": success,
            "message": "Positions sent" if success else "Failed to send"
        }
    except Exception as e:
        logger.error(f"Failed to send positions: {e}")
        return {"success": False, "error": str(e)}


@router.post("/test")
async def test_telegram():
    """Test Telegram bot connection."""
    try:
        success = await telegram_service.send_message("🤖 *Reversal Pro*\n\nBot is working!")
        return {
            "success": success,
            "message": "Test message sent" if success else "Failed to send test message"
        }
    except Exception as e:
        logger.error(f"Telegram test failed: {e}")
        return {"success": False, "error": str(e)}
