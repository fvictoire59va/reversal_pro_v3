"""EMA (Exponential Moving Average) and trend detection service."""

import numpy as np
from typing import List, Tuple

from ...domain.enums import TrendState
from ...domain.entities import TrendInfo, EMAState


class EMAService:
    """Computes EMAs and derives triple-EMA trend signals."""

    @staticmethod
    def ema(data: np.ndarray, period: int) -> np.ndarray:
        """
        Compute Exponential Moving Average.
        Uses the standard EMA formula: EMA_t = alpha * price_t + (1 - alpha) * EMA_{t-1}
        """
        n = len(data)
        result = np.full(n, np.nan)
        if n == 0 or period <= 0:
            return result

        alpha = 2.0 / (period + 1)

        # Seed with SMA over first `period` bars
        if n >= period:
            result[period - 1] = np.mean(data[:period])
            for i in range(period, n):
                result[i] = alpha * data[i] + (1.0 - alpha) * result[i - 1]
        else:
            # Not enough data — compute SMA of available
            result[-1] = np.mean(data)

        return result

    @staticmethod
    def compute_trend(
        closes: np.ndarray,
        highs: np.ndarray,
        lows: np.ndarray,
        superfast_length: int = 9,
        fast_length: int = 14,
        slow_length: int = 21,
    ) -> Tuple[List[TrendInfo], EMAState]:
        """
        Compute triple-EMA trend for each bar.
        Returns list of TrendInfo (one per bar) and final EMAState.

        Buy: EMA9 > EMA14 > EMA21 and low > EMA9
        Sell: EMA9 < EMA14 < EMA21 and high < EMA9
        """
        n = len(closes)

        ema9 = EMAService.ema(closes, superfast_length)
        ema14 = EMAService.ema(closes, fast_length)
        ema21 = EMAService.ema(closes, slow_length)

        state = EMAState()
        trends: List[TrendInfo] = []

        for i in range(n):
            e9 = ema9[i]
            e14 = ema14[i]
            e21 = ema21[i]

            if np.isnan(e9) or np.isnan(e14) or np.isnan(e21):
                trends.append(TrendInfo(
                    state=TrendState.NEUTRAL,
                    ema_fast=e9 if not np.isnan(e9) else 0.0,
                    ema_mid=e14 if not np.isnan(e14) else 0.0,
                    ema_slow=e21 if not np.isnan(e21) else 0.0,
                ))
                continue

            # Buy conditions
            buy = e9 > e14 and e14 > e21 and lows[i] > e9
            stop_buy = e9 <= e14
            buy_now = buy and not state.prev_buy

            prev_buy_signal = state.buy_signal
            if buy_now and not stop_buy:
                state.buy_signal = 1
            elif state.buy_signal == 1 and stop_buy:
                state.buy_signal = 0

            # Sell conditions
            sell = e9 < e14 and e14 < e21 and highs[i] < e9
            stop_sell = e9 >= e14
            sell_now = sell and not state.prev_sell

            prev_sell_signal = state.sell_signal
            if sell_now and not stop_sell:
                state.sell_signal = 1
            elif state.sell_signal == 1 and stop_sell:
                state.sell_signal = 0

            # Determine trend state
            if state.buy_signal == 1:
                trend_state = TrendState.BULLISH
            elif state.sell_signal == 1:
                trend_state = TrendState.BEARISH
            else:
                trend_state = TrendState.NEUTRAL

            trend_to_bullish = state.buy_signal == 1 and state.prev_buy_signal != 1
            trend_to_bearish = state.sell_signal == 1 and state.prev_sell_signal != 1

            trends.append(TrendInfo(
                state=trend_state,
                ema_fast=e9,
                ema_mid=e14,
                ema_slow=e21,
                buy_signal=buy_now,
                sell_signal=sell_now,
                trend_changed_to_bullish=trend_to_bullish,
                trend_changed_to_bearish=trend_to_bearish,
            ))

            state.prev_buy = buy
            state.prev_sell = sell
            state.prev_buy_signal = state.buy_signal
            state.prev_sell_signal = state.sell_signal

        return trends, state
