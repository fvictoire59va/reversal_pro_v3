-- Migration: Add balance column to agents table
-- The balance tracks the agent's available capital.
-- Initialized to trade_amount, set to 0 when in a position, restored on close.

ALTER TABLE agents ADD COLUMN IF NOT EXISTS balance DOUBLE PRECISION NOT NULL DEFAULT 100.0;

-- Initialize balance for existing agents:
-- If agent has an open position, balance = 0
-- Otherwise, balance = trade_amount + total realized PnL
UPDATE agents SET balance = 0
WHERE id IN (SELECT DISTINCT agent_id FROM agent_positions WHERE status = 'OPEN');

UPDATE agents SET balance = trade_amount + COALESCE(
    (SELECT SUM(pnl) FROM agent_positions WHERE agent_positions.agent_id = agents.id AND status IN ('CLOSED', 'STOPPED')),
    0
)
WHERE id NOT IN (SELECT DISTINCT agent_id FROM agent_positions WHERE status = 'OPEN');
