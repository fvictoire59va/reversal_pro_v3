-- ============================================================================
-- TimescaleDB Initialization — Reversal Detection Pro
-- ============================================================================

-- Enable TimescaleDB extension
CREATE EXTENSION IF NOT EXISTS timescaledb;

-- ============================================================================
-- OHLCV Bars Table (hypertable for time-series performance)
-- ============================================================================
CREATE TABLE IF NOT EXISTS ohlcv (
    time        TIMESTAMPTZ NOT NULL,
    symbol      TEXT        NOT NULL,
    timeframe   TEXT        NOT NULL DEFAULT '1h',
    open        DOUBLE PRECISION NOT NULL,
    high        DOUBLE PRECISION NOT NULL,
    low         DOUBLE PRECISION NOT NULL,
    close       DOUBLE PRECISION NOT NULL,
    volume      DOUBLE PRECISION NOT NULL DEFAULT 0,
    PRIMARY KEY (time, symbol, timeframe)
);

SELECT create_hypertable('ohlcv', 'time', if_not_exists => TRUE);

-- Indexes for fast queries
CREATE INDEX IF NOT EXISTS idx_ohlcv_symbol_tf ON ohlcv (symbol, timeframe, time DESC);

-- ============================================================================
-- Indicators / EMA cache
-- ============================================================================
CREATE TABLE IF NOT EXISTS indicators (
    time        TIMESTAMPTZ NOT NULL,
    symbol      TEXT        NOT NULL,
    timeframe   TEXT        NOT NULL DEFAULT '1h',
    ema_9       DOUBLE PRECISION,
    ema_14      DOUBLE PRECISION,
    ema_21      DOUBLE PRECISION,
    atr         DOUBLE PRECISION,
    trend       TEXT,           -- BULLISH / BEARISH / NEUTRAL
    PRIMARY KEY (time, symbol, timeframe)
);

SELECT create_hypertable('indicators', 'time', if_not_exists => TRUE);

CREATE INDEX IF NOT EXISTS idx_indicators_symbol_tf ON indicators (symbol, timeframe, time DESC);

