"""
CUSUM (Cumulative Sum) Change-Point Detection Service.

Uses a two-sided CUSUM algorithm to detect structural shifts in the
price series *before* the ZigZag confirms them.  The CUSUM accumulates
small deviations from the local mean and fires as soon as the
cumulative sum exceeds a threshold — often 3–8 candles earlier than
a traditional ATR-based reversal detection.

The service produces a per-bar threshold reduction factor that can be
multiplied onto the ZigZag's reversal_amounts, exactly like the
Matrix Profile service.

References
----------
- ruptures (change-point detection): https://github.com/deepcharles/ruptures
- detecta (CUSUM implementation): https://github.com/demotu/detecta
- Page, E. S. (1954). Continuous inspection schemes.
"""

from __future__ import annotations

import numpy as np


class CUSUMService:
    """
    Two-sided CUSUM for early trend-shift detection.

    The algorithm maintains two accumulators (upward / downward) and
    resets each time the accumulated deviation exceeds `threshold × ATR`.
    At those moments and for `decay_bars` after, the reversal threshold
    is reduced so the ZigZag can confirm a pivot faster.

    Parameters
    ----------
    drift_fraction : float
        Allowance (drift) expressed as a fraction of ATR.
        Higher values make the detector less sensitive.
    threshold_mult : float
        Number of ATR multiples the cumulative sum must exceed to
        declare a change point.
    min_reduction : float
        Floor for the reduction factor (0.45 → max 55 % reduction).
    decay_bars : int
        Number of bars over which the reduction linearly decays back
        to 1.0 after a change-point detection.
    """

    def __init__(
        self,
        drift_fraction: float = 0.5,
        threshold_mult: float = 3.0,
        min_reduction: float = 0.45,
        decay_bars: int = 5,
    ):
        self.drift_fraction = drift_fraction
        self.threshold_mult = threshold_mult
        self.min_reduction = min_reduction
        self.decay_bars = decay_bars

    def compute_reduction(
        self,
        closes: np.ndarray,
        atr_values: np.ndarray,
    ) -> np.ndarray:
        """
        Return a per-bar multiplier in [min_reduction, 1.0].

        Parameters
        ----------
        closes     : 1-D array of close prices.
        atr_values : 1-D array of ATR values (may contain NaN at start).

        Returns
        -------
        np.ndarray of shape (n,) — multiply onto reversal_amounts.
        """
        n = len(closes)
        reduction = np.ones(n, dtype=float)

        if n < 2:
            return reduction

        returns = np.diff(closes, prepend=closes[0])

        s_pos = 0.0  # upward cumulative sum
        s_neg = 0.0  # downward cumulative sum
        change_points: list[int] = []

        for i in range(1, n):
            atr = atr_values[i] if not np.isnan(atr_values[i]) else 0.0
            if atr <= 0:
                # Fallback: use the absolute return as proxy
                atr = max(abs(returns[i]), 1e-10)

            drift = self.drift_fraction * atr
            threshold = self.threshold_mult * atr

            s_pos = max(0.0, s_pos + returns[i] - drift)
            s_neg = max(0.0, s_neg - returns[i] - drift)

            if s_pos > threshold or s_neg > threshold:
                change_points.append(i)
                s_pos = 0.0
                s_neg = 0.0

        # Apply decay reduction around each change point
        for cp in change_points:
            for d in range(self.decay_bars + 1):
                idx = cp + d
                if idx >= n:
                    break
                t = d / max(self.decay_bars, 1)
                value = self.min_reduction + t * (1.0 - self.min_reduction)
                reduction[idx] = min(reduction[idx], value)

        return reduction
