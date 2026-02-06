"""Watchlist endpoints."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.dialects.postgresql import insert as pg_insert

from ..database import get_db
from ..schemas import WatchlistItem, WatchlistResponse
from ..models import Watchlist

router = APIRouter(prefix="/api/watchlist", tags=["Watchlist"])


@router.get("/", response_model=WatchlistResponse)
async def get_watchlist(db: AsyncSession = Depends(get_db)):
    """Get all watchlist items."""
    result = await db.execute(
        text("SELECT symbol, timeframe, exchange, is_active FROM watchlist ORDER BY symbol")
    )
    rows = result.fetchall()
    items = [
        WatchlistItem(symbol=r[0], timeframe=r[1], exchange=r[2], is_active=r[3])
        for r in rows
    ]
    return WatchlistResponse(items=items)


@router.post("/", response_model=WatchlistItem)
async def add_to_watchlist(
    item: WatchlistItem,
    db: AsyncSession = Depends(get_db),
):
    """Add a symbol to the watchlist."""
    stmt = pg_insert(Watchlist).values(
        symbol=item.symbol,
        timeframe=item.timeframe,
        exchange=item.exchange,
        is_active=item.is_active,
    )
    stmt = stmt.on_conflict_do_update(
        constraint="watchlist_pkey",
        set_={"exchange": item.exchange, "is_active": item.is_active},
    )
    await db.execute(stmt)
    await db.commit()
    return item


@router.delete("/{symbol}/{timeframe}")
async def remove_from_watchlist(
    symbol: str,
    timeframe: str = "1h",
    db: AsyncSession = Depends(get_db),
):
    """Remove a symbol from the watchlist."""
    # Convert URL format (BTC-USDT) back to standard format (BTC/USDT)
    symbol = symbol.replace('-', '/')
    
    await db.execute(text(
        "DELETE FROM watchlist WHERE symbol = :s AND timeframe = :tf"
    ), {"s": symbol, "tf": timeframe})
    await db.commit()
    return {"status": "deleted", "symbol": symbol, "timeframe": timeframe}
