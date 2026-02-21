"""
Optimizer API routes — launch grid-search optimization and poll progress.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db, get_session_factory
from ..services.optimizer_service import optimizer_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/optimizer", tags=["optimizer"])


@router.post("/start")
async def start_optimization(
    symbol: str = "BTC/USDT",
    db: AsyncSession = Depends(get_db),
):
    """Launch grid-search optimization in background.

    Tests all (sensitivity × signal_mode) combinations per timeframe,
    runs a backtest, and creates inactive agents with best params.
    """
    if optimizer_service.is_running:
        raise HTTPException(409, "Optimization already running")

    db_factory = get_session_factory()
    await optimizer_service.start(db_factory, symbol=symbol)

    return {
        "status": "started",
        "message": f"Optimization started for {symbol}",
    }


@router.get("/progress")
async def get_optimization_progress():
    """Poll current optimization progress."""
    progress = await optimizer_service.get_progress()
    return {
        "status": progress.status,
        "started_at": progress.started_at,
        "finished_at": progress.finished_at,
        "current_tf": progress.current_tf,
        "current_combo": progress.current_combo,
        "total_combos": progress.total_combos,
        "elapsed_seconds": progress.elapsed_seconds,
        "results": progress.results,
        "error": progress.error,
    }
