-- Add invested_eur column to agent_positions
-- Stores the EUR amount invested at open time to avoid exchange rate drift on close

ALTER TABLE agent_positions ADD COLUMN IF NOT EXISTS invested_eur DOUBLE PRECISION;

-- Backfill existing open positions with agent's trade_amount
UPDATE agent_positions ap
SET invested_eur = a.trade_amount
FROM agents a
WHERE ap.agent_id = a.id
  AND ap.invested_eur IS NULL;
