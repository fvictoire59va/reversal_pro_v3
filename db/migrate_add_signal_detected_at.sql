-- Migration: Add detected_at column to signals table
-- This stores the first detection time of a signal (never updated on re-analysis)

ALTER TABLE signals ADD COLUMN IF NOT EXISTS detected_at TIMESTAMPTZ NOT NULL DEFAULT NOW();

-- Add a unique constraint so we can UPSERT signals by identity
-- A signal is uniquely identified by its candle time + symbol + timeframe + direction
CREATE UNIQUE INDEX IF NOT EXISTS idx_signals_unique
    ON signals (time, symbol, timeframe, is_bullish);
