-- Migration: Add analysis parameters to agents table
-- Date: 2026-02-07

-- Add new columns for analysis parameters
ALTER TABLE agents 
ADD COLUMN IF NOT EXISTS sensitivity VARCHAR(20) NOT NULL DEFAULT 'Medium',
ADD COLUMN IF NOT EXISTS signal_mode VARCHAR(30) NOT NULL DEFAULT 'Confirmed Only',
ADD COLUMN IF NOT EXISTS analysis_limit INTEGER NOT NULL DEFAULT 500;

-- Update existing agents to have default values
UPDATE agents 
SET 
    sensitivity = 'Medium',
    signal_mode = 'Confirmed Only',
    analysis_limit = 500
WHERE sensitivity IS NULL OR signal_mode IS NULL OR analysis_limit IS NULL;

-- Print success message
DO $$
BEGIN
    RAISE NOTICE 'Migration completed: Added sensitivity, signal_mode, and analysis_limit columns to agents table';
END $$;