-- ============================================================================
-- Reversal Signals
-- ============================================================================
CREATE TABLE IF NOT EXISTS signals (
    id              SERIAL,
    time            TIMESTAMPTZ NOT NULL,
    symbol          TEXT        NOT NULL,
    timeframe       TEXT        NOT NULL DEFAULT '1h',
    bar_index       INTEGER     NOT NULL,
    price           DOUBLE PRECISION NOT NULL,
    actual_price    DOUBLE PRECISION NOT NULL,
    is_bullish      BOOLEAN     NOT NULL,
    is_preview      BOOLEAN     NOT NULL DEFAULT FALSE,
    signal_label    TEXT        NOT NULL DEFAULT 'REVERSAL',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

SELECT create_hypertable('signals', 'time', if_not_exists => TRUE);

CREATE INDEX IF NOT EXISTS idx_signals_symbol_tf ON signals (symbol, timeframe, time DESC);

-- ============================================================================
-- Supply / Demand Zones
-- ============================================================================
CREATE TABLE IF NOT EXISTS zones (
    id              SERIAL,
    time            TIMESTAMPTZ NOT NULL,
    symbol          TEXT        NOT NULL,
    timeframe       TEXT        NOT NULL DEFAULT '1h',
    zone_type       TEXT        NOT NULL,  -- SUPPLY / DEMAND
    center_price    DOUBLE PRECISION NOT NULL,
    top_price       DOUBLE PRECISION NOT NULL,
    bottom_price    DOUBLE PRECISION NOT NULL,
    start_bar       INTEGER     NOT NULL,
    end_bar         INTEGER     NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

SELECT create_hypertable('zones', 'time', if_not_exists => TRUE);

-- ============================================================================
-- Analysis Runs (metadata)
-- ============================================================================
CREATE TABLE IF NOT EXISTS analysis_runs (
    id              SERIAL PRIMARY KEY,
    symbol          TEXT        NOT NULL,
    timeframe       TEXT        NOT NULL,
    sensitivity     TEXT        NOT NULL DEFAULT 'Medium',
    signal_mode     TEXT        NOT NULL DEFAULT 'Confirmed Only',
    atr_multiplier  DOUBLE PRECISION,
    current_atr     DOUBLE PRECISION,
    threshold       DOUBLE PRECISION,
    current_trend   TEXT,
    total_signals   INTEGER DEFAULT 0,
    total_zones     INTEGER DEFAULT 0,
    bars_analyzed   INTEGER DEFAULT 0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ============================================================================
-- Symbols watchlist
-- ============================================================================
CREATE TABLE IF NOT EXISTS watchlist (
    symbol      TEXT        NOT NULL,
    timeframe   TEXT        NOT NULL DEFAULT '1h',
    exchange    TEXT        NOT NULL DEFAULT 'binance',
    is_active   BOOLEAN     NOT NULL DEFAULT TRUE,
    added_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (symbol, timeframe)
);

-- Default watchlist
INSERT INTO watchlist (symbol, timeframe, exchange) VALUES
    ('BTC/USDT', '1h', 'binance'),
    ('ETH/USDT', '1h', 'binance'),
    ('BTC/USDT', '15m', 'binance')
ON CONFLICT DO NOTHING;

-- ============================================================================
-- Agent Brokers
-- ============================================================================
CREATE TABLE IF NOT EXISTS agents (
    id              SERIAL PRIMARY KEY,
    name            VARCHAR(50) UNIQUE NOT NULL,
    symbol          VARCHAR(20) NOT NULL,
    timeframe       VARCHAR(10) NOT NULL,
    trade_amount    DOUBLE PRECISION NOT NULL DEFAULT 100.0,
    balance         DOUBLE PRECISION NOT NULL DEFAULT 100.0,
    is_active       BOOLEAN NOT NULL DEFAULT FALSE,
    mode            VARCHAR(10) NOT NULL DEFAULT 'paper',  -- 'paper' or 'live'
    sensitivity     VARCHAR(20) NOT NULL DEFAULT 'Medium',
    signal_mode     VARCHAR(30) NOT NULL DEFAULT 'Confirmed Only',
    analysis_limit  INTEGER NOT NULL DEFAULT 500,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ============================================================================
-- Agent Positions (open / closed)
-- ============================================================================
CREATE TABLE IF NOT EXISTS agent_positions (
    id              SERIAL PRIMARY KEY,
    agent_id        INTEGER NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    symbol          VARCHAR(20) NOT NULL,
    side            VARCHAR(5) NOT NULL,           -- 'LONG' or 'SHORT'
    entry_price     DOUBLE PRECISION NOT NULL,
    exit_price      DOUBLE PRECISION,
    stop_loss       DOUBLE PRECISION NOT NULL,
    take_profit     DOUBLE PRECISION,
    quantity        DOUBLE PRECISION NOT NULL,
    status          VARCHAR(10) NOT NULL DEFAULT 'OPEN',  -- OPEN, CLOSED, STOPPED
    entry_signal_id INTEGER,
    exit_signal_id  INTEGER,
    pnl             DOUBLE PRECISION,
    pnl_percent     DOUBLE PRECISION,
    opened_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    closed_at       TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_positions_agent ON agent_positions (agent_id, status);
CREATE INDEX IF NOT EXISTS idx_positions_status ON agent_positions (status);

-- ============================================================================
-- Agent Activity Logs
-- ============================================================================
CREATE TABLE IF NOT EXISTS agent_logs (
    id              SERIAL PRIMARY KEY,
    agent_id        INTEGER NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    action          VARCHAR(50) NOT NULL,
    details         JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_logs_agent ON agent_logs (agent_id, created_at DESC);
