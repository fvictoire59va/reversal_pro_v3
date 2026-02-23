-- ============================================================================
-- Additional performance indexes
-- ============================================================================

-- Fast lookup: open positions per agent (used on every tick)
CREATE INDEX IF NOT EXISTS idx_positions_agent_open
    ON agent_positions (agent_id)
    WHERE status = 'OPEN';

-- Covering index for signal upsert dedup queries
CREATE INDEX IF NOT EXISTS idx_signals_symbol_tf_bull
    ON signals (symbol, timeframe, is_bullish, time DESC);

-- Agents by symbol+timeframe (used by agent_broker scheduler)
CREATE INDEX IF NOT EXISTS idx_agents_symbol_tf
    ON agents (symbol, timeframe)
    WHERE is_active = TRUE;

-- Agent logs: faster position-specific queries
CREATE INDEX IF NOT EXISTS idx_logs_agent_action
    ON agent_logs (agent_id, action);
