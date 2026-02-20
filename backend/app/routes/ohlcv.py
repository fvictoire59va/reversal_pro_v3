"""OHLCV data endpoints."""

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..schemas import OHLCVResponse, OHLCVBar
from ..services.data_ingestion import ingestion_service

import tempfile, os
from datetime import datetime

router = APIRouter(prefix="/ohlcv", tags=["OHLCV Data"])


@router.get("/{symbol}/{timeframe}", response_model=OHLCVResponse)
async def get_ohlcv(
    symbol: str,
    timeframe: str = "1h",
    limit: int = Query(default=500, ge=10, le=5000),
    db: AsyncSession = Depends(get_db),
):
    """Get OHLCV bars from database."""
    # Convert URL format (BTC-USDT) back to standard format (BTC/USDT)
    symbol = symbol.replace('-', '/')
    
    from ..services.analysis_service import analysis_service
    bars_data = await analysis_service.get_ohlcv_from_db(db, symbol, timeframe, limit)

    if not bars_data:
        raise HTTPException(status_code=404, detail=f"No data for {symbol} {timeframe}")

    bars = [
        OHLCVBar(
            time=datetime.fromisoformat(b["time"]),
            open=b["open"], high=b["high"],
            low=b["low"], close=b["close"],
            volume=b["volume"],
        )
        for b in bars_data
    ]

    return OHLCVResponse(
        symbol=symbol,
        timeframe=timeframe,
        bars=bars,
        count=len(bars),
    )


@router.post("/fetch/{symbol}/{timeframe}")
async def fetch_ohlcv(
    symbol: str,
    timeframe: str = "1h",
    exchange: str = Query(default="binance"),
    limit: int = Query(default=500, ge=10, le=5000),
    db: AsyncSession = Depends(get_db),
):
    """Fetch OHLCV from exchange and store in database."""
    # Convert URL format (BTC-USDT) back to exchange format (BTC/USDT)
    symbol = symbol.replace('-', '/')
    
    try:
        count = await ingestion_service.fetch_and_store(
            db, symbol=symbol, timeframe=timeframe,
            exchange_id=exchange, limit=limit,
        )
        return {"status": "ok", "symbol": symbol, "timeframe": timeframe, "bars_stored": count}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/upload")
async def upload_csv(
    file: UploadFile = File(...),
    symbol: str = Query(default="BTC/USDT"),
    timeframe: str = Query(default="1h"),
    db: AsyncSession = Depends(get_db),
):
    """Upload CSV file with OHLCV data."""
    if not file.filename.endswith(".csv"):
        raise HTTPException(status_code=400, detail="Only CSV files accepted")

    # Save to temp file
    content = await file.read()
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".csv")
    tmp.write(content)
    tmp.close()

    try:
        count = await ingestion_service.load_from_csv(
            db, file_path=tmp.name, symbol=symbol, timeframe=timeframe,
        )
        return {"status": "ok", "symbol": symbol, "bars_stored": count}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        os.unlink(tmp.name)


@router.post("/fetch-watchlist")
async def fetch_watchlist(db: AsyncSession = Depends(get_db)):
    """Fetch data for all active watchlist symbols."""
    try:
        report = await ingestion_service.fetch_all_watchlist(db)
        return {"status": "ok", "report": report}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
