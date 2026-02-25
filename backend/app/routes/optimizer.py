"""
Optimizer API routes — launch grid-search optimization and poll progress.
"""

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db, get_session_factory
from ..schemas import OptimizerStartRequest
from ..services.optimizer_service import optimizer_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/optimizer", tags=["optimizer"])


@router.post("/start")
async def start_optimization(
    body: OptimizerStartRequest = OptimizerStartRequest(),
):
    """Launch grid-search optimization in background.

    Any parameter sent in the body is *locked* to that value—only the
    remaining parameters are grid-searched.  This drastically reduces
    the number of combinations.
    """
    if optimizer_service.is_running:
        raise HTTPException(409, "Optimization already running")

    # Build dict of fixed (locked) parameters — only non-None values
    fixed_params = {}
    for key in (
        "sensitivity", "signal_mode", "confirmation_bars",
        "atr_length", "average_length", "absolute_reversal", "timeframes",
        "use_volume_adaptive", "use_candle_patterns", "use_cusum",
    ):
        val = getattr(body, key, None)
        if val is not None:
            fixed_params[key] = val

    db_factory = get_session_factory()
    await optimizer_service.start(
        db_factory, symbol=body.symbol, fixed_params=fixed_params,
    )

    return {
        "status": "started",
        "message": f"Optimization started for {body.symbol}",
        "fixed_params": fixed_params,
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
