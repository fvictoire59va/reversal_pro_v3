"""Analysis & chart data endpoints."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..schemas import (
    AnalysisRequest, AnalysisResponse, ChartDataResponse,
)
from ..services.analysis_service import analysis_service

router = APIRouter(prefix="/analysis", tags=["Analysis"])


@router.post("/run", response_model=AnalysisResponse)
async def run_analysis(
    request: AnalysisRequest,
    db: AsyncSession = Depends(get_db),
):
    """Run the reversal detection analysis."""
    try:
        result = await analysis_service.run_analysis(db, request)
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/chart/{symbol}/{timeframe}", response_model=ChartDataResponse)
async def get_chart_data(
    symbol: str,
    timeframe: str = "1h",
    limit: int = Query(default=500, ge=50, le=5000),
    sensitivity: str = Query(default="Medium"),
    signal_mode: str = Query(default="Confirmed Only"),
    confirmation_bars: int = Query(default=0, ge=0, le=5),
    method: str = Query(default="average"),
    atr_length: int = Query(default=5, ge=1, le=50),
    average_length: int = Query(default=5, ge=1, le=50),
    absolute_reversal: float = Query(default=0.5, ge=0.0, le=10.0),
    db: AsyncSession = Depends(get_db),
):
    """Get chart data formatted for TradingView lightweight-charts."""
    # Convert URL format (BTC-USDT) back to standard format (BTC/USDT)
    symbol = symbol.replace('-', '/')
    
    try:
        data = await analysis_service.get_chart_data(
            db, symbol=symbol, timeframe=timeframe,
            limit=limit, sensitivity=sensitivity,
            signal_mode=signal_mode,
            confirmation_bars=confirmation_bars,
            method=method,
            atr_length=atr_length,
            average_length=average_length,
            absolute_reversal=absolute_reversal,
        )
        return data
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/signals/{symbol}/{timeframe}")
async def get_signals(
    symbol: str,
    timeframe: str = "1h",
    limit: int = Query(default=50, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
):
    """Get latest reversal signals."""
    # Convert URL format (BTC-USDT) back to standard format (BTC/USDT)
    symbol = symbol.replace('-', '/')
    
    from sqlalchemy import text
    result = await db.execute(text("""
        SELECT time, bar_index, price, actual_price, is_bullish, is_preview, signal_label
        FROM signals
        WHERE symbol = :symbol AND timeframe = :timeframe
        ORDER BY time DESC
        LIMIT :limit
    """), {"symbol": symbol, "timeframe": timeframe, "limit": limit})

    rows = result.fetchall()
    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "signals": [
            {
                "time": row[0].isoformat(),
                "bar_index": row[1],
                "price": row[2],
                "actual_price": row[3],
                "is_bullish": row[4],
                "is_preview": row[5],
                "label": row[6],
            }
            for row in rows
        ],
    }


@router.get("/zones/{symbol}/{timeframe}")
async def get_zones(
    symbol: str,
    timeframe: str = "1h",
    db: AsyncSession = Depends(get_db),
):
    """Get supply/demand zones."""
    # Convert URL format (BTC-USDT) back to standard format (BTC/USDT)
    symbol = symbol.replace('-', '/')
    
    from sqlalchemy import text
    result = await db.execute(text("""
        SELECT zone_type, center_price, top_price, bottom_price, start_bar, end_bar
        FROM zones
        WHERE symbol = :symbol AND timeframe = :timeframe
        ORDER BY created_at DESC
        LIMIT 20
    """), {"symbol": symbol, "timeframe": timeframe})

    rows = result.fetchall()
    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "zones": [
            {
                "zone_type": row[0],
                "center_price": row[1],
                "top_price": row[2],
                "bottom_price": row[3],
                "start_bar": row[4],
                "end_bar": row[5],
            }
            for row in rows
        ],
    }
