"""
FastAPI Application — Reversal Detection Pro v3.0
"""

import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

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
            from .dependencies import get_ingestion_service, get_agent_broker_service, get_analysis_service
            from .database import get_session_factory

            scheduler = AsyncIOScheduler()

            async def auto_fetch():
                async with get_session_factory()() as db:
                    try:
                        report = await get_ingestion_service().fetch_all_watchlist(db)
                        logger.info(f"Auto-refresh completed: {report}")
                    except Exception as e:
                        logger.error(f"Auto-refresh error: {e}")

            async def auto_analyze_and_run_agents():
                """Re-run analysis for all watchlist symbols, then run agents."""
                try:
                    async with get_session_factory()() as db:
                        try:
                            from .schemas import AnalysisRequest

                            # Get all active watchlist items
                            result = await db.execute(
                                text("SELECT symbol, timeframe FROM watchlist WHERE is_active = TRUE")
                            )
                            rows = result.fetchall()

                            # Re-run analysis for each
                            for row in rows:
                                try:
                                    request = AnalysisRequest(symbol=row[0], timeframe=row[1])
                                    await get_analysis_service().run_analysis(db, request)
                                except Exception as e:
                                    logger.warning(f"Auto-analysis error for {row[0]} {row[1]}: {e}")

                            # Run all active agents (each gets its own DB session)
                            await get_agent_broker_service().run_all_active_agents(db)

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
        from .dependencies import get_ingestion_service
        await get_ingestion_service().close_exchanges()
    except Exception:
        pass


# Create app
app = FastAPI(
    title="Reversal Detection Pro v3.0",
    description="Professional reversal detection API — Non-Repainting",
    version="3.0.0",
    lifespan=lifespan,
)

# ── CORS — use configured origins instead of wildcard ────────
settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
)


# ── Global middleware: request ID + timing ────────────────────
@app.middleware("http")
async def request_context_middleware(request: Request, call_next):
    request_id = uuid.uuid4().hex[:8]
    request.state.request_id = request_id
    start = time.perf_counter()
    response = await call_next(request)
    duration = time.perf_counter() - start
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Process-Time"] = f"{duration:.3f}"
    if duration > 2.0:
        logger.warning(
            f"[{request_id}] SLOW {request.method} {request.url.path} "
            f"→ {response.status_code} ({duration:.3f}s)"
        )
    return response


# ── Global exception handlers ────────────────────────────────
@app.exception_handler(ValueError)
async def value_error_handler(request: Request, exc: ValueError):
    return JSONResponse(status_code=400, content={"error": str(exc)})


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    request_id = getattr(getattr(request, "state", None), "request_id", "unknown")
    logger.error(f"[{request_id}] Unhandled: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"error": "Internal server error", "request_id": request_id},
    )


# ── API v1 Router ────────────────────────────────────────────
from fastapi import APIRouter

api_v1 = APIRouter(prefix="/api/v1")
api_v1.include_router(ohlcv.router)
api_v1.include_router(analysis.router)
api_v1.include_router(watchlist.router)
api_v1.include_router(agents.router)
api_v1.include_router(telegram.router)
app.include_router(api_v1)


@app.get("/")
async def root():
    return {
        "name": "Reversal Detection Pro",
        "version": "3.0.0",
        "status": "running",
        "docs": "/docs",
        "api": "/api/v1",
    }


@app.get("/health")
async def health():
    """Deep health check — verifies database and Redis connectivity."""
    checks = {}

    # Database check
    try:
        from .database import get_session_factory
        async with get_session_factory()() as db:
            await db.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as e:
        checks["database"] = f"error: {e}"

    # Redis check
    try:
        from .cache import get_redis_client
        await get_redis_client().ping()
        checks["redis"] = "ok"
    except Exception as e:
        checks["redis"] = f"error: {e}"

    all_ok = all(v == "ok" for v in checks.values())
    return JSONResponse(
        status_code=200 if all_ok else 503,
        content={
            "status": "healthy" if all_ok else "degraded",
            "checks": checks,
        },
    )
