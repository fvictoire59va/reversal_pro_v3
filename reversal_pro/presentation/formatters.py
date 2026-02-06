"""Price and data formatters — mirrors Pine Script's formatPrice()."""


def format_price(price: float, decimals: int = 2) -> str:
    """
    Format a price with comma-separated thousands.
    e.g. 12345.678 => "12,345.68"
    """
    if price != price:  # NaN check
        return "N/A"

    formatted = f"{price:,.{decimals}f}"
    return formatted


def format_percent(value: float) -> str:
    """Format as percentage string."""
    return f"{value:.4f}%"


def format_sensitivity_info(
    preset: str,
    atr_multiplier: float,
    percent_threshold: float,
) -> str:
    """Format sensitivity configuration summary."""
    return (
        f"Preset: {preset}  |  "
        f"ATR Mult: {atr_multiplier:.2f}  |  "
        f"Pct Threshold: {format_percent(percent_threshold)}"
    )
