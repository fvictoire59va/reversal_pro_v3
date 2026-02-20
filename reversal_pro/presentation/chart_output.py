"""Chart output — generates a matplotlib chart with signals, zones, and EMAs."""

from typing import Dict, List, Optional

from ..domain.entities import AnalysisResult, ReversalSignal, SupplyDemandZone
from ..domain.enums import ZoneType, TrendState
from ..domain.value_objects import OHLCVBar

# ── Chart Theme ──────────────────────────────────────────────
CHART_THEME: Dict[str, str | float | int] = {
    # Background
    "bg": "#1E1E1E",
    # Candles
    "candle_bull": "#00FF00",
    "candle_bear": "#FF0000",
    "candle_wick_width": 0.6,
    "candle_body_width": 0.6,
    "candle_edge_width": 0.5,
    # EMAs
    "ema_fast_color": "#FFD700",
    "ema_mid_color": "#00BFFF",
    "ema_slow_color": "#FF69B4",
    "ema_width": 0.8,
    "ema_alpha": 0.7,
    # Zones
    "supply_fill": "#FF000030",
    "supply_edge": "#FF0000",
    "demand_fill": "#00FF0030",
    "demand_edge": "#00FF00",
    "zone_edge_width": 0.8,
    "zone_label_size": 7,
    # Signals
    "signal_font_size": 7,
    "signal_line_width": 1.5,
    "signal_extend_bars": 5,
    "bull_offset_pct": 0.997,
    "bear_offset_pct": 1.003,
    # Axes & grid
    "title_color": "#00FF00",
    "title_size": 14,
    "spine_color": "#444444",
    "grid_color": "#333333",
    "grid_width": 0.3,
    "legend_bg": "#2A2A2A",
    "legend_edge": "#444444",
    "legend_size": 8,
}


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
    fig.patch.set_facecolor(CHART_THEME["bg"])
    ax.set_facecolor(CHART_THEME["bg"])

    # ── Candlesticks ─────────────────────────────────────────────
    for i in range(n):
        color = CHART_THEME["candle_bull"] if closes[i] >= opens[i] else CHART_THEME["candle_bear"]
        # Wick
        ax.plot([i, i], [lows[i], highs[i]], color=color, linewidth=CHART_THEME["candle_wick_width"])
        # Body
        body_bottom = min(opens[i], closes[i])
        body_height = abs(closes[i] - opens[i])
        ax.bar(i, body_height, bottom=body_bottom, width=CHART_THEME["candle_body_width"],
               color=color, edgecolor=color, linewidth=CHART_THEME["candle_edge_width"])

    # ── EMAs ─────────────────────────────────────────────────────
    if result.trend_history:
        ema9 = [t.ema_fast for t in result.trend_history]
        ema14 = [t.ema_mid for t in result.trend_history]
        ema21 = [t.ema_slow for t in result.trend_history]
        ax.plot(indices, ema9, color=CHART_THEME["ema_fast_color"], linewidth=CHART_THEME["ema_width"], alpha=CHART_THEME["ema_alpha"], label="EMA 9")
        ax.plot(indices, ema14, color=CHART_THEME["ema_mid_color"], linewidth=CHART_THEME["ema_width"], alpha=CHART_THEME["ema_alpha"], label="EMA 14")
        ax.plot(indices, ema21, color=CHART_THEME["ema_slow_color"], linewidth=CHART_THEME["ema_width"], alpha=CHART_THEME["ema_alpha"], label="EMA 21")

    # ── Supply / Demand zones ────────────────────────────────────
    for zone in result.zones:
        is_supply = zone.zone_type == ZoneType.SUPPLY
        fill = CHART_THEME["supply_fill"] if is_supply else CHART_THEME["demand_fill"]
        edge = CHART_THEME["supply_edge"] if is_supply else CHART_THEME["demand_edge"]
        width = min(zone.end_bar, n - 1) - zone.start_bar
        rect = mpatches.FancyBboxPatch(
            (zone.start_bar, zone.bottom_price),
            width,
            zone.top_price - zone.bottom_price,
            boxstyle="round,pad=0",
            facecolor=fill,
            edgecolor=edge,
            linewidth=CHART_THEME["zone_edge_width"],
        )
        ax.add_patch(rect)
        label_text = zone.zone_type.value
        mid = (zone.top_price + zone.bottom_price) / 2
        ax.text(
            zone.start_bar + width / 2, mid, label_text,
            color=edge, fontsize=CHART_THEME["zone_label_size"], ha="center", va="center", alpha=0.7,
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
                xytext=(sig.bar_index, sig.actual_price * CHART_THEME["bull_offset_pct"]),
                fontsize=CHART_THEME["signal_font_size"], color=CHART_THEME["candle_bull"], fontweight="bold",
                ha="center", va="top",
                arrowprops=dict(arrowstyle="->", color=CHART_THEME["candle_bull"], lw=1),
            )
            # Horizontal stop line
            end = min(sig.bar_index + CHART_THEME["signal_extend_bars"], n - 1)
            ax.hlines(sig.actual_price, sig.bar_index, end,
                      colors=CHART_THEME["candle_bull"], linewidths=CHART_THEME["signal_line_width"], linestyles="solid")
        else:
            ax.annotate(
                f"▼ REVERSAL\n{sig.price:,.2f}",
                xy=(sig.bar_index, sig.actual_price),
                xytext=(sig.bar_index, sig.actual_price * CHART_THEME["bear_offset_pct"]),
                fontsize=CHART_THEME["signal_font_size"], color=CHART_THEME["candle_bear"], fontweight="bold",
                ha="center", va="bottom",
                arrowprops=dict(arrowstyle="->", color=CHART_THEME["candle_bear"], lw=1),
            )
            end = min(sig.bar_index + CHART_THEME["signal_extend_bars"], n - 1)
            ax.hlines(sig.actual_price, sig.bar_index, end,
                      colors=CHART_THEME["candle_bear"], linewidths=CHART_THEME["signal_line_width"], linestyles="solid")

    # ── Style ────────────────────────────────────────────────────
    ax.set_title(title, color=CHART_THEME["title_color"], fontsize=CHART_THEME["title_size"], fontweight="bold")
    ax.tick_params(colors="white")
    ax.spines["bottom"].set_color(CHART_THEME["spine_color"])
    ax.spines["top"].set_color(CHART_THEME["spine_color"])
    ax.spines["left"].set_color(CHART_THEME["spine_color"])
    ax.spines["right"].set_color(CHART_THEME["spine_color"])
    ax.grid(True, color=CHART_THEME["grid_color"], linewidth=CHART_THEME["grid_width"])
    ax.legend(loc="upper left", fontsize=CHART_THEME["legend_size"], facecolor=CHART_THEME["legend_bg"],
              edgecolor=CHART_THEME["legend_edge"], labelcolor="white")

    plt.tight_layout()

    if save_path:
        plt.savefig(save_path, dpi=150, facecolor=fig.get_facecolor())
        print(f"Chart saved to: {save_path}")

    if show:
        plt.show()
