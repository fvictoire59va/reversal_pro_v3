"""
Data ingestion service — fetches OHLCV from exchanges and persists to TimescaleDB.
"""

import logging
from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import text, delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.dialects.postgresql import insert as pg_insert

from ..models import OHLCV, Watchlist
from ..cache import cache_delete

logger = logging.getLogger(__name__)


class DataIngestionService:
    """Fetch OHLCV data from ccxt exchanges and store in TimescaleDB."""

    def __init__(self):
        self._exchanges = {}

    def _get_exchange(self, exchange_id: str):
        """Lazy-load async ccxt exchange instance."""
        if exchange_id not in self._exchanges:
            try:
                import ccxt.async_support as ccxt_async
                exchange_class = getattr(ccxt_async, exchange_id, None)
                if exchange_class is None:
                    raise ValueError(f"Exchange '{exchange_id}' not found")
                self._exchanges[exchange_id] = exchange_class({"enableRateLimit": True})
            except ImportError:
                raise ImportError("ccxt is required: pip install ccxt")
        return self._exchanges[exchange_id]

    async def close_exchanges(self):
        """Close all async exchange sessions."""
        for exchange in self._exchanges.values():
            try:
                await exchange.close()
            except Exception:
                pass
        self._exchanges.clear()

    async def fetch_and_store(
        self,
        db: AsyncSession,
        symbol: str = "BTC/USDT",
        timeframe: str = "1h",
        exchange_id: str = "binance",
        limit: int = 500,
        since: Optional[datetime] = None,
    ) -> int:
        """
        Fetch OHLCV from exchange and upsert into TimescaleDB.
        Returns number of bars stored.
        """
        exchange = self._get_exchange(exchange_id)
        since_ms = int(since.timestamp() * 1000) if since else None

        logger.info(f"Fetching {limit} bars for {symbol} {timeframe} from {exchange_id}")

        try:
            raw = await exchange.fetch_ohlcv(symbol, timeframe=timeframe, since=since_ms, limit=limit)
        except Exception as e:
            logger.error(f"Error fetching from {exchange_id}: {e}")
            raise

        if not raw:
            return 0

        # Build upsert values
        values = []
        for candle in raw:
            values.append({
                "time": datetime.fromtimestamp(candle[0] / 1000, tz=timezone.utc),
                "symbol": symbol,
                "timeframe": timeframe,
                "open": float(candle[1]),
                "high": float(candle[2]),
                "low": float(candle[3]),
                "close": float(candle[4]),
                "volume": float(candle[5]) if candle[5] else 0.0,
            })

        # PostgreSQL upsert (ON CONFLICT DO UPDATE)
        stmt = pg_insert(OHLCV).values(values)
        stmt = stmt.on_conflict_do_update(
            constraint="ohlcv_pkey",
            set_={
                "open": stmt.excluded.open,
                "high": stmt.excluded.high,
                "low": stmt.excluded.low,
                "close": stmt.excluded.close,
                "volume": stmt.excluded.volume,
            },
        )

        await db.execute(stmt)
        await db.commit()

        # Invalidate cache
        await cache_delete(f"ohlcv:{symbol}:{timeframe}*")
        await cache_delete(f"chart:{symbol}:{timeframe}*")

        logger.info(f"Stored {len(values)} bars for {symbol} {timeframe}")
        return len(values)

    async def fetch_all_watchlist(self, db: AsyncSession) -> dict:
        """Fetch data for all active watchlist symbols."""
        result = await db.execute(
            text("SELECT symbol, timeframe, exchange FROM watchlist WHERE is_active = TRUE")
        )
        rows = result.fetchall()
        report = {}

        for row in rows:
            symbol, timeframe, exchange = row
            try:
                count = await self.fetch_and_store(
                    db, symbol=symbol, timeframe=timeframe,
                    exchange_id=exchange, limit=500,
                )
                report[f"{symbol}_{timeframe}"] = {"status": "ok", "bars": count}
            except Exception as e:
                report[f"{symbol}_{timeframe}"] = {"status": "error", "error": str(e)}
                logger.error(f"Error fetching {symbol} {timeframe}: {e}")

        return report

    async def load_from_csv(
        self,
        db: AsyncSession,
        file_path: str,
        symbol: str = "BTC/USDT",
        timeframe: str = "1h",
    ) -> int:
        """Load OHLCV from a CSV file into the database."""
        import csv
        from pathlib import Path

        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"CSV file not found: {file_path}")

        values = []
        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    ts = row.get("timestamp") or row.get("date") or row.get("time")
                    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)

                    values.append({
                        "time": dt,
                        "symbol": symbol,
                        "timeframe": timeframe,
                        "open": float(row["open"]),
                        "high": float(row["high"]),
                        "low": float(row["low"]),
                        "close": float(row["close"]),
                        "volume": float(row.get("volume", 0)),
                    })
                except (ValueError, KeyError) as e:
                    logger.warning(f"Skipping row: {e}")
                    continue

        if not values:
            return 0

        stmt = pg_insert(OHLCV).values(values)
        stmt = stmt.on_conflict_do_update(
            constraint="ohlcv_pkey",
            set_={
                "open": stmt.excluded.open,
                "high": stmt.excluded.high,
                "low": stmt.excluded.low,
                "close": stmt.excluded.close,
                "volume": stmt.excluded.volume,
            },
        )

        await db.execute(stmt)
        await db.commit()

        await cache_delete(f"ohlcv:{symbol}:{timeframe}*")
        logger.info(f"Loaded {len(values)} bars from CSV for {symbol} {timeframe}")
        return len(values)


# Backward-compatible singleton — delegates to centralized dependencies
def __getattr__(name):
    if name == "ingestion_service":
        from ..dependencies import get_ingestion_service
        return get_ingestion_service()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
