"""
Optimizer Service — grid-search backtest to find the best
(sensitivity, signal_mode) per timeframe.

Runs the reversal detection engine over historical OHLCV data for every
combination and simulates paper trades with SL/TP logic.  The best
combo per TF is used to create an inactive agent.
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import numpy as np
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..cache import cache_get, cache_set, cache_delete, get_redis_client

# Core engine imports
from reversal_pro.domain.enums import SignalMode, SensitivityPreset, CalculationMethod
from reversal_pro.domain.value_objects import SensitivityConfig, OHLCVBar as CoreOHLCVBar
from reversal_pro.application.use_cases.detect_reversals import DetectReversalsUseCase

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────
SENSITIVITIES = ["Very High", "High", "Medium", "Low", "Very Low"]
SIGNAL_MODES = ["Confirmed Only", "Confirmed + Preview"]
TIMEFRAMES = ["1m", "5m", "15m", "1h", "4h", "1d"]

# SL/TP params per TF (mirrors risk_manager.TF_PARAMS)
_TF_PARAMS = {
    1:    (1.5, 1.0, 0.30, 0.50),
    5:    (2.0, 1.2, 0.50, 0.80),
    15:   (2.5, 1.3, 0.80, 1.20),
    60:   (3.0, 1.5, 1.50, 2.00),
    240:  (3.0, 1.5, 3.00, 3.00),
    1440: (3.0, 1.5, 5.00, 5.00),
}


def _tf_to_minutes(tf: str) -> int:
    tf = tf.strip().lower()
    if tf.endswith("m"):
        return int(tf[:-1])
    elif tf.endswith("h"):
        return int(tf[:-1]) * 60
    elif tf.endswith("d"):
        return int(tf[:-1]) * 1440
    return 60


def _get_tf_params(timeframe: str) -> tuple:
    tf_min = _tf_to_minutes(timeframe)
    for minutes in sorted(_TF_PARAMS.keys()):
        if tf_min <= minutes:
            return _TF_PARAMS[minutes]
    return _TF_PARAMS[1440]


# ── Backtest data structures ─────────────────────────────────

@dataclass
class BacktestTrade:
    side: str            # LONG / SHORT
    entry_price: float
    sl: float
    tp: float
    exit_price: float = 0.0
    pnl_pct: float = 0.0
    is_winner: bool = False
    bars_held: int = 0


@dataclass
class BacktestResult:
    sensitivity: str
    signal_mode: str
    timeframe: str
    total_trades: int = 0
    winners: int = 0
    losers: int = 0
    win_rate: float = 0.0
    total_pnl_pct: float = 0.0
    avg_pnl_pct: float = 0.0
    profit_factor: float = 0.0
    max_drawdown_pct: float = 0.0
    score: float = 0.0


@dataclass
class OptimizationProgress:
    status: str = "idle"               # idle | running | done | error
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    current_tf: str = ""
    current_combo: int = 0
    total_combos: int = 0
    results: Dict[str, dict] = field(default_factory=dict)
    error: Optional[str] = None
    elapsed_seconds: float = 0.0


REDIS_PROGRESS_KEY = "optimizer:progress"


# ── Backtest engine ──────────────────────────────────────────

def _run_backtest(
    bars: List[CoreOHLCVBar],
    timeframe: str,
    sensitivity: str,
    signal_mode: str,
    trade_amount: float = 100.0,
) -> BacktestResult:
    """
    Run the reversal detection engine and simulate trades.

    For each signal detected:
    - Open a position (LONG or SHORT) at the signal's actual_price
    - SL from the opposite pivot; TP from R:R ratio
    - Walk forward through subsequent bars checking SL/TP hits
    """
    n = len(bars)
    if n < 50:
        return BacktestResult(
            sensitivity=sensitivity, signal_mode=signal_mode,
            timeframe=timeframe,
        )

    # Run analysis engine
    try:
        use_case = DetectReversalsUseCase(
            signal_mode=SignalMode(signal_mode),
            sensitivity=SensitivityPreset(sensitivity),
            calculation_method=CalculationMethod.AVERAGE,
            atr_length=5,
            average_length=5,
            confirmation_bars=0,
            generate_zones=False,
            timeframe=timeframe,
            use_matrix_profile=True,
        )
        result = use_case.execute(bars)
    except Exception as e:
        logger.warning(f"Backtest engine error ({sensitivity}/{signal_mode}/{timeframe}): {e}")
        return BacktestResult(
            sensitivity=sensitivity, signal_mode=signal_mode,
            timeframe=timeframe,
        )

    signals = result.signals
    if not signals:
        return BacktestResult(
            sensitivity=sensitivity, signal_mode=signal_mode,
            timeframe=timeframe,
        )

    # ATR values for SL calculation
    highs = np.array([b.high for b in bars], dtype=float)
    lows = np.array([b.low for b in bars], dtype=float)
    closes = np.array([b.close for b in bars], dtype=float)
    from reversal_pro.application.services.atr_service import ATRService
    atr_values = ATRService().atr(highs, lows, closes, 5)

    rr_ratio, atr_mult, max_sl_pct, fallback_sl_pct = _get_tf_params(timeframe)

    trades: List[BacktestTrade] = []

    for sig in signals:
        idx = sig.bar_index
        if idx >= n - 2:
            continue  # Need at least a couple bars after signal

        entry_price = sig.actual_price
        side = "LONG" if sig.is_bullish else "SHORT"
        atr = atr_values[idx] if idx < len(atr_values) and not np.isnan(atr_values[idx]) else None

        # SL from opposite pivot
        pivot_price = None
        for prev_sig in reversed(signals):
            if prev_sig.bar_index < idx and prev_sig.is_bullish != sig.is_bullish:
                pivot_price = prev_sig.actual_price
                break

        # Calculate SL/TP
        sl, tp, _ = _calculate_sl_tp(
            side, entry_price, pivot_price, atr, timeframe,
            rr_ratio, atr_mult, max_sl_pct, fallback_sl_pct,
        )

        # Walk forward
        exit_price = entry_price
        is_winner = False
        bars_held = 0

        for j in range(idx + 1, n):
            bars_held += 1
            candle_high = bars[j].high
            candle_low = bars[j].low

            if side == "LONG":
                if candle_low <= sl:
                    exit_price = sl
                    break
                if candle_high >= tp:
                    exit_price = tp
                    is_winner = True
                    break
            else:
                if candle_high >= sl:
                    exit_price = sl
                    break
                if candle_low <= tp:
                    exit_price = tp
                    is_winner = True
                    break
        else:
            # End of data without SL/TP hit — close at last bar close
            exit_price = bars[-1].close
            if side == "LONG":
                is_winner = exit_price > entry_price
            else:
                is_winner = exit_price < entry_price

        if side == "LONG":
            pnl_pct = ((exit_price - entry_price) / entry_price) * 100
        else:
            pnl_pct = ((entry_price - exit_price) / entry_price) * 100

        trades.append(BacktestTrade(
            side=side,
            entry_price=entry_price,
            sl=sl,
            tp=tp,
            exit_price=exit_price,
            pnl_pct=pnl_pct,
            is_winner=is_winner,
            bars_held=bars_held,
        ))

    if not trades:
        return BacktestResult(
            sensitivity=sensitivity, signal_mode=signal_mode,
            timeframe=timeframe,
        )

    # Compute statistics
    winners = sum(1 for t in trades if t.is_winner)
    losers = len(trades) - winners
    total_pnl = sum(t.pnl_pct for t in trades)
    avg_pnl = total_pnl / len(trades)
    win_rate = (winners / len(trades)) * 100

    gross_profit = sum(t.pnl_pct for t in trades if t.pnl_pct > 0)
    gross_loss = abs(sum(t.pnl_pct for t in trades if t.pnl_pct < 0))
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (
        10.0 if gross_profit > 0 else 0.0
    )

    # Max drawdown
    equity_curve = []
    equity = 0.0
    for t in trades:
        equity += t.pnl_pct
        equity_curve.append(equity)
    peak = 0.0
    max_dd = 0.0
    for e in equity_curve:
        if e > peak:
            peak = e
        dd = peak - e
        if dd > max_dd:
            max_dd = dd

    # Scoring: balanced metric
    # Formula: score = win_rate * profit_factor * sqrt(num_trades) * (1 - dd_penalty)
    trade_count_bonus = min(len(trades) ** 0.5, 10.0)  # cap at ~100 trades
    dd_penalty = min(max_dd / 20.0, 0.8)  # cap drawdown penalty at 80%
    score = win_rate * max(profit_factor, 0.01) * trade_count_bonus * (1 - dd_penalty)

    # Penalize very few trades
    if len(trades) < 3:
        score *= 0.3
    elif len(trades) < 5:
        score *= 0.6

    return BacktestResult(
        sensitivity=sensitivity,
        signal_mode=signal_mode,
        timeframe=timeframe,
        total_trades=len(trades),
        winners=winners,
        losers=losers,
        win_rate=round(win_rate, 1),
        total_pnl_pct=round(total_pnl, 2),
        avg_pnl_pct=round(avg_pnl, 3),
        profit_factor=round(profit_factor, 2),
        max_drawdown_pct=round(max_dd, 2),
        score=round(score, 1),
    )


def _calculate_sl_tp(
    side: str, entry_price: float,
    pivot_price: Optional[float], atr: Optional[float],
    timeframe: str, rr_ratio: float, atr_mult: float,
    max_sl_pct: float, fallback_sl_pct: float,
) -> Tuple[float, float, float]:
    """Pure-function SL/TP calculation (mirrors RiskManagerMixin._calculate_sl_tp)."""
    if side == "LONG":
        if pivot_price and pivot_price < entry_price:
            sl = pivot_price
        elif atr:
            sl = entry_price - (atr_mult * atr)
        else:
            sl = entry_price * (1 - fallback_sl_pct / 100)

        max_sl_dist = entry_price * (max_sl_pct / 100)
        if (entry_price - sl) > max_sl_dist:
            sl = entry_price - max_sl_dist

        risk = entry_price - sl
        tp = entry_price + (rr_ratio * risk)
        tp2 = entry_price + (1.5 * (tp - entry_price))
    else:
        if pivot_price and pivot_price > entry_price:
            sl = pivot_price
        elif atr:
            sl = entry_price + (atr_mult * atr)
        else:
            sl = entry_price * (1 + fallback_sl_pct / 100)

        max_sl_dist = entry_price * (max_sl_pct / 100)
        if (sl - entry_price) > max_sl_dist:
            sl = entry_price + max_sl_dist

        risk = sl - entry_price
        tp = entry_price - (rr_ratio * risk)
        tp2 = entry_price - (1.5 * (entry_price - tp))

    return round(sl, 2), round(tp, 2), round(tp2, 2)


# ── Async orchestrator ───────────────────────────────────────

class OptimizerService:
    """Grid-search optimizer that runs as a background task."""

    def __init__(self):
        self._running = False
        self._task: Optional[asyncio.Task] = None

    @property
    def is_running(self) -> bool:
        return self._running

    async def get_progress(self) -> OptimizationProgress:
        """Read current progress from Redis."""
        redis = get_redis_client()
        data = await cache_get(REDIS_PROGRESS_KEY)
        if data:
            return OptimizationProgress(**data)
        return OptimizationProgress()

    async def _save_progress(self, progress: OptimizationProgress):
        redis = get_redis_client()
        await redis.setex(
            REDIS_PROGRESS_KEY, 3600,
            __import__("json").dumps(asdict(progress)),
        )

    async def start(self, db_factory, symbol: str = "BTC/USDT"):
        """Launch the optimization in a background asyncio task."""
        if self._running:
            raise RuntimeError("Optimization already running")

        self._running = True
        self._task = asyncio.create_task(
            self._run(db_factory, symbol)
        )
        # Don't await — fire and forget
        self._task.add_done_callback(self._on_done)

    def _on_done(self, task: asyncio.Task):
        self._running = False
        if task.exception():
            logger.error(f"Optimizer crashed: {task.exception()}", exc_info=task.exception())

    async def _run(self, db_factory, symbol: str):
        """Main optimization loop."""
        t0 = time.perf_counter()
        total_combos = len(TIMEFRAMES) * len(SENSITIVITIES) * len(SIGNAL_MODES)
        progress = OptimizationProgress(
            status="running",
            started_at=datetime.now(timezone.utc).isoformat(),
            total_combos=total_combos,
        )
        await self._save_progress(progress)

        best_per_tf: Dict[str, BacktestResult] = {}
        combo_idx = 0

        try:
            for tf in TIMEFRAMES:
                progress.current_tf = tf
                await self._save_progress(progress)

                # Load OHLCV from DB
                async with db_factory() as db:
                    bars = await self._load_bars(db, symbol, tf)

                if len(bars) < 50:
                    logger.info(f"[OPTIMIZER] Skipping {tf}: only {len(bars)} bars")
                    combo_idx += len(SENSITIVITIES) * len(SIGNAL_MODES)
                    progress.current_combo = combo_idx
                    await self._save_progress(progress)
                    continue

                best_result: Optional[BacktestResult] = None

                for sensitivity in SENSITIVITIES:
                    for signal_mode in SIGNAL_MODES:
                        combo_idx += 1
                        progress.current_combo = combo_idx
                        progress.elapsed_seconds = round(time.perf_counter() - t0, 1)

                        # Run CPU-bound backtest in executor to not block event loop
                        loop = asyncio.get_event_loop()
                        result = await loop.run_in_executor(
                            None,
                            _run_backtest,
                            bars, tf, sensitivity, signal_mode,
                        )

                        logger.info(
                            f"[OPTIMIZER] {tf} {sensitivity}/{signal_mode}: "
                            f"{result.total_trades} trades, "
                            f"WR={result.win_rate}%, PF={result.profit_factor}, "
                            f"score={result.score}"
                        )

                        if best_result is None or result.score > best_result.score:
                            best_result = result

                        # Save progress periodically
                        if combo_idx % 3 == 0:
                            await self._save_progress(progress)

                if best_result and best_result.total_trades > 0:
                    best_per_tf[tf] = best_result
                    progress.results[tf] = asdict(best_result)

                await self._save_progress(progress)

            # Create agents from best results
            async with db_factory() as db:
                created_agents = await self._create_optimized_agents(
                    db, symbol, best_per_tf,
                )

            progress.status = "done"
            progress.finished_at = datetime.now(timezone.utc).isoformat()
            progress.elapsed_seconds = round(time.perf_counter() - t0, 1)
            progress.results["_created_agents"] = created_agents
            await self._save_progress(progress)

            logger.info(
                f"[OPTIMIZER] Done in {progress.elapsed_seconds}s — "
                f"created {len(created_agents)} agents"
            )

        except Exception as e:
            progress.status = "error"
            progress.error = str(e)
            progress.elapsed_seconds = round(time.perf_counter() - t0, 1)
            await self._save_progress(progress)
            logger.error(f"[OPTIMIZER] Failed: {e}", exc_info=True)
            raise

        finally:
            self._running = False

    async def _load_bars(
        self, db: AsyncSession, symbol: str, timeframe: str,
    ) -> List[CoreOHLCVBar]:
        """Load all available OHLCV bars from DB."""
        result = await db.execute(text("""
            SELECT time, open, high, low, close, volume
            FROM ohlcv
            WHERE symbol = :symbol AND timeframe = :timeframe
            ORDER BY time ASC
        """), {"symbol": symbol, "timeframe": timeframe})
        rows = result.fetchall()
        return [
            CoreOHLCVBar(
                timestamp=row[0].isoformat(),
                open=row[1], high=row[2], low=row[3],
                close=row[4], volume=row[5],
            )
            for row in rows
        ]

    async def _create_optimized_agents(
        self, db: AsyncSession, symbol: str,
        best_per_tf: Dict[str, BacktestResult],
    ) -> List[dict]:
        """Create one inactive agent per timeframe with optimized params."""
        created = []

        for tf, result in best_per_tf.items():
            # Check if an optimized agent already exists for this TF
            existing = await db.execute(text(
                "SELECT id, name FROM agents "
                "WHERE symbol = :symbol AND timeframe = :tf "
                "  AND name LIKE 'opti_%'"
            ), {"symbol": symbol, "tf": tf})
            existing_row = existing.fetchone()

            if existing_row:
                # Update existing optimized agent
                await db.execute(text(
                    "UPDATE agents SET "
                    "  sensitivity = :sensitivity, "
                    "  signal_mode = :signal_mode, "
                    "  is_active = FALSE, "
                    "  updated_at = NOW() "
                    "WHERE id = :id"
                ), {
                    "sensitivity": result.sensitivity,
                    "signal_mode": result.signal_mode,
                    "id": existing_row[0],
                })
                created.append({
                    "action": "updated",
                    "agent_id": existing_row[0],
                    "name": existing_row[1],
                    "timeframe": tf,
                    "sensitivity": result.sensitivity,
                    "signal_mode": result.signal_mode,
                    "score": result.score,
                    "win_rate": result.win_rate,
                    "profit_factor": result.profit_factor,
                    "total_trades": result.total_trades,
                })
                logger.info(
                    f"[OPTIMIZER] Updated agent {existing_row[1]} → "
                    f"{result.sensitivity} / {result.signal_mode} "
                    f"(score={result.score})"
                )
            else:
                # Find next available name
                count_result = await db.execute(text(
                    "SELECT COUNT(*) FROM agents WHERE name LIKE 'opti_%'"
                ))
                count = count_result.scalar() or 0
                agent_name = f"opti_{tf}_{count + 1}"

                await db.execute(text(
                    "INSERT INTO agents "
                    "  (name, symbol, timeframe, trade_amount, balance, "
                    "   is_active, mode, sensitivity, signal_mode, "
                    "   analysis_limit, created_at, updated_at) "
                    "VALUES "
                    "  (:name, :symbol, :tf, 100.0, 100.0, "
                    "   FALSE, 'paper', :sensitivity, :signal_mode, "
                    "   500, NOW(), NOW())"
                ), {
                    "name": agent_name,
                    "symbol": symbol,
                    "tf": tf,
                    "sensitivity": result.sensitivity,
                    "signal_mode": result.signal_mode,
                })
                created.append({
                    "action": "created",
                    "name": agent_name,
                    "timeframe": tf,
                    "sensitivity": result.sensitivity,
                    "signal_mode": result.signal_mode,
                    "score": result.score,
                    "win_rate": result.win_rate,
                    "profit_factor": result.profit_factor,
                    "total_trades": result.total_trades,
                })
                logger.info(
                    f"[OPTIMIZER] Created agent {agent_name}: "
                    f"{result.sensitivity} / {result.signal_mode} "
                    f"(score={result.score}, WR={result.win_rate}%, "
                    f"PF={result.profit_factor})"
                )

        await db.commit()
        return created


# Module-level singleton
optimizer_service = OptimizerService()
