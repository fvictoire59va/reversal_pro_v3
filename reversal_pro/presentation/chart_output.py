"""Chart output — generates a matplotlib chart with signals, zones, and EMAs."""

from typing import List, Optional

from ..domain.entities import AnalysisResult, ReversalSignal, SupplyDemandZone
from ..domain.enums import ZoneType, TrendState
from ..domain.value_objects import OHLCVBar


def plot_chart(
    bars: List[OHLCVBar],
    result: AnalysisResult,
    title: str = "Reversal Detection Pro v3.0",
    save_path: Optional[str] = None,
    show: bool = True,
) -> None:
    """
    Plot a candlestick-style chart with reversal signals, supply/demand zones,
    and EMA overlays.

    Requires: pip install matplotlib mplfinance
    """
    try:
        import matplotlib.pyplot as plt
        import matplotlib.patches as mpatches
        import matplotlib.dates as mdates
        import numpy as np
        from datetime import datetime
    except ImportError:
        print("matplotlib is required for charting. Install with: pip install matplotlib")
        return

    n = len(bars)
    indices = list(range(n))
    closes = [b.close for b in bars]
    highs = [b.high for b in bars]
    lows = [b.low for b in bars]
    opens = [b.open for b in bars]

    fig, ax = plt.subplots(figsize=(18, 8))
    fig.patch.set_facecolor("#1E1E1E")
    ax.set_facecolor("#1E1E1E")

    # ── Candlesticks ─────────────────────────────────────────────
    for i in range(n):
        color = "#00FF00" if closes[i] >= opens[i] else "#FF0000"
        # Wick
        ax.plot([i, i], [lows[i], highs[i]], color=color, linewidth=0.6)
        # Body
        body_bottom = min(opens[i], closes[i])
        body_height = abs(closes[i] - opens[i])
        ax.bar(i, body_height, bottom=body_bottom, width=0.6,
               color=color, edgecolor=color, linewidth=0.5)

    # ── EMAs ─────────────────────────────────────────────────────
    if result.trend_history:
        ema9 = [t.ema_fast for t in result.trend_history]
        ema14 = [t.ema_mid for t in result.trend_history]
        ema21 = [t.ema_slow for t in result.trend_history]
        ax.plot(indices, ema9, color="#FFD700", linewidth=0.8, alpha=0.7, label="EMA 9")
        ax.plot(indices, ema14, color="#00BFFF", linewidth=0.8, alpha=0.7, label="EMA 14")
        ax.plot(indices, ema21, color="#FF69B4", linewidth=0.8, alpha=0.7, label="EMA 21")

    # ── Supply / Demand zones ────────────────────────────────────
    for zone in result.zones:
        color = "#FF000030" if zone.zone_type == ZoneType.SUPPLY else "#00FF0030"
        edge = "#FF0000" if zone.zone_type == ZoneType.SUPPLY else "#00FF00"
        width = min(zone.end_bar, n - 1) - zone.start_bar
        rect = mpatches.FancyBboxPatch(
            (zone.start_bar, zone.bottom_price),
            width,
            zone.top_price - zone.bottom_price,
            boxstyle="round,pad=0",
            facecolor=color,
            edgecolor=edge,
            linewidth=0.8,
        )
        ax.add_patch(rect)
        label_text = zone.zone_type.value
        mid = (zone.top_price + zone.bottom_price) / 2
        ax.text(
            zone.start_bar + width / 2, mid, label_text,
            color=edge, fontsize=7, ha="center", va="center", alpha=0.7,
        )

    # ── Reversal signals ─────────────────────────────────────────
    for sig in result.signals:
        if sig.is_preview:
            continue
        if sig.bar_index >= n:
            continue

        if sig.is_bullish:
            ax.annotate(
                f"▲ REVERSAL\n{sig.price:,.2f}",
                xy=(sig.bar_index, sig.actual_price),
                xytext=(sig.bar_index, sig.actual_price * 0.997),
                fontsize=7, color="#00FF00", fontweight="bold",
                ha="center", va="top",
                arrowprops=dict(arrowstyle="->", color="#00FF00", lw=1),
            )
            # Horizontal stop line
            end = min(sig.bar_index + 5, n - 1)
            ax.hlines(sig.actual_price, sig.bar_index, end,
                      colors="#00FF00", linewidths=1.5, linestyles="solid")
        else:
            ax.annotate(
                f"▼ REVERSAL\n{sig.price:,.2f}",
                xy=(sig.bar_index, sig.actual_price),
                xytext=(sig.bar_index, sig.actual_price * 1.003),
                fontsize=7, color="#FF0000", fontweight="bold",
                ha="center", va="bottom",
                arrowprops=dict(arrowstyle="->", color="#FF0000", lw=1),
            )
            end = min(sig.bar_index + 5, n - 1)
            ax.hlines(sig.actual_price, sig.bar_index, end,
                      colors="#FF0000", linewidths=1.5, linestyles="solid")

    # ── Style ────────────────────────────────────────────────────
    ax.set_title(title, color="#00FF00", fontsize=14, fontweight="bold")
    ax.tick_params(colors="white")
    ax.spines["bottom"].set_color("#444444")
    ax.spines["top"].set_color("#444444")
    ax.spines["left"].set_color("#444444")
    ax.spines["right"].set_color("#444444")
    ax.grid(True, color="#333333", linewidth=0.3)
    ax.legend(loc="upper left", fontsize=8, facecolor="#2A2A2A", edgecolor="#444444",
              labelcolor="white")

    plt.tight_layout()

    if save_path:
        plt.savefig(save_path, dpi=150, facecolor=fig.get_facecolor())
        print(f"Chart saved to: {save_path}")

    if show:
        plt.show()
