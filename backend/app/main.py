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
from .routes import ohlcv, analysis, watchlist, agents, telegram, optimizer

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
            from datetime import datetime, timezone
            from apscheduler.schedulers.asyncio import AsyncIOScheduler
            from .dependencies import get_ingestion_service, get_agent_broker_service, get_analysis_service
            from .database import get_session_factory
            from .cache import get_redis_client

            scheduler = AsyncIOScheduler()

            # ── Timeframe → seconds lookup ──
            TF_SECONDS = {
                "1m": 60, "5m": 300, "15m": 900, "30m": 1800,
                "1h": 3600, "4h": 14400, "1d": 86400,
            }
            HTF_MAP = {
                "1m": ["5m"], "5m": ["15m"], "15m": ["1h"],
                "30m": ["1h"], "1h": ["4h"], "4h": ["1d"], "1d": [],
            }

            _pipeline_run_count = 0

            async def autonomous_pipeline():
                """
                Unified autonomous pipeline — SEQUENTIAL execution:
                  1. Fetch OHLCV data  (throttled per symbol/timeframe)
                  2. Run analysis      (only for pairs with fresh data)
                  3. Run all agents    (always — for SL/TP monitoring)

                Merges watchlist + active agent pairs so agents are
                fully autonomous even when nobody is on the frontend.
                """
                nonlocal _pipeline_run_count
                _pipeline_run_count += 1
                pipeline_start = time.perf_counter()
                is_startup = (_pipeline_run_count == 1)
                label = "STARTUP" if is_startup else f"CYCLE #{_pipeline_run_count}"

                logger.info(f"[PIPELINE] ═══ {label} starting ═══")

                try:
                    redis = get_redis_client()

                    async with get_session_factory()() as db:
                        # ── Collect ALL (symbol, timeframe) pairs ──
                        wl_result = await db.execute(text(
                            "SELECT symbol, timeframe, exchange "
                            "FROM watchlist WHERE is_active = TRUE"
                        ))
                        watchlist_rows = wl_result.fetchall()

                        agent_result = await db.execute(text(
                            "SELECT DISTINCT symbol, timeframe "
                            "FROM agents WHERE is_active = TRUE"
                        ))
                        agent_rows = agent_result.fetchall()

                        # Merge: (symbol, tf) → exchange
                        all_pairs = {}
                        for row in watchlist_rows:
                            all_pairs[(row[0], row[1])] = row[2]
                        for row in agent_rows:
                            if (row[0], row[1]) not in all_pairs:
                                all_pairs[(row[0], row[1])] = settings.default_exchange

                        # Also include HTFs for each pair
                        htf_pairs = {}
                        for (symbol, tf) in list(all_pairs.keys()):
                            for htf in HTF_MAP.get(tf, []):
                                if (symbol, htf) not in all_pairs:
                                    htf_pairs[(symbol, htf)] = settings.default_exchange
                        all_pairs.update(htf_pairs)

                        if not all_pairs:
                            logger.debug("[PIPELINE] No active watchlist or agents")
                            return

                        # ── STEP 1: Fetch OHLCV data (throttled per TF) ──
                        fetched_pairs = set()
                        for (symbol, tf), exchange_id in all_pairs.items():
                            throttle_key = f"pipeline_fetch:{symbol}:{tf}"
                            tf_seconds = TF_SECONDS.get(tf, 300)
                            # On startup: ignore throttle, always fetch
                            if not is_startup and await redis.get(throttle_key):
                                continue

                            fetch_ttl = max(tf_seconds - 15, 30)
                            try:
                                count = await get_ingestion_service().fetch_and_store(
                                    db, symbol=symbol, timeframe=tf,
                                    exchange_id=exchange_id, limit=500,
                                )
                                fetched_pairs.add((symbol, tf))
                                await redis.setex(throttle_key, fetch_ttl, "1")
                                logger.info(
                                    f"[PIPELINE] Fetched {count} bars: "
                                    f"{symbol} {tf}"
                                )
                            except Exception as e:
                                logger.warning(
                                    f"[PIPELINE] Fetch error {symbol}/{tf}: {e}"
                                )

                        # ── STEP 2: Run analysis (for pairs with fresh data) ──
                        from .schemas import AnalysisRequest

                        analyzed = 0
                        for symbol, tf in fetched_pairs:
                            try:
                                # Use agent-specific params when available
                                agent_params = await db.execute(text(
                                    "SELECT sensitivity, signal_mode, "
                                    "       analysis_limit "
                                    "FROM agents "
                                    "WHERE symbol = :s AND timeframe = :tf "
                                    "  AND is_active = TRUE "
                                    "ORDER BY created_at LIMIT 1"
                                ), {"s": symbol, "tf": tf})
                                agent_row = agent_params.fetchone()

                                if agent_row:
                                    request = AnalysisRequest(
                                        symbol=symbol, timeframe=tf,
                                        limit=agent_row[2],
                                        sensitivity=agent_row[0],
                                        signal_mode=agent_row[1],
                                    )
                                else:
                                    request = AnalysisRequest(
                                        symbol=symbol, timeframe=tf,
                                    )

                                await get_analysis_service().run_analysis(
                                    db, request
                                )
                                analyzed += 1
                            except Exception as e:
                                logger.warning(
                                    f"[PIPELINE] Analysis error "
                                    f"{symbol}/{tf}: {e}"
                                )

                        logger.info(
                            f"[PIPELINE] Fetched {len(fetched_pairs)} pairs, "
                            f"analyzed {analyzed}"
                        )

                        # ── STEP 3: Run all active agents ──
                        try:
                            await get_agent_broker_service().run_all_active_agents(db)
                        except Exception as e:
                            logger.error(
                                f"[PIPELINE] Agent cycle error: {e}",
                                exc_info=True,
                            )

                except Exception as e:
                    logger.critical(
                        f"[PIPELINE] CRASHED: {e}", exc_info=True
                    )
                finally:
                    elapsed = time.perf_counter() - pipeline_start
                    logger.info(
                        f"[PIPELINE] ═══ {label} done in {elapsed:.1f}s ═══"
                    )
                    # Heartbeat key — used by /health to verify scheduler
                    try:
                        redis = get_redis_client()
                        await redis.setex(
                            "pipeline_heartbeat",
                            600,  # 10-min TTL
                            datetime.now(timezone.utc).isoformat(),
                        )
                    except Exception:
                        pass

            # Schedule the unified pipeline
            pipeline_interval = min(
                settings.auto_refresh_interval_minutes,
                settings.agent_cycle_interval_minutes,
            )

            scheduler.add_job(
                autonomous_pipeline, "interval",
                minutes=pipeline_interval,
                id="autonomous_pipeline",
                max_instances=1,
                misfire_grace_time=180,
                coalesce=True,
                # Fire immediately on startup to catch up
                next_run_time=datetime.now(timezone.utc),
            )

            scheduler.start()
            app.state.scheduler = scheduler  # prevent GC, enable health check

            logger.info(
                f"[PIPELINE] Autonomous scheduler started "
                f"(every {pipeline_interval} min, immediate first run)"
            )
        except Exception as e:
            logger.warning(f"Scheduler not started: {e}")

    yield  # App is running

    # Cleanup
    logger.info("Shutting down...")
    if hasattr(app, "state") and hasattr(app.state, "scheduler"):
        try:
            app.state.scheduler.shutdown(wait=False)
        except Exception:
            pass
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
api_v1.include_router(optimizer.router)
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
    """Deep health check — verifies database, Redis, and scheduler."""
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
        redis = get_redis_client()
        await redis.ping()
        checks["redis"] = "ok"
    except Exception as e:
        checks["redis"] = f"error: {e}"

    # Scheduler / pipeline check — heartbeat written by autonomous_pipeline
    try:
        from .cache import get_redis_client
        redis = get_redis_client()
        heartbeat = await redis.get("pipeline_heartbeat")
        if heartbeat:
            checks["scheduler"] = f"ok (last: {heartbeat.decode() if isinstance(heartbeat, bytes) else heartbeat})"
        else:
            # Might just not have run yet on fresh start
            sched = getattr(getattr(app, "state", None), "scheduler", None)
            if sched and sched.running:
                checks["scheduler"] = "ok (starting)"
            else:
                checks["scheduler"] = "warning: no heartbeat"
    except Exception as e:
        checks["scheduler"] = f"error: {e}"

    core_checks = {k: v for k, v in checks.items() if k in ("database", "redis")}
    all_ok = all(v.startswith("ok") for v in core_checks.values())
    return JSONResponse(
        status_code=200 if all_ok else 503,
        content={
            "status": "healthy" if all_ok else "degraded",
            "checks": checks,
        },
    )
