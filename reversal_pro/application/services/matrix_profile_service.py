"""
Matrix Profile service for early regime-change detection using STUMPY.

Uses the Matrix Profile distance to detect **anomalous subsequences** —
moments when the current price pattern is unlike anything seen recently.
These novelty spikes strongly correlate with trend reversals and can be
detected *before* the ZigZag confirms, reducing overall detection latency.

The approach is:
1. Compute the Matrix Profile on close-price log-returns.
2. Compute a rolling Z-score on the MP distances.
3. Spikes above a threshold indicate regime changes.
4. Near those spikes, lower the reversal threshold so ZigZag confirms faster.

References
----------
- https://github.com/TDAmeritrade/stumpy
- Yeh et al., "Matrix Profile I: All Pairs Similarity Joins for Time
  Series", ICDM 2016.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import List, Optional

import numpy as np

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lazy import — stumpy is heavy; we only load it when actually needed.
# ---------------------------------------------------------------------------
_stumpy = None


def _get_stumpy():
    global _stumpy
    if _stumpy is None:
        try:
            import stumpy as _s
            _stumpy = _s
        except ImportError:
            raise ImportError(
                "stumpy is required for Matrix-Profile-based regime detection. "
                "Install it with:  pip install stumpy"
            )
    return _stumpy


# ---------------------------------------------------------------------------
# Result containers
# ---------------------------------------------------------------------------

@dataclass
class RegimeChangePoint:
    """A detected regime-change point in the time series."""
    bar_index: int
    score: float          # rolling Z-score (higher = stronger anomaly)
    is_significant: bool  # True if above the adaptive threshold


@dataclass
class MatrixProfileResult:
    """Full output of the Matrix Profile analysis."""
    # Per-bar novelty score (rolling Z-score of MP distance).
    # NaN for the first few bars where the MP/rolling window isn't ready.
    novelty_scores: np.ndarray
    # Detected change points (score > threshold)
    change_points: List[RegimeChangePoint]
    # The threshold that was used
    threshold: float
    # Per-bar threshold reduction factor for the ZigZag.
    # Values < 1.0 mean "reduce the reversal amount here".
    threshold_reduction: np.ndarray


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class MatrixProfileService:
    """
    Wraps STUMPY to produce regime-change scores that the reversal
    detection pipeline can exploit to lower detection latency.

    Parameters
    ----------
    subsequence_length : int
        Length of the sliding window (m) used for the Matrix Profile.
        Shorter = more reactive but noisier.  A good starting point is
        8–12 for intraday timeframes.
    z_threshold : float
        Rolling Z-score threshold on MP distances.  Values above this
        are considered regime changes.  Default 1.8 catches significant
        shifts while filtering noise.
    rolling_window : int
        Window size for the rolling mean/std used to compute Z-scores.
        Controls adaptiveness to recent volatility.
    min_reduction : float
        Floor for the threshold reduction factor.  E.g. 0.40 means
        the reversal amount can be reduced by at most 60 % at a regime
        change.
    score_decay_bars : int
        Number of bars over which the reduction factor linearly decays
        back to 1.0 after a regime-change peak.
    use_returns : bool
        If True, compute the Matrix Profile on log-returns rather than
        raw close prices.  Returns are more stationary and usually give
        better results.
    """

    DEFAULT_SUBSEQ_LEN = {
        "1m": 20,
        "3m": 16,
        "5m": 14,
        "15m": 12,
        "30m": 10,
        "1h": 10,
        "2h": 8,
        "4h": 8,
        "6h": 6,
        "8h": 6,
        "12h": 6,
        "1d": 6,
        "3d": 5,
        "1w": 5,
        "1M": 4,
    }

    def __init__(
        self,
        subsequence_length: Optional[int] = None,
        z_threshold: float = 1.8,
        rolling_window: int = 20,
        min_reduction: float = 0.40,
        score_decay_bars: int = 6,
        use_returns: bool = True,
        timeframe: str = "1h",
    ):
        self.subsequence_length = (
            subsequence_length
            or self.DEFAULT_SUBSEQ_LEN.get(timeframe, 10)
        )
        self.z_threshold = z_threshold
        self.rolling_window = rolling_window
        self.min_reduction = min_reduction
        self.score_decay_bars = score_decay_bars
        self.use_returns = use_returns
        self.timeframe = timeframe

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def analyze(self, closes: np.ndarray) -> MatrixProfileResult:
        """
        Run the Matrix-Profile-based regime-change detection.

        Parameters
        ----------
        closes : 1-D array of close prices, chronological order.

        Returns
        -------
        MatrixProfileResult with per-bar scores & change points.
        """
        stumpy = _get_stumpy()
        n = len(closes)
        m = self.subsequence_length

        # ── Guard: not enough data ───────────────────────────────────
        min_required = 2 * m + self.rolling_window
        if n < min_required:
            logger.debug(
                "Not enough bars (%d) for MP with m=%d rw=%d — skipping.",
                n, m, self.rolling_window,
            )
            return self._empty_result(n)

        # ── Prepare the time series ──────────────────────────────────
        ts = self._prepare_series(closes)
        n_ts = len(ts)

        if n_ts < 2 * m:
            return self._empty_result(n)

        # ── Compute the Matrix Profile ───────────────────────────────
        try:
            mp = stumpy.stump(ts, m)
        except Exception as exc:
            logger.warning("stumpy.stump failed: %s — skipping MP.", exc)
            return self._empty_result(n)

        # MP distances (1st column)
        mp_dist = mp[:, 0].astype(float)

        # ── Compute rolling Z-scores of MP distances ─────────────────
        rolling_z = self._rolling_z_score(mp_dist, self.rolling_window)

        # ── Map rolling Z-scores back to bar indices ─────────────────
        # mp_dist has length (n_ts - m + 1).  The i-th MP value
        # corresponds to the subsequence starting at index i in ts.
        # The "novelty" is best attributed to the END of the
        # subsequence: bar_index = i + m - 1 in ts-space.
        # If we used returns, ts is 1 element shorter than closes.
        offset = (m - 1) + (1 if self.use_returns else 0)

        novelty_scores = np.full(n, np.nan)
        for j in range(len(rolling_z)):
            bar_idx = j + offset
            if 0 <= bar_idx < n:
                novelty_scores[bar_idx] = rolling_z[j]

        # ── Detect significant change points ─────────────────────────
        change_points: List[RegimeChangePoint] = []

        for i in range(n):
            if np.isnan(novelty_scores[i]):
                continue
            if novelty_scores[i] >= self.z_threshold:
                change_points.append(
                    RegimeChangePoint(
                        bar_index=i,
                        score=float(novelty_scores[i]),
                        is_significant=True,
                    )
                )

        # ── Merge nearby change points (keep strongest) ──────────────
        change_points = self._merge_nearby(change_points, min_gap=m)

        # ── Compute per-bar threshold reduction factor ───────────────
        threshold_reduction = self._compute_reduction(
            n, change_points, novelty_scores,
        )

        logger.info(
            "MatrixProfile: n=%d  m=%d  change_points=%d  z_threshold=%.2f",
            n, m, len(change_points), self.z_threshold,
        )

        return MatrixProfileResult(
            novelty_scores=novelty_scores,
            change_points=change_points,
            threshold=self.z_threshold,
            threshold_reduction=threshold_reduction,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _prepare_series(self, closes: np.ndarray) -> np.ndarray:
        """Return log-returns or normalised closes."""
        if self.use_returns:
            # Log-returns are more stationary
            lr = np.diff(np.log(closes))
            # Replace any inf/nan with 0
            lr[~np.isfinite(lr)] = 0.0
            return lr
        else:
            # z-normalise
            mu = float(np.mean(closes))
            sd = float(np.std(closes))
            if sd < 1e-10:
                return closes - mu
            return (closes - mu) / sd

    @staticmethod
    def _rolling_z_score(arr: np.ndarray, window: int) -> np.ndarray:
        """
        Compute a rolling (causal) Z-score for each element.
        Uses only past data (no look-ahead).
        """
        n = len(arr)
        result = np.zeros(n, dtype=float)

        for i in range(n):
            start = max(0, i - window + 1)
            chunk = arr[start:i + 1].astype(float)
            mu = float(np.mean(chunk))
            sd = float(np.std(chunk))
            if sd > 1e-10:
                result[i] = (float(arr[i]) - mu) / sd
            else:
                result[i] = 0.0

        return result

    def _merge_nearby(
        self,
        points: List[RegimeChangePoint],
        min_gap: int,
    ) -> List[RegimeChangePoint]:
        """Keep only the strongest point in each min_gap window."""
        if not points:
            return points
        merged: List[RegimeChangePoint] = [points[0]]
        for pt in points[1:]:
            if pt.bar_index - merged[-1].bar_index < min_gap:
                # Keep the one with the higher score
                if pt.score > merged[-1].score:
                    merged[-1] = pt
            else:
                merged.append(pt)
        return merged

    def _compute_reduction(
        self,
        n: int,
        change_points: List[RegimeChangePoint],
        scores: np.ndarray,
    ) -> np.ndarray:
        """
        Build a per-bar multiplier in [min_reduction, 1.0].

        At a change-point bar the multiplier drops proportionally to
        the score strength.  After the change point the reduction decays
        linearly over `score_decay_bars` bars back to 1.0.
        """
        reduction = np.ones(n, dtype=float)

        for cp in change_points:
            # Normalise the score to a 0..1 "strength" using a sigmoid-like
            # mapping: strength = 1 - 1/(1 + score - threshold)
            excess = max(0.0, cp.score - self.z_threshold)
            strength = 1.0 - 1.0 / (1.0 + excess)
            floor = self.min_reduction + (1.0 - strength) * (1.0 - self.min_reduction)

            for d in range(self.score_decay_bars + 1):
                idx = cp.bar_index + d
                if idx >= n:
                    break
                # Linear decay from floor → 1.0
                t = d / max(self.score_decay_bars, 1)
                value = floor + t * (1.0 - floor)
                # Take the minimum if overlapping reductions
                reduction[idx] = min(reduction[idx], value)

        return reduction

    def _empty_result(self, n: int) -> MatrixProfileResult:
        """Return a neutral result when analysis cannot be performed."""
        return MatrixProfileResult(
            novelty_scores=np.full(n, np.nan),
            change_points=[],
            threshold=self.z_threshold,
            threshold_reduction=np.ones(n, dtype=float),
        )
