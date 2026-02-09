-- Migration: Add stable signal tracking columns to agent_positions
-- These columns track the signal's natural key (time + direction) instead of
-- relying on volatile signal IDs that change after DELETE+INSERT re-analysis.

ALTER TABLE agent_positions ADD COLUMN IF NOT EXISTS entry_signal_time TIMESTAMPTZ;
ALTER TABLE agent_positions ADD COLUMN IF NOT EXISTS entry_signal_is_bullish BOOLEAN;

-- Backfill from existing positions where signal still exists
UPDATE agent_positions ap
SET entry_signal_time = s.time,
    entry_signal_is_bullish = s.is_bullish
FROM signals s
WHERE s.id = ap.entry_signal_id
  AND ap.entry_signal_time IS NULL;

-- Index for fast duplicate lookups
CREATE INDEX IF NOT EXISTS idx_positions_signal_key
ON agent_positions (agent_id, entry_signal_time, entry_signal_is_bullish);
