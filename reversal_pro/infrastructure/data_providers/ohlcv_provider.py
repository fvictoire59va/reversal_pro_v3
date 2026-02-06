"""
OHLCV data provider — abstraction + CSV / ccxt implementations.
"""

import csv
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from ...domain.value_objects import OHLCVBar


class OHLCVProvider(ABC):
    """Abstract base class for OHLCV data sources."""

    @abstractmethod
    def fetch(
        self,
        symbol: str,
        timeframe: str = "1h",
        limit: int = 500,
        since: Optional[datetime] = None,
    ) -> List[OHLCVBar]:
        """Fetch OHLCV bars for the given symbol/timeframe."""
        ...


class CSVProvider(OHLCVProvider):
    """
    Load OHLCV data from a CSV file.

    Expected columns: timestamp, open, high, low, close, volume
    (header is optional; auto-detected).
    """

    def __init__(self, file_path: str):
        self.file_path = Path(file_path)

    def fetch(
        self,
        symbol: str = "",
        timeframe: str = "",
        limit: int = 0,
        since: Optional[datetime] = None,
    ) -> List[OHLCVBar]:
        bars: List[OHLCVBar] = []

        with open(self.file_path, newline="", encoding="utf-8") as f:
            reader = csv.reader(f)
            header = next(reader, None)

            # Detect column order from header
            col_map = self._detect_columns(header)

            for row in reader:
                try:
                    bar = OHLCVBar(
                        timestamp=row[col_map["timestamp"]],
                        open=float(row[col_map["open"]]),
                        high=float(row[col_map["high"]]),
                        low=float(row[col_map["low"]]),
                        close=float(row[col_map["close"]]),
                        volume=float(row[col_map["volume"]]) if "volume" in col_map else 0.0,
                    )
                    bars.append(bar)
                except (ValueError, IndexError):
                    continue

        if limit > 0:
            bars = bars[-limit:]

        return bars

    @staticmethod
    def _detect_columns(header: Optional[list]) -> dict:
        """Map column names to indices."""
        default = {
            "timestamp": 0, "open": 1, "high": 2,
            "low": 3, "close": 4, "volume": 5,
        }
        if header is None:
            return default

        col_map = {}
        lower_header = [h.strip().lower() for h in header]
        synonyms = {
            "timestamp": ["timestamp", "date", "time", "datetime"],
            "open": ["open", "o"],
            "high": ["high", "h"],
            "low": ["low", "l"],
            "close": ["close", "c"],
            "volume": ["volume", "vol", "v"],
        }
        for key, names in synonyms.items():
            for name in names:
                if name in lower_header:
                    col_map[key] = lower_header.index(name)
                    break

        # Fallback to positional
        for key, idx in default.items():
            if key not in col_map:
                col_map[key] = idx

        return col_map


class CCXTProvider(OHLCVProvider):
    """
    Fetch live/historical OHLCV data via the ccxt library.
    Requires: pip install ccxt
    """

    def __init__(self, exchange_id: str = "binance", api_key: str = "", secret: str = ""):
        try:
            import ccxt
        except ImportError:
            raise ImportError(
                "ccxt is required for live data. Install with: pip install ccxt"
            )

        exchange_class = getattr(ccxt, exchange_id, None)
        if exchange_class is None:
            raise ValueError(f"Exchange '{exchange_id}' not found in ccxt")

        config = {}
        if api_key:
            config["apiKey"] = api_key
        if secret:
            config["secret"] = secret

        self.exchange = exchange_class(config)

    def fetch(
        self,
        symbol: str = "BTC/USDT",
        timeframe: str = "1h",
        limit: int = 500,
        since: Optional[datetime] = None,
    ) -> List[OHLCVBar]:
        since_ms = int(since.timestamp() * 1000) if since else None
        raw = self.exchange.fetch_ohlcv(
            symbol, timeframe=timeframe, since=since_ms, limit=limit
        )

        bars: List[OHLCVBar] = []
        for candle in raw:
            bars.append(OHLCVBar(
                timestamp=datetime.fromtimestamp(candle[0] / 1000),
                open=float(candle[1]),
                high=float(candle[2]),
                low=float(candle[3]),
                close=float(candle[4]),
                volume=float(candle[5]),
            ))

        return bars
