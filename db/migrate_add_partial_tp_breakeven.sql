-- Migration: Add columns for partial take profit and breakeven mechanism
-- Points 10 (breakeven) and 14 (partial TP) of the strategy audit

-- original_stop_loss: preserves the initial SL so breakeven/trailing can modify stop_loss
ALTER TABLE agent_positions ADD COLUMN IF NOT EXISTS original_stop_loss FLOAT;

-- tp2: second take profit target (used after partial close at TP1)
ALTER TABLE agent_positions ADD COLUMN IF NOT EXISTS tp2 FLOAT;

-- partial_closed: whether the first partial TP has been taken (50%)
ALTER TABLE agent_positions ADD COLUMN IF NOT EXISTS partial_closed BOOLEAN DEFAULT FALSE;

-- partial_pnl: PnL realized from the partial close (EUR)
ALTER TABLE agent_positions ADD COLUMN IF NOT EXISTS partial_pnl FLOAT;

-- original_quantity: quantity at position open (before partial close)
ALTER TABLE agent_positions ADD COLUMN IF NOT EXISTS original_quantity FLOAT;

-- Backfill: set original_stop_loss = stop_loss and original_quantity = quantity for existing positions
UPDATE agent_positions SET original_stop_loss = stop_loss WHERE original_stop_loss IS NULL;
UPDATE agent_positions SET original_quantity = quantity WHERE original_quantity IS NULL;
