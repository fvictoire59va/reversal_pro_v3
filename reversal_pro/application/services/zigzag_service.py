"""ZigZag calculation service with confirmation support."""

import numpy as np
from typing import List, Optional, Tuple

from ...domain.enums import Direction
from ...domain.entities import Pivot, ZigZagState


class ZigZagService:
    """
    Computes a non-repainting zigzag based on ATR reversal thresholds.
    Supports optional EMA smoothing and confirmation bars.
    """

    def __init__(
        self,
        use_ema: bool = True,
        ema_length: int = 5,
        confirmation_bars: int = 0,
    ):
        self.use_ema = use_ema
        self.ema_length = ema_length
        self.confirmation_bars = confirmation_bars

    @staticmethod
    def _ema(data: np.ndarray, period: int) -> np.ndarray:
        """Simple EMA computation."""
        n = len(data)
        result = np.full(n, np.nan)
        if n == 0 or period <= 0:
            return result
        alpha = 2.0 / (period + 1)
        if n >= period:
            result[period - 1] = np.mean(data[:period])
            for i in range(period, n):
                result[i] = alpha * data[i] + (1.0 - alpha) * result[i - 1]
        return result

    def _prepare_prices(
        self, highs: np.ndarray, lows: np.ndarray
    ) -> Tuple[np.ndarray, np.ndarray]:
        """Return smoothed or raw prices depending on method."""
        if self.use_ema:
            price_h = self._ema(highs, self.ema_length)
            price_l = self._ema(lows, self.ema_length)
        else:
            price_h = highs.copy()
            price_l = lows.copy()
        return price_h, price_l

    def compute_pivots(
        self,
        highs: np.ndarray,
        lows: np.ndarray,
        reversal_amounts: np.ndarray,
    ) -> List[Pivot]:
        """
        Run the zigzag algorithm over all bars.
        Returns a list of confirmed Pivot points.

        Parameters
        ----------
        highs : raw high prices
        lows  : raw low prices
        reversal_amounts : reversal threshold per bar
        """
        n = len(highs)
        price_h, price_l = self._prepare_prices(highs, lows)
        cb = self.confirmation_bars

        state = ZigZagState()
        pivots: List[Pivot] = []

        for i in range(n):
            # Index of the confirmed bar
            ci = i - cb
            if ci < 0:
                continue

            ph = price_h[ci]
            pl = price_l[ci]
            ah = highs[ci]
            al = lows[ci]

            if np.isnan(ph) or np.isnan(pl):
                continue

            rev = reversal_amounts[i]
            if np.isnan(rev):
                continue

            # Initialize
            if state.direction == Direction.NONE:
                state.zhigh = ph
                state.zlow = pl
                state.zhigh_actual = ah
                state.zlow_actual = al
                state.zhigh_bar = ci
                state.zlow_bar = ci
                state.direction = Direction.UP
                continue

            if state.direction == Direction.UP:
                if ph > state.zhigh:
                    state.zhigh = ph
                    state.zhigh_actual = ah
                    state.zhigh_bar = ci

                if state.zhigh - pl >= rev:
                    pivots.append(Pivot(
                        price=state.zhigh,
                        actual_price=state.zhigh_actual,
                        bar_index=state.zhigh_bar,
                        is_high=True,
                    ))
                    state.direction = Direction.DOWN
                    state.zlow = pl
                    state.zlow_actual = al
                    state.zlow_bar = ci

            elif state.direction == Direction.DOWN:
                if pl < state.zlow:
                    state.zlow = pl
                    state.zlow_actual = al
                    state.zlow_bar = ci

                if ph - state.zlow >= rev:
                    pivots.append(Pivot(
                        price=state.zlow,
                        actual_price=state.zlow_actual,
                        bar_index=state.zlow_bar,
                        is_high=False,
                    ))
                    state.direction = Direction.UP
                    state.zhigh = ph
                    state.zhigh_actual = ah
                    state.zhigh_bar = ci

        return pivots

    def compute_preview_pivots(
        self,
        highs: np.ndarray,
        lows: np.ndarray,
        reversal_amounts: np.ndarray,
    ) -> List[Pivot]:
        """
        Preview zigzag (no confirmation delay) — may repaint.
        Used only in preview modes.
        """
        n = len(highs)
        price_h, price_l = self._prepare_prices(highs, lows)

        zhigh = None
        zlow = None
        zhigh_actual = None
        zlow_actual = None
        zhigh_bar = 0
        zlow_bar = 0
        direction = 0
        previews: List[Pivot] = []

        for i in range(n):
            ph = price_h[i]
            pl = price_l[i]
            if np.isnan(ph) or np.isnan(pl):
                continue

            rev = reversal_amounts[i]
            if np.isnan(rev):
                continue

            if zhigh is None:
                zhigh = ph
                zlow = pl
                zhigh_actual = highs[i]
                zlow_actual = lows[i]
                zhigh_bar = i
                zlow_bar = i
                direction = 1
                continue

            if direction == 1:
                if ph > zhigh:
                    zhigh = ph
                    zhigh_actual = highs[i]
                    zhigh_bar = i
                if zhigh - pl >= rev:
                    previews.append(Pivot(
                        price=zhigh,
                        actual_price=zhigh_actual,
                        bar_index=zhigh_bar,
                        is_high=True,
                        is_preview=True,
                    ))
                    direction = -1
                    zlow = pl
                    zlow_actual = lows[i]
                    zlow_bar = i

            elif direction == -1:
                if pl < zlow:
                    zlow = pl
                    zlow_actual = lows[i]
                    zlow_bar = i
                if ph - zlow >= rev:
                    previews.append(Pivot(
                        price=zlow,
                        actual_price=zlow_actual,
                        bar_index=zlow_bar,
                        is_high=False,
                        is_preview=True,
                    ))
                    direction = 1
                    zhigh = ph
                    zhigh_actual = highs[i]
                    zhigh_bar = i

        return previews
