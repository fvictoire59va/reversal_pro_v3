"""
FastAPI Application — Reversal Detection Pro v3.0
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import get_settings
from .routes import ohlcv, analysis, watchlist, agents, telegram

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events."""
    logger.info("=" * 60)
    logger.info("  REVERSAL DETECTION PRO v3.0 — API Starting")
    logger.info("=" * 60)

    settings = get_settings()

    # Start background scheduler for auto-refresh
    if settings.auto_refresh_enabled:
        try:
            from apscheduler.schedulers.asyncio import AsyncIOScheduler
            from .services.data_ingestion import ingestion_service
            from .services.agent_broker import agent_broker_service
            from .database import async_session

            scheduler = AsyncIOScheduler()

            async def auto_fetch():
                async with async_session() as db:
                    try:
                        report = await ingestion_service.fetch_all_watchlist(db)
                        logger.info(f"Auto-refresh completed: {report}")
                    except Exception as e:
                        logger.error(f"Auto-refresh error: {e}")

            async def auto_analyze_and_run_agents():
                """Re-run analysis for all watchlist symbols, then run agents."""
                try:
                    async with async_session() as db:
                        try:
                            from .services.analysis_service import analysis_service
                            from .schemas import AnalysisRequest
                            from sqlalchemy import text

                            # Get all active watchlist items
                            result = await db.execute(
                                text("SELECT symbol, timeframe FROM watchlist WHERE is_active = TRUE")
                            )
                            rows = result.fetchall()

                            # Re-run analysis for each
                            for row in rows:
                                try:
                                    request = AnalysisRequest(symbol=row[0], timeframe=row[1])
                                    await analysis_service.run_analysis(db, request)
                                except Exception as e:
                                    logger.warning(f"Auto-analysis error for {row[0]} {row[1]}: {e}")

                            # Run all active agents (each gets its own DB session)
                            await agent_broker_service.run_all_active_agents(db)

                        except Exception as e:
                            logger.error(f"Auto-analyze/agents error: {e}", exc_info=True)
                except Exception as e:
                    logger.critical(f"SCHEDULER JOB CRASHED: {e}", exc_info=True)

            scheduler.add_job(
                auto_fetch, "interval",
                minutes=settings.auto_refresh_interval_minutes,
                id="auto_fetch",
                max_instances=1,
                misfire_grace_time=120,  # tolerate up to 2 min delay
                coalesce=True,           # if multiple missed, run once
            )

            # Agent cycle runs after fetch to allow data to settle
            scheduler.add_job(
                auto_analyze_and_run_agents, "interval",
                minutes=settings.agent_cycle_interval_minutes,
                id="agent_cycle",
                max_instances=1,
                misfire_grace_time=120,
                coalesce=True,
            )

            scheduler.start()
            logger.info(
                f"Auto-refresh scheduler started "
                f"(every {settings.auto_refresh_interval_minutes} min)"
            )
            logger.info(
                f"Agent broker scheduler started "
                f"(every {settings.agent_cycle_interval_minutes} min)"
            )
        except Exception as e:
            logger.warning(f"Scheduler not started: {e}")

    yield  # App is running

    # Cleanup: close async exchange sessions
    logger.info("Shutting down...")
    try:
        from .services.data_ingestion import ingestion_service
        await ingestion_service.close_exchanges()
    except Exception:
        pass


# Create app
app = FastAPI(
    title="Reversal Detection Pro v3.0",
    description="Professional reversal detection API — Non-Repainting",
    version="3.0.0",
    lifespan=lifespan,
)

# CORS
settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Open for Docker internal + dev
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register routes
app.include_router(ohlcv.router)
app.include_router(analysis.router)
app.include_router(watchlist.router)
app.include_router(agents.router)
app.include_router(telegram.router)


@app.get("/")
async def root():
    return {
        "name": "Reversal Detection Pro",
        "version": "3.0.0",
        "status": "running",
        "docs": "/docs",
    }


@app.get("/health")
async def health():
    return {"status": "healthy"}
