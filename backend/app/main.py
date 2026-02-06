"""
FastAPI Application — Reversal Detection Pro v3.0
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import get_settings
from .routes import ohlcv, analysis, watchlist

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
            from .database import async_session

            scheduler = AsyncIOScheduler()

            async def auto_fetch():
                async with async_session() as db:
                    try:
                        report = await ingestion_service.fetch_all_watchlist(db)
                        logger.info(f"Auto-refresh completed: {report}")
                    except Exception as e:
                        logger.error(f"Auto-refresh error: {e}")

            scheduler.add_job(
                auto_fetch, "interval",
                minutes=settings.auto_refresh_interval_minutes,
                id="auto_fetch",
            )
            scheduler.start()
            logger.info(
                f"Auto-refresh scheduler started "
                f"(every {settings.auto_refresh_interval_minutes} min)"
            )
        except Exception as e:
            logger.warning(f"Scheduler not started: {e}")

    yield  # App is running

    logger.info("Shutting down...")


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
