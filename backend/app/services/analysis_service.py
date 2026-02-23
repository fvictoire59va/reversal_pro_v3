"""
Analysis service — runs the reversal detection engine and persists results.
Bridges the application layer (reversal_pro core) with the API.
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import List, Optional

import numpy as np
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.dialects.postgresql import insert as pg_insert

from ..models import Indicator, Signal, Zone, AnalysisRun
from ..schemas import (
    AnalysisRequest, AnalysisResponse, ChartDataResponse,
    OHLCVBar, IndicatorBar, SignalResponse, ZoneResponse,
    CandlestickData, LineData, MarkerData,
)
from ..cache import cache_get, cache_set, cache_delete

# Import core engine — reversal_pro is on PYTHONPATH via the Docker WORKDIR
# or project root when running locally.
from reversal_pro.domain.enums import SignalMode, SensitivityPreset, CalculationMethod
from reversal_pro.domain.value_objects import SensitivityConfig
from reversal_pro.domain.value_objects import OHLCVBar as CoreOHLCVBar
from reversal_pro.application.use_cases.detect_reversals import DetectReversalsUseCase

from ..services.telegram_service import telegram_service

logger = logging.getLogger(__name__)


# Cache TTL in seconds, adapted to candle timeframe
_CACHE_TTL = {"1m": 15, "5m": 45, "15m": 90, "30m": 120, "1h": 180, "4h": 300, "1d": 600}


def _ttl_for(timeframe: str) -> int:
    return _CACHE_TTL.get(timeframe, 120)


class AnalysisService:
    """Run the reversal detection engine and persist/format results."""

    async def get_ohlcv_from_db(
        self,
        db: AsyncSession,
        symbol: str,
        timeframe: str,
        limit: int = 500,
    ) -> List[dict]:
        """Load OHLCV bars from TimescaleDB."""
        cache_key = f"ohlcv:{symbol}:{timeframe}:{limit}"
        cached = await cache_get(cache_key)
        if cached:
            return cached

        query = text("""
            SELECT time, open, high, low, close, volume
            FROM (
                SELECT time, open, high, low, close, volume
                FROM ohlcv
                WHERE symbol = :symbol AND timeframe = :timeframe
                ORDER BY time DESC
                LIMIT :limit
            ) AS recent_bars
            ORDER BY time ASC
        """)

        result = await db.execute(query, {
            "symbol": symbol,
            "timeframe": timeframe,
            "limit": limit,
        })
        rows = result.fetchall()

        bars = [
            {
                "time": row[0].isoformat(),
                "open": row[1],
                "high": row[2],
                "low": row[3],
                "close": row[4],
                "volume": row[5],
            }
            for row in rows
        ]

        if bars:
            await cache_set(cache_key, bars, ttl=_ttl_for(timeframe))

        return bars

    async def run_analysis(
        self,
        db: AsyncSession,
        request: AnalysisRequest,
    ) -> AnalysisResponse:
        """Full analysis pipeline: load bars → detect → persist → return."""

        # 1. Load OHLCV from DB
        bars_data = await self.get_ohlcv_from_db(
            db, request.symbol, request.timeframe, request.limit
        )

        if not bars_data:
            raise ValueError(f"No OHLCV data found for {request.symbol} {request.timeframe}")

        # 2. Convert to core engine format
        core_bars = []
        for b in bars_data:
            core_bars.append(CoreOHLCVBar(
                timestamp=b["time"],
                open=b["open"],
                high=b["high"],
                low=b["low"],
                close=b["close"],
                volume=b["volume"],
            ))

        # 3. Build and run use case (with timeframe-adaptive ATR scaling)
        sensitivity = SensitivityPreset(request.sensitivity)
        custom_config = None
        if sensitivity == SensitivityPreset.CUSTOM:
            custom_config = SensitivityConfig.from_custom(2.0, 0.01)

        use_case = DetectReversalsUseCase(
            signal_mode=SignalMode(request.signal_mode),
            sensitivity=sensitivity,
            custom_config=custom_config,
            calculation_method=CalculationMethod(request.method),
            atr_length=request.atr_length,
            average_length=request.average_length,
            confirmation_bars=request.confirmation_bars,
            generate_zones=request.show_zones,
            timeframe=request.timeframe,
        )

        result = use_case.execute(core_bars)

        # 4. Persist indicators
        await self._persist_indicators(db, bars_data, result, request)

        # 5. Persist signals
        await self._persist_signals(db, bars_data, result, request)

        # 6. Persist zones
        await self._persist_zones(db, bars_data, result, request)

        # 7. Persist analysis run
        await self._persist_run(db, result, request, len(core_bars))

        # 8. Build response
        api_bars = [
            OHLCVBar(
                time=datetime.fromisoformat(b["time"]),
                open=b["open"], high=b["high"],
                low=b["low"], close=b["close"],
                volume=b["volume"],
            )
            for b in bars_data
        ]

        api_indicators = []
        for i, t in enumerate(result.trend_history):
            if i < len(bars_data):
                api_indicators.append(IndicatorBar(
                    time=datetime.fromisoformat(bars_data[i]["time"]),
                    ema_9=t.ema_fast if t.ema_fast else None,
                    ema_14=t.ema_mid if t.ema_mid else None,
                    ema_21=t.ema_slow if t.ema_slow else None,
                    trend=t.state.value,
                ))

        api_signals = [
            SignalResponse(
                time=datetime.fromisoformat(bars_data[min(s.bar_index, len(bars_data) - 1)]["time"]),
                bar_index=s.bar_index,
                price=s.price,
                actual_price=s.actual_price,
                is_bullish=s.is_bullish,
                is_preview=s.is_preview,
                label=s.label,
            )
            for s in result.signals
            if s.bar_index < len(bars_data)
        ]

        api_zones = [
            ZoneResponse(
                zone_type=z.zone_type.value,
                center_price=z.center_price,
                top_price=z.top_price,
                bottom_price=z.bottom_price,
                start_bar=z.start_bar,
                end_bar=z.end_bar,
            )
            for z in result.zones
        ]

        # Invalidate chart and ohlcv caches so get_chart_data re-builds
        await cache_delete(f"chart:{request.symbol}:{request.timeframe}*")
        await cache_delete(f"ohlcv:{request.symbol}:{request.timeframe}*")

        return AnalysisResponse(
            symbol=request.symbol,
            timeframe=request.timeframe,
            sensitivity=request.sensitivity,
            signal_mode=request.signal_mode,
            atr_multiplier=result.atr_multiplier,
            current_atr=result.current_atr,
            threshold=result.current_threshold,
            current_trend=result.current_trend.state.value if result.current_trend else None,
            bars=api_bars,
            indicators=api_indicators,
            signals=api_signals,
            zones=api_zones,
            total_signals=len(result.signals),
            total_zones=len(result.zones),
            bars_analyzed=len(core_bars),
            analyzed_at=datetime.now(timezone.utc),
        )

    async def get_chart_data(
        self,
        db: AsyncSession,
        symbol: str,
        timeframe: str,
        limit: int = 500,
        sensitivity: str = "Medium",
        signal_mode: str = "Confirmed Only",
    ) -> ChartDataResponse:
        """Get data formatted for TradingView lightweight-charts."""

        cache_key = f"chart:{symbol}:{timeframe}:{limit}:{sensitivity}:{signal_mode}"
        cached = await cache_get(cache_key)
        if cached:
            return ChartDataResponse(**cached)

        # Run full analysis
        request = AnalysisRequest(
            symbol=symbol,
            timeframe=timeframe,
            limit=limit,
            sensitivity=sensitivity,
            signal_mode=signal_mode,
            show_zones=True,
        )
        analysis = await self.run_analysis(db, request)

        # Convert to lightweight-charts format
        candles = []
        ema9_data = []
        ema14_data = []
        ema21_data = []

        for i, bar in enumerate(analysis.bars):
            ts = int(bar.time.timestamp())
            candles.append(CandlestickData(
                time=ts, open=bar.open, high=bar.high,
                low=bar.low, close=bar.close,
            ))
            if i < len(analysis.indicators):
                ind = analysis.indicators[i]
                if ind.ema_9 is not None and ind.ema_9 > 0:
                    ema9_data.append(LineData(time=ts, value=ind.ema_9))
                if ind.ema_14 is not None and ind.ema_14 > 0:
                    ema14_data.append(LineData(time=ts, value=ind.ema_14))
                if ind.ema_21 is not None and ind.ema_21 > 0:
                    ema21_data.append(LineData(time=ts, value=ind.ema_21))

        # Load persisted detected_at timestamps from DB
        sig_rows = await db.execute(text(
            "SELECT time, is_bullish, detected_at FROM signals "
            "WHERE symbol = :s AND timeframe = :tf"
        ), {"s": symbol, "tf": timeframe})
        detected_at_map = {}
        for row in sig_rows.fetchall():
            sig_ts = int(row[0].timestamp())
            detected_at_map[(sig_ts, row[1])] = row[2]

        # Markers for reversal signals
        markers = []
        # Compute candle interval in seconds for delay calculation
        candle_interval = 60  # default 1m
        if len(analysis.bars) >= 2:
            candle_interval = int(
                analysis.bars[1].time.timestamp() - analysis.bars[0].time.timestamp()
            )
            if candle_interval <= 0:
                candle_interval = 60

        for sig in analysis.signals:
            if sig.bar_index < len(analysis.bars):
                ts = int(analysis.bars[sig.bar_index].time.timestamp())
                db_detected = detected_at_map.get((ts, sig.is_bullish))
                detected_at_str = db_detected.isoformat() if db_detected else datetime.now(timezone.utc).isoformat()

                # candles_delay = number of candles between signal bar and detection time
                if db_detected:
                    delay_seconds = int(db_detected.timestamp()) - ts
                    candles_delay = max(0, delay_seconds // candle_interval)
                else:
                    candles_delay = 0

                markers.append(MarkerData(
                    time=ts,
                    position="belowBar" if sig.is_bullish else "aboveBar",
                    color="#00FF00" if sig.is_bullish else "#FF0000",
                    shape="arrowUp" if sig.is_bullish else "arrowDown",
                    text=f"{'▲' if sig.is_bullish else '▼'} {sig.label} {sig.price:,.2f}",
                    size=2 if not sig.is_preview else 1,
                    detected_at=detected_at_str,
                    candles_delay=candles_delay,
                ))

        # Sort markers by time (required by lightweight-charts)
        markers.sort(key=lambda m: m.time)

        chart_data = ChartDataResponse(
            symbol=symbol,
            timeframe=timeframe,
            candles=candles,
            ema_9=ema9_data,
            ema_14=ema14_data,
            ema_21=ema21_data,
            markers=markers,
            zones=analysis.zones,
            current_trend=analysis.current_trend,
            current_atr=analysis.current_atr,
            threshold=analysis.threshold,
            atr_multiplier=analysis.atr_multiplier,
        )

        await cache_set(cache_key, chart_data.model_dump(), ttl=_ttl_for(timeframe))

        return chart_data

    # ── Persistence helpers ──────────────────────────────────────
    async def _persist_indicators(self, db, bars_data, result, request):
        """Store computed indicators."""
        if not result.trend_history:
            return

        values = []
        for i, trend in enumerate(result.trend_history):
            if i >= len(bars_data):
                break
            values.append({
                "time": datetime.fromisoformat(bars_data[i]["time"]),
                "symbol": request.symbol,
                "timeframe": request.timeframe,
                "ema_9": trend.ema_fast if trend.ema_fast else None,
                "ema_14": trend.ema_mid if trend.ema_mid else None,
                "ema_21": trend.ema_slow if trend.ema_slow else None,
                "trend": trend.state.value,
            })

        if values:
            stmt = pg_insert(Indicator).values(values)
            stmt = stmt.on_conflict_do_update(
                constraint="indicators_pkey",
                set_={
                    "ema_9": stmt.excluded.ema_9,
                    "ema_14": stmt.excluded.ema_14,
                    "ema_21": stmt.excluded.ema_21,
                    "trend": stmt.excluded.trend,
                },
            )
            await db.execute(stmt)
            await db.commit()

    async def _persist_signals(self, db, bars_data, result, request):
        """Store detected signals using UPSERT to avoid race conditions.

        Instead of DELETE + re-INSERT (which creates a window where agents
        see zero signals), we:
          1. Build the new set of signal values.
          2. UPSERT via ON CONFLICT on the unique index
             (time, symbol, timeframe, is_bullish).
          3. DELETE stale signals that are no longer in the analysis result.

        This guarantees that readers always see a consistent set of signals.
        """
        now = datetime.now(timezone.utc)

        # Compute candle interval and recent cutoff
        last_bar_time = datetime.fromisoformat(bars_data[-1]["time"])
        if len(bars_data) >= 2:
            candle_seconds = (
                datetime.fromisoformat(bars_data[-1]["time"])
                - datetime.fromisoformat(bars_data[-2]["time"])
            ).total_seconds()
        else:
            candle_seconds = 60

        # Ghost-signal cutoff: only applies on FIRST-EVER analysis for this
        # symbol/timeframe (no existing signals in DB).  On subsequent runs,
        # any NEW signal is genuinely new (the scheduler was running) →
        # detected_at = now so agents can act on it immediately.
        # On first analysis the cutoff is generous (100 candles) to cover
        # startup on historical data without marking recent signals as ghosts.
        recent_cutoff = last_bar_time - timedelta(seconds=candle_seconds * 100)

        # 1. Load existing signals (id + detected_at) for this symbol/timeframe
        #    Single SELECT serves both the detected_at lookup and the stale-delete step.
        existing = await db.execute(text(
            "SELECT id, time, is_bullish, detected_at FROM signals "
            "WHERE symbol = :s AND timeframe = :tf"
        ), {"s": request.symbol, "tf": request.timeframe})
        existing_rows = existing.fetchall()
        existing_map = {}   # (time_naive, is_bullish) → detected_at
        existing_id_map = {} # (time_naive, is_bullish) → id
        for row in existing_rows:
            key = (row[1].replace(tzinfo=None) if row[1].tzinfo else row[1], row[2])
            existing_map[key] = row[3]
            existing_id_map[key] = row[0]

        # If this symbol/TF already has signals in DB, the system was running
        # → any brand-new signal is genuinely fresh.
        first_analysis = len(existing_rows) == 0

        # 2. Build upsert values list and track which signal keys we keep
        upsert_values = []
        kept_keys = set()  # (sig_time, is_bullish) for delete-stale step

        for sig in result.signals:
            if sig.bar_index >= len(bars_data):
                continue
            sig_time = datetime.fromisoformat(bars_data[sig.bar_index]["time"])
            sig_time_naive = sig_time.replace(tzinfo=None) if sig_time.tzinfo else sig_time

            key = (sig_time_naive, sig.is_bullish)
            kept_keys.add(key)

            # Reuse original detected_at if the signal was already known
            original_detected = existing_map.get(key)

            if not original_detected:
                if first_analysis:
                    # First-ever analysis: backdate old signals to prevent
                    # agents from acting on ancient historical reversals.
                    cutoff_naive = recent_cutoff.replace(tzinfo=None) if recent_cutoff.tzinfo else recent_cutoff
                    if sig_time_naive >= cutoff_naive:
                        detected_at = now
                    else:
                        detected_at = sig_time
                        logger.debug(
                            f"Ghost signal (first analysis): {sig_time} "
                            f"{'LONG' if sig.is_bullish else 'SHORT'} "
                            f"(older than cutoff {cutoff_naive})"
                        )
                else:
                    # Ongoing operations: system was running, this signal is
                    # genuinely new → detected_at = now so agents can act.
                    detected_at = now
                    logger.info(
                        f"New signal detected: {sig_time} "
                        f"{'LONG' if sig.is_bullish else 'SHORT'} "
                        f"price={sig.price:.2f} detected_at={now}"
                    )
            else:
                detected_at = original_detected

            upsert_values.append({
                "time": sig_time,
                "symbol": request.symbol,
                "timeframe": request.timeframe,
                "bar_index": sig.bar_index,
                "price": sig.price,
                "actual_price": sig.actual_price,
                "is_bullish": sig.is_bullish,
                "is_preview": sig.is_preview,
                "signal_label": sig.label,
                "detected_at": detected_at,
            })

        # 3. Deduplicate by constraint key (time, symbol, timeframe, is_bullish)
        #    In "confirmed + preview" mode the same candle may appear twice
        #    (once confirmed, once preview). Keep confirmed over preview.
        deduped: dict[tuple, dict] = {}
        for val in upsert_values:
            key = (val["time"], val["symbol"], val["timeframe"], val["is_bullish"])
            existing_val = deduped.get(key)
            if existing_val is None:
                deduped[key] = val
            elif existing_val["is_preview"] and not val["is_preview"]:
                # confirmed wins over preview
                deduped[key] = val
            # else: keep existing (confirmed already in place, or both preview → keep first)
        upsert_values = list(deduped.values())

        # 4. Upsert signals in batch
        if upsert_values:
            stmt = pg_insert(Signal).values(upsert_values)
            stmt = stmt.on_conflict_do_update(
                index_elements=["time", "symbol", "timeframe", "is_bullish"],
                set_={
                    "bar_index": stmt.excluded.bar_index,
                    "price": stmt.excluded.price,
                    "actual_price": stmt.excluded.actual_price,
                    "is_preview": stmt.excluded.is_preview,
                    "signal_label": stmt.excluded.signal_label,
                    # detected_at is preserved — use COALESCE to keep existing value
                    "detected_at": text(
                        "COALESCE(signals.detected_at, EXCLUDED.detected_at)"
                    ),
                },
            )
            await db.execute(stmt)

        # 5. Delete stale signals no longer in the analysis result
        #    Reuse existing_id_map from step 1 (no extra SELECT)
        if result.signals:
            ids_to_delete = [
                sid for key, sid in existing_id_map.items()
                if key not in kept_keys
            ]
            if ids_to_delete:
                await db.execute(text(
                    "DELETE FROM signals WHERE id = ANY(:ids)"
                ), {"ids": ids_to_delete})
        else:
            # No signals in result — remove all for this pair
            await db.execute(text(
                "DELETE FROM signals WHERE symbol = :s AND timeframe = :tf"
            ), {"s": request.symbol, "tf": request.timeframe})

        await db.commit()

    async def _persist_zones(self, db, bars_data, result, request):
        """Store supply/demand zones."""
        if not result.zones:
            return

        await db.execute(text(
            "DELETE FROM zones WHERE symbol = :s AND timeframe = :tf"
        ), {"s": request.symbol, "tf": request.timeframe})

        for zone in result.zones:
            start_idx = min(zone.start_bar, len(bars_data) - 1)
            z = Zone(
                time=datetime.fromisoformat(bars_data[start_idx]["time"]),
                symbol=request.symbol,
                timeframe=request.timeframe,
                zone_type=zone.zone_type.value,
                center_price=zone.center_price,
                top_price=zone.top_price,
                bottom_price=zone.bottom_price,
                start_bar=zone.start_bar,
                end_bar=zone.end_bar,
            )
            db.add(z)

        await db.commit()

    async def _persist_run(self, db, result, request, bars_count):
        """Store analysis run metadata."""
        run = AnalysisRun(
            symbol=request.symbol,
            timeframe=request.timeframe,
            sensitivity=request.sensitivity,
            signal_mode=request.signal_mode,
            atr_multiplier=result.atr_multiplier,
            current_atr=result.current_atr,
            threshold=result.current_threshold,
            current_trend=result.current_trend.state.value if result.current_trend else None,
            total_signals=len(result.signals),
            total_zones=len(result.zones),
            bars_analyzed=bars_count,
        )
        db.add(run)
        await db.commit()


# Backward-compatible singleton — delegates to centralized dependencies
def __getattr__(name):
    if name == "analysis_service":
        from ..dependencies import get_analysis_service
        return get_analysis_service()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
