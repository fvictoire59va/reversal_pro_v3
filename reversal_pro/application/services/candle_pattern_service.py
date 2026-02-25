"""
Candlestick Pattern Recognition Service.

Detects classic reversal candlestick patterns (engulfing, hammer,
shooting star, doji) and produces a per-bar threshold reduction factor.

When a reversal pattern forms right after a significant swing, the
pivot can be confirmed 1–3 candles earlier because the pattern itself
is strong evidence of a turning point.

References
----------
- TA-Lib candlestick: https://github.com/TA-Lib/ta-lib-python
- candlestick-patterns: https://github.com/SpiralDevelopment/candlestick-patterns
"""

from __future__ import annotations

import numpy as np


class CandlePatternService:
    """Detect key reversal candlestick patterns to accelerate pivot confirmation."""

    def __init__(
        self,
        body_ratio_threshold: float = 0.30,
        engulfing_reduction: float = 0.50,
        hammer_reduction: float = 0.65,
        doji_reduction: float = 0.80,
    ):
        """
        Parameters
        ----------
        body_ratio_threshold : float
            Maximum body/range ratio to classify as small-body candle.
        engulfing_reduction : float
            Reduction factor when an engulfing pattern is detected.
        hammer_reduction : float
            Reduction factor for hammer / shooting star.
        doji_reduction : float
            Reduction factor for doji candles.
        """
        self.body_ratio_threshold = body_ratio_threshold
        self.engulfing_reduction = engulfing_reduction
        self.hammer_reduction = hammer_reduction
        self.doji_reduction = doji_reduction

    def compute_reduction(
        self,
        opens: np.ndarray,
        highs: np.ndarray,
        lows: np.ndarray,
        closes: np.ndarray,
    ) -> np.ndarray:
        """
        Return a per-bar multiplier in [reduction, 1.0].

        Parameters
        ----------
        opens, highs, lows, closes : 1-D arrays of bar OHLC prices.

        Returns
        -------
        np.ndarray of shape (n,) — multiply onto reversal_amounts.
        """
        n = len(closes)
        reduction = np.ones(n, dtype=float)

        for i in range(1, n):
            body = abs(closes[i] - opens[i])
            full_range = highs[i] - lows[i]
            if full_range < 1e-10:
                continue

            ratio = body / full_range
            prev_body_signed = closes[i - 1] - opens[i - 1]

            # ── Bullish Engulfing ────────────────────────────────────
            # Previous candle was bearish, current bullish body engulfs it
            if (prev_body_signed < 0
                    and closes[i] > opens[i]
                    and closes[i] > opens[i - 1]
                    and opens[i] < closes[i - 1]):
                reduction[i] = min(reduction[i], self.engulfing_reduction)
                continue  # strongest pattern — skip weaker checks

            # ── Bearish Engulfing ────────────────────────────────────
            if (prev_body_signed > 0
                    and closes[i] < opens[i]
                    and closes[i] < opens[i - 1]
                    and opens[i] > closes[i - 1]):
                reduction[i] = min(reduction[i], self.engulfing_reduction)
                continue

            # ── Hammer (bullish) ─────────────────────────────────────
            # Small body at the top of the range, long lower shadow
            lower_shadow = min(opens[i], closes[i]) - lows[i]
            upper_shadow = highs[i] - max(opens[i], closes[i])
            if (ratio < self.body_ratio_threshold
                    and lower_shadow > 2.0 * body
                    and upper_shadow < body
                    and closes[i] >= opens[i]):
                reduction[i] = min(reduction[i], self.hammer_reduction)
                continue

            # ── Shooting Star (bearish) ──────────────────────────────
            # Small body at the bottom of the range, long upper shadow
            if (ratio < self.body_ratio_threshold
                    and upper_shadow > 2.0 * body
                    and lower_shadow < body
                    and closes[i] <= opens[i]):
                reduction[i] = min(reduction[i], self.hammer_reduction)
                continue

            # ── Doji ─────────────────────────────────────────────────
            # Very small body relative to range → indecision
            if ratio < 0.10:
                reduction[i] = min(reduction[i], self.doji_reduction)

        return reduction
