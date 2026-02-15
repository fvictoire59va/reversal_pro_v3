"""ATR (Average True Range) calculation service."""

import numpy as np
from typing import List


class ATRService:
    """Calculates Average True Range for volatility-based thresholds."""

    @staticmethod
    def true_range(
        highs: np.ndarray, lows: np.ndarray, closes: np.ndarray
    ) -> np.ndarray:
        """
        Compute True Range for each bar.
        TR = max(high - low, |high - prev_close|, |low - prev_close|)
        """
        n = len(highs)
        tr = np.zeros(n)
        tr[0] = highs[0] - lows[0]

        for i in range(1, n):
            hl = highs[i] - lows[i]
            hpc = abs(highs[i] - closes[i - 1])
            lpc = abs(lows[i] - closes[i - 1])
            tr[i] = max(hl, hpc, lpc)

        return tr

    @staticmethod
    def atr(
        highs: np.ndarray,
        lows: np.ndarray,
        closes: np.ndarray,
        period: int = 5,
    ) -> np.ndarray:
        """
        Compute ATR using a simple moving average of True Range.
        Returns an array of ATR values (NaN for the first `period - 1` bars).
        """
        tr = ATRService.true_range(highs, lows, closes)
        n = len(tr)
        atr_values = np.full(n, np.nan)

        if n < period:
            return atr_values

        # Initial SMA
        atr_values[period - 1] = np.mean(tr[:period])

        # Wilder's smoothing (RMA)
        for i in range(period, n):
            atr_values[i] = (
                atr_values[i - 1] * (period - 1) + tr[i]
            ) / period

        return atr_values

    @staticmethod
    def compute_reversal_threshold(
        close: float,
        percent_threshold: float,
        absolute_reversal: float,
        atr_multiplier: float,
        atr_value: float,
    ) -> float:
        """
        Compute the final reversal threshold.
        reversalAmount = max(close * pct, max(absRev, atrMult * atr))

        Note: percent_threshold is already a fraction (e.g. 0.01 = 1%),
        so we do NOT divide by 100 again.
        """
        pct_amount = close * percent_threshold
        atr_amount = atr_multiplier * atr_value
        return max(pct_amount, max(absolute_reversal, atr_amount))
