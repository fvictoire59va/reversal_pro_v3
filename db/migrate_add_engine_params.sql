-- Migration: Add engine tuning parameters to agents table
-- These mirror the Pine Script inputs and allow per-agent optimization.
-- Date: 2026-02-23

ALTER TABLE agents
ADD COLUMN IF NOT EXISTS confirmation_bars  INTEGER           NOT NULL DEFAULT 0,
ADD COLUMN IF NOT EXISTS method             VARCHAR(20)       NOT NULL DEFAULT 'average',
ADD COLUMN IF NOT EXISTS atr_length         INTEGER           NOT NULL DEFAULT 5,
ADD COLUMN IF NOT EXISTS average_length     INTEGER           NOT NULL DEFAULT 5,
ADD COLUMN IF NOT EXISTS absolute_reversal  DOUBLE PRECISION  NOT NULL DEFAULT 0.5;

-- Back-fill any NULLs for safety
UPDATE agents SET
    confirmation_bars = 0,
    method            = 'average',
    atr_length        = 5,
    average_length    = 5,
    absolute_reversal = 0.5
WHERE confirmation_bars IS NULL
   OR method IS NULL
   OR atr_length IS NULL
   OR average_length IS NULL
   OR absolute_reversal IS NULL;

DO $$
BEGIN
    RAISE NOTICE 'Migration completed: Added confirmation_bars, method, atr_length, average_length, absolute_reversal to agents';
END $$;
