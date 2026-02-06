"""Console output — pretty-prints analysis results to the terminal."""

from typing import Optional

from ..domain.entities import AnalysisResult, ReversalSignal, SupplyDemandZone, TrendInfo
from ..domain.enums import TrendState, ZoneType
from .formatters import format_price


# ============================================================================
# ANSI color helpers
# ============================================================================
class _C:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    GREEN = "\033[92m"
    RED = "\033[91m"
    YELLOW = "\033[93m"
    CYAN = "\033[96m"
    PURPLE = "\033[95m"
    WHITE = "\033[97m"
    DIM = "\033[2m"
    BG_DARK = "\033[40m"


def _colored(text: str, color: str) -> str:
    return f"{color}{text}{_C.RESET}"


# ============================================================================
# Main output
# ============================================================================
def print_header() -> None:
    """Print the indicator banner."""
    border = _colored("=" * 62, _C.GREEN)
    print(border)
    print(_colored("  REVERSAL DETECTION PRO v3.0 — Non-Repainting (Python)", _C.GREEN + _C.BOLD))
    print(border)
    print()


def print_info_table(result: AnalysisResult, settings: dict) -> None:
    """
    Print an information table similar to the Pine Script info panel.

    Parameters
    ----------
    result   : analysis result
    settings : dict with keys like 'signal_mode', 'sensitivity', etc.
    """
    print(_colored("┌─────────────────────────────────────────────┐", _C.GREEN))
    print(_colored("│  REVERSAL PRO v3.0       NON-REPAINTING     │", _C.GREEN + _C.BOLD))
    print(_colored("├─────────────────────────────────────────────┤", _C.GREEN))

    # Mode
    mode = settings.get("signal_mode", "Confirmed Only")
    mode_color = _C.GREEN
    if "Preview" in mode:
        mode_color = _C.YELLOW
    if mode == "Preview Only":
        mode_color = _C.RED
    _print_row("Mode", mode, mode_color)

    # Sensitivity
    _print_row("Sensitivity", settings.get("sensitivity", "Medium"), _C.YELLOW)

    # ATR Mult
    _print_row("ATR Mult", f"{result.atr_multiplier:.2f}", _C.CYAN)

    # Current ATR
    _print_row("Current ATR", format_price(result.current_atr, 6), _C.CYAN)

    # Threshold
    _print_row("Threshold", format_price(result.current_threshold, 6), _C.YELLOW)

    # Trend
    if result.current_trend:
        trend = result.current_trend
        t_color = {
            TrendState.BULLISH: _C.GREEN,
            TrendState.BEARISH: _C.RED,
            TrendState.NEUTRAL: _C.PURPLE,
        }.get(trend.state, _C.WHITE)
        _print_row("Trend", trend.state.value, t_color)
    else:
        _print_row("Trend", "N/A", _C.DIM)

    print(_colored("└─────────────────────────────────────────────┘", _C.GREEN))
    print()


def _print_row(label: str, value: str, value_color: str = _C.WHITE) -> None:
    lbl = f"  {label}:".ljust(18)
    print(
        _colored("│", _C.GREEN)
        + _colored(lbl, _C.WHITE)
        + _colored(value.ljust(27), value_color)
        + _colored("│", _C.GREEN)
    )


def print_signals(signals: list) -> None:
    """Print a table of reversal signals."""
    confirmed = [s for s in signals if not s.is_preview]
    previews = [s for s in signals if s.is_preview]

    if confirmed:
        print(_colored("─── Confirmed Reversal Signals ─────────────────", _C.BOLD))
        print(f"  {'Bar':>6}  {'Direction':>10}  {'Price':>14}  {'Actual':>14}")
        print(f"  {'---':>6}  {'---':>10}  {'---':>14}  {'---':>14}")
        for s in confirmed:
            color = _C.GREEN if s.is_bullish else _C.RED
            arrow = "▲ BULL" if s.is_bullish else "▼ BEAR"
            print(
                f"  {s.bar_index:>6}  "
                f"{_colored(arrow, color):>21}  "
                f"{format_price(s.price):>14}  "
                f"{format_price(s.actual_price):>14}"
            )
        print()

    if previews:
        print(_colored("─── Preview Signals (may repaint) ───────────────", _C.DIM))
        for s in previews:
            color = _C.GREEN if s.is_bullish else _C.RED
            arrow = "▲ BULL" if s.is_bullish else "▼ BEAR"
            print(
                f"  {s.bar_index:>6}  "
                f"{_colored(arrow, color):>21}  "
                f"{format_price(s.price):>14}  "
                f"{format_price(s.actual_price):>14}"
            )
        print()


def print_zones(zones: list) -> None:
    """Print supply/demand zones."""
    if not zones:
        return

    print(_colored("─── Supply / Demand Zones ──────────────────────", _C.BOLD))
    print(f"  {'Type':>8}  {'Center':>14}  {'Top':>14}  {'Bottom':>14}  {'Bars':>10}")
    print(f"  {'---':>8}  {'---':>14}  {'---':>14}  {'---':>14}  {'---':>10}")

    for z in zones:
        color = _C.RED if z.zone_type == ZoneType.SUPPLY else _C.GREEN
        label = z.zone_type.value
        bar_range = f"{z.start_bar}-{z.end_bar}"
        print(
            f"  {_colored(label, color):>19}  "
            f"{format_price(z.center_price):>14}  "
            f"{format_price(z.top_price):>14}  "
            f"{format_price(z.bottom_price):>14}  "
            f"{bar_range:>10}"
        )
    print()


def print_trend_summary(trends: list, last_n: int = 5) -> None:
    """Print the last N trend snapshots."""
    if not trends:
        return

    tail = trends[-last_n:]
    print(_colored(f"─── Last {last_n} Trend Snapshots ─────────────────────", _C.BOLD))
    print(f"  {'Bar':>6}  {'Trend':>10}  {'EMA9':>12}  {'EMA14':>12}  {'EMA21':>12}")
    print(f"  {'---':>6}  {'---':>10}  {'---':>12}  {'---':>12}  {'---':>12}")

    start_idx = len(trends) - len(tail)
    for i, t in enumerate(tail):
        bar_idx = start_idx + i
        t_color = {
            TrendState.BULLISH: _C.GREEN,
            TrendState.BEARISH: _C.RED,
            TrendState.NEUTRAL: _C.PURPLE,
        }.get(t.state, _C.WHITE)

        print(
            f"  {bar_idx:>6}  "
            f"{_colored(t.state.value, t_color):>21}  "
            f"{format_price(t.ema_fast, 4):>12}  "
            f"{format_price(t.ema_mid, 4):>12}  "
            f"{format_price(t.ema_slow, 4):>12}"
        )
    print()


def print_full_report(result: AnalysisResult, settings: dict) -> None:
    """Print the complete analysis report."""
    print_header()
    print_info_table(result, settings)
    print_signals(result.signals)
    print_zones(result.zones)
    print_trend_summary(result.trend_history, last_n=10)

    # Summary footer
    bullish = sum(1 for s in result.signals if s.is_bullish and not s.is_preview)
    bearish = sum(1 for s in result.signals if not s.is_bullish and not s.is_preview)
    print(_colored("─── Summary ───────────────────────────────────", _C.BOLD))
    print(f"  Total confirmed signals: {bullish + bearish}")
    print(f"    Bullish: {_colored(str(bullish), _C.GREEN)}")
    print(f"    Bearish: {_colored(str(bearish), _C.RED)}")
    print(f"  Supply/Demand zones: {len(result.zones)}")
    print()
