-- Migration: Add column for trailing stop mechanism
-- Tracks the best price reached during the position's lifetime
-- to calculate the trailing stop level.

-- best_price: highest price for LONG, lowest price for SHORT
ALTER TABLE agent_positions ADD COLUMN IF NOT EXISTS best_price FLOAT;

-- Backfill: set best_price to entry_price for existing open positions
UPDATE agent_positions SET best_price = entry_price WHERE best_price IS NULL AND status = 'OPEN';
