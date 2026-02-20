"""Shared constants used across broker sub-modules."""

# Timeframe → seconds mapping
TIMEFRAME_SECONDS = {
    "1m": 60, "5m": 300, "15m": 900, "30m": 1800,
    "1h": 3600, "4h": 14400, "1d": 86400,
}

# Higher-timeframe map: for each TF, which HTF to check for trend confirmation
# Only 1 level above to keep it simple and avoid over-filtering
HTF_MAP = {
    "1m":  ["5m"],
    "5m":  ["15m"],
    "15m": ["1h"],
    "30m": ["1h"],
    "1h":  ["4h"],
    "4h":  ["1d"],
    "1d":  [],           # No higher TF to check
}
