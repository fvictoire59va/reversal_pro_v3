-- Migration: Add unrealized PnL columns to agent_positions table
-- Date: 2026-02-08

ALTER TABLE agent_positions
ADD COLUMN IF NOT EXISTS unrealized_pnl FLOAT DEFAULT NULL,
ADD COLUMN IF NOT EXISTS unrealized_pnl_percent FLOAT DEFAULT NULL,
ADD COLUMN IF NOT EXISTS current_price FLOAT DEFAULT NULL,
ADD COLUMN IF NOT EXISTS pnl_updated_at TIMESTAMPTZ DEFAULT NULL;
