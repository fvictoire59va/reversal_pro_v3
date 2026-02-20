# ============================================================================
# Reversal Detection Pro v3.0 — Full Stack Application
# ============================================================================
# © 2025-2026 NPR21 — Converted to Python by GitHub Copilot
# ============================================================================
#
# DESCRIPTION:
#   Professional reversal detection system with a modern web frontend (TradingView
#   charts), FastAPI backend, TimescaleDB for time-series storage, Redis cache,
#   and Docker orchestration. Non-repainting signals, ATR-based sensitivity,
#   triple EMA trend system, supply/demand zones, automated agent broker
#   (paper & live trading via Hyperliquid DEX), and Telegram notifications.
#
# ============================================================================
# ARCHITECTURE
# ============================================================================
#
#   ┌──────────────┐     ┌──────────────┐     ┌────────────────┐
#   │   Frontend   │────▶│    Nginx      │────▶│    FastAPI     │
#   │  (Vite +     │     │  (reverse     │     │   (Python)     │
#   │  TradingView │     │   proxy)      │     │                │
#   │  Charts)     │     └──────────────┘     └───────┬────────┘
#   └──────────────┘                                  │
#                                          ┌──────────┴──────────┐
#                                          │                     │
#                                    ┌─────▼─────┐       ┌──────▼──────┐
#                                    │TimescaleDB │       │   Redis     │
#                                    │(PostgreSQL)│       │  (Cache)    │
#                                    │            │       │             │
#                                    └────────────┘       └─────────────┘
#                                                           │
#                                                    ┌──────▼──────┐
#                                                    │ Hyperliquid │
#                                                    │   DEX API   │
#                                                    └─────────────┘
#
# ============================================================================
# PROJECT STRUCTURE
# ============================================================================
#
#   ├── docker-compose.yml           # Full stack orchestration
#   ├── docker-compose.swarm.yml     # Docker Swarm deployment
#   ├── docker-compose.monitoring.yml # Monitoring stack
#   ├── .env                         # Environment variables
#   │
#   ├── reversal_pro/                # Core analysis engine (Clean Architecture)
#   │   ├── domain/                  # Entities, enums, value objects
#   │   ├── application/             # Services + use cases
#   │   ├── infrastructure/          # Data providers, repositories
#   │   ├── presentation/            # Console + chart output (CLI)
#   │   └── config/                  # AppConfig settings
#   │
#   ├── backend/                     # FastAPI REST API
#   │   ├── Dockerfile
#   │   ├── requirements.txt
#   │   └── app/
#   │       ├── main.py              # FastAPI app + lifespan
#   │       ├── config.py            # Pydantic Settings
#   │       ├── database.py          # AsyncPG + SQLAlchemy
#   │       ├── cache.py             # Redis client
#   │       ├── models.py            # ORM models (TimescaleDB)
#   │       ├── schemas.py           # Pydantic API schemas
#   │       ├── routes/              # API endpoints
#   │       │   ├── ohlcv.py         # OHLCV CRUD + fetch/upload
#   │       │   ├── analysis.py      # Analysis + chart data
#   │       │   ├── watchlist.py     # Watchlist management
#   │       │   ├── agents.py        # Agent broker CRUD + trading
#   │       │   └── telegram.py      # Telegram webhook
#   │       └── services/
#   │           ├── data_ingestion.py      # ccxt (async) / CSV → TimescaleDB
#   │           ├── analysis_service.py    # Engine bridge + persistence
#   │           ├── agent_broker.py        # Automated trading agent
#   │           ├── hyperliquid_client.py  # Hyperliquid DEX integration
#   │           └── telegram_service.py    # Telegram bot notifications
#   │
#   ├── frontend/                    # Vite SPA
#   │   ├── Dockerfile
#   │   ├── package.json
#   │   ├── vite.config.js
#   │   ├── index.html
#   │   └── src/
#   │       ├── main.js              # App controller
#   │       ├── chart.js             # TradingView Lightweight Charts
#   │       ├── api.js               # API client
#   │       └── styles.css           # Dark theme UI
#   │
#   ├── nginx/
#   │   └── nginx.conf               # Reverse proxy config
#   │
#   └── db/
#       ├── init.sql                  # TimescaleDB schema + hypertables
#       └── run-migrations.sh         # Database migration runner
#
# ============================================================================
# TECH STACK
# ============================================================================
#
#   Frontend:   Vite + TradingView Lightweight Charts (official open-source)
#   Backend:    FastAPI (async Python) + SQLAlchemy + asyncpg
#   Database:   TimescaleDB (PostgreSQL + hypertables) — time-series optimized
#   Cache:      Redis (LRU, 256MB) — chart/OHLCV cache
#   Proxy:      Nginx (static serving + API proxy)
#   Data:       ccxt async (130+ exchanges) + CSV import
#   Trading:    Hyperliquid DEX (perpetual futures, paper/live)
#   Alerts:     Telegram Bot API (signal + position notifications)
#   Engine:     Custom reversal detection (Clean Architecture Python)
#   Container:  Docker Compose (5 services) / Docker Swarm (production)
#
# ============================================================================
# QUICK START
# ============================================================================
#
#   1. Start the stack:
#        docker compose up -d --build
#
#   2. Open the app:
#        http://localhost:8080
#
#   3. API docs (Swagger):
#        http://localhost:8080/docs
#
#   4. Fetch live data (via UI "Fetch" button or API):
#        curl -X POST http://localhost:8080/api/ohlcv/fetch/BTC-USDT/1h
#
#   5. Upload CSV:
#        curl -X POST http://localhost:8080/api/ohlcv/upload \
#          -F "file=@data/sample_BTCUSDT_1h.csv" \
#          -F "symbol=BTC/USDT" -F "timeframe=1h"
#
#   6. Run analysis:
#        curl http://localhost:8080/api/analysis/chart/BTC-USDT/1h
#
#   7. Stop:
#        docker compose down
#
# ============================================================================
# CLI MODE (without Docker)
# ============================================================================
#
#   pip install -r requirements.txt
#   python generate_sample_data.py
#   python -m reversal_pro --source csv --file data/sample_BTCUSDT_1h.csv --chart
#
# ============================================================================
# API ENDPOINTS
# ============================================================================
#
#   OHLCV Data:
#   GET   /api/ohlcv/{symbol}/{timeframe}          — Get stored OHLCV bars
#   POST  /api/ohlcv/fetch/{symbol}/{timeframe}    — Fetch from exchange → DB
#   POST  /api/ohlcv/upload                        — Upload CSV → DB
#   POST  /api/ohlcv/fetch-watchlist                — Fetch all watchlist symbols
#
#   Analysis:
#   POST  /api/analysis/run                        — Run full analysis
#   GET   /api/analysis/chart/{symbol}/{timeframe}  — Chart data (TradingView format)
#   GET   /api/analysis/signals/{symbol}/{timeframe} — Get reversal signals
#   GET   /api/analysis/zones/{symbol}/{timeframe}  — Get supply/demand zones
#
#   Watchlist:
#   GET   /api/watchlist/                           — List watchlist
#   POST  /api/watchlist/                           — Add to watchlist
#   DELETE /api/watchlist/{symbol}/{timeframe}       — Remove from watchlist
#
#   Agent Broker:
#   GET   /api/agents/                              — List all agents with positions
#   POST  /api/agents/                              — Create a new trading agent
#   PATCH /api/agents/{id}/toggle                   — Enable/disable agent
#   PUT   /api/agents/{id}                          — Update agent settings
#   DELETE /api/agents/{id}                          — Delete agent
#   POST  /api/agents/positions/{id}/close           — Force-close a position
#   GET   /api/agents/{id}/logs                     — Agent activity logs
#   GET   /api/agents/{id}/performance              — Agent P&L performance
#   GET   /api/agents/positions-by-chart/{s}/{tf}   — Positions overlay for chart
#   GET   /api/agents/skipped-signals/{s}/{tf}       — Skipped signal markers
#
#   Telegram:
#   POST  /api/telegram/webhook                     — Telegram bot webhook
#
# ============================================================================
