"""Reversal signal detection service."""

from typing import List

from ...domain.entities import Pivot, ReversalSignal, SignalState


class ReversalDetector:
    """
    Converts zigzag pivots into actionable reversal signals.
    Mirrors the Pine Script U1/D1 logic.
    """

    def detect(
        self,
        pivots: List[Pivot],
        n_bars: int,
        price_h: "np.ndarray",
        price_l: "np.ndarray",
    ) -> List[ReversalSignal]:
        """
        Walk through bars and emit reversal signals when direction changes
        and price confirms past the extreme inflection level.

        Parameters
        ----------
        pivots   : confirmed pivots from the zigzag
        n_bars   : total number of bars
        price_h  : confirmed high prices (smoothed or raw)
        price_l  : confirmed low prices (smoothed or raw)

        Returns
        -------
        List of ReversalSignal
        """
        import numpy as np

        state = SignalState()
        signals: List[ReversalSignal] = []

        # Build a pivot lookup: bar_index -> pivot
        pivot_at_bar = {}
        for p in pivots:
            pivot_at_bar.setdefault(p.bar_index, []).append(p)

        # We need to iterate bars in order, processing pivots as they appear
        pivot_iter = iter(sorted(pivots, key=lambda p: p.bar_index))
        next_pivot = next(pivot_iter, None)

        for i in range(n_bars):
            ph = price_h[i] if not np.isnan(price_h[i]) else None
            pl = price_l[i] if not np.isnan(price_l[i]) else None

            # Process any pivot on this bar
            while next_pivot is not None and next_pivot.bar_index <= i:
                if next_pivot.is_high:
                    state.eih = next_pivot.price
                    state.eih_actual = next_pivot.actual_price
                    state.eih_bar = next_pivot.bar_index
                    state.dir = -1
                else:
                    state.eil = next_pivot.price
                    state.eil_actual = next_pivot.actual_price
                    state.eil_bar = next_pivot.bar_index
                    state.dir = 1
                next_pivot = next(pivot_iter, None)

            # Signal detection
            state.prev_signal = state.signal

            if state.dir > 0 and pl is not None and state.eil is not None:
                if pl > state.eil:
                    if state.signal <= 0:
                        state.signal = 1
            elif state.dir < 0 and ph is not None and state.eih is not None:
                if ph < state.eih:
                    if state.signal >= 0:
                        state.signal = -1

            # U1: bullish reversal onset
            if state.signal > 0 and state.prev_signal <= 0:
                signals.append(ReversalSignal(
                    bar_index=state.eil_bar,
                    price=state.eil,
                    actual_price=state.eil_actual or state.eil,
                    is_bullish=True,
                    is_preview=False,
                ))

            # D1: bearish reversal onset
            if state.signal < 0 and state.prev_signal >= 0:
                signals.append(ReversalSignal(
                    bar_index=state.eih_bar,
                    price=state.eih,
                    actual_price=state.eih_actual or state.eih,
                    is_bullish=False,
                    is_preview=False,
                ))

        return signals
