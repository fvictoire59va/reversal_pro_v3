-- Migration: Add latency-reduction methodology flags to agents table
-- These allow per-agent activation of Volume-Adaptive, Candle-Pattern,
-- and CUSUM change-point detection services.
-- Date: 2026-02-25

ALTER TABLE agents
ADD COLUMN IF NOT EXISTS use_volume_adaptive  BOOLEAN NOT NULL DEFAULT TRUE,
ADD COLUMN IF NOT EXISTS use_candle_patterns  BOOLEAN NOT NULL DEFAULT TRUE,
ADD COLUMN IF NOT EXISTS use_cusum            BOOLEAN NOT NULL DEFAULT TRUE;

-- Back-fill NULLs for safety
UPDATE agents SET
    use_volume_adaptive = TRUE,
    use_candle_patterns = TRUE,
    use_cusum           = TRUE
WHERE use_volume_adaptive IS NULL
   OR use_candle_patterns IS NULL
   OR use_cusum IS NULL;

DO $$
BEGIN
    RAISE NOTICE 'Migration completed: Added use_volume_adaptive, use_candle_patterns, use_cusum to agents';
END $$;
