"""
Reversal Detection Pro v3.0 — Main Entry Point
================================================
Usage:
    # With CSV data:
    python -m reversal_pro.main --source csv --file data/BTCUSDT_1h.csv

    # With ccxt (live exchange data):
    python -m reversal_pro.main --source ccxt --exchange binance --symbol BTC/USDT --timeframe 1h

    # With custom sensitivity:
    python -m reversal_pro.main --source csv --file data.csv --sensitivity "Very High"

    # Save chart + signals:
    python -m reversal_pro.main --source csv --file data.csv --save-chart chart.png --save-signals
"""

import argparse
import sys
from typing import List

from .config import (
    AppConfig, SignalSettings, SensitivitySettings, AdvancedSettings,
    ZoneSettings, EMASettings, DataSettings,
)
from .domain.enums import SignalMode, SensitivityPreset, CalculationMethod
from .domain.value_objects import SensitivityConfig, OHLCVBar
from .application.use_cases.detect_reversals import DetectReversalsUseCase
from .infrastructure.data_providers.ohlcv_provider import CSVProvider, CCXTProvider
from .infrastructure.repositories.signal_repository import SignalRepository
from .presentation.console_output import print_full_report


def parse_args() -> AppConfig:
    """Parse CLI arguments into AppConfig."""
    parser = argparse.ArgumentParser(
        description="Reversal Detection Pro v3.0 — Non-Repainting Python Edition",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    # Data source
    parser.add_argument("--source", choices=["csv", "ccxt"], default="csv",
                        help="Data source type (default: csv)")
    parser.add_argument("--file", default="", help="CSV file path")
    parser.add_argument("--exchange", default="binance", help="ccxt exchange id")
    parser.add_argument("--symbol", default="BTC/USDT", help="Trading pair symbol")
    parser.add_argument("--timeframe", default="1h", help="Candle timeframe")
    parser.add_argument("--limit", type=int, default=500, help="Number of bars to fetch")

    # Signal mode
    parser.add_argument("--mode", default="Confirmed Only",
                        choices=["Confirmed Only", "Confirmed + Preview", "Preview Only"],
                        help="Signal confirmation mode")
    parser.add_argument("--confirmation-bars", type=int, default=0,
                        help="Extra confirmation bars (0-5)")

    # Sensitivity
    parser.add_argument("--sensitivity", default="Medium",
                        choices=["Very High", "High", "Medium", "Low", "Very Low", "Custom"],
                        help="Sensitivity preset")
    parser.add_argument("--atr-multiplier", type=float, default=2.0,
                        help="Custom ATR multiplier")
    parser.add_argument("--percent-threshold", type=float, default=0.01,
                        help="Custom percent threshold")

    # Advanced
    parser.add_argument("--method", choices=["average", "high_low"], default="average",
                        help="Calculation method")
    parser.add_argument("--atr-length", type=int, default=5, help="ATR period")
    parser.add_argument("--average-length", type=int, default=5, help="EMA smoothing period")
    parser.add_argument("--absolute-reversal", type=float, default=0.05,
                        help="Absolute reversal amount")

    # Zones
    parser.add_argument("--show-zones", action="store_true", help="Generate supply/demand zones")
    parser.add_argument("--num-zones", type=int, default=3, help="Max zones to display")
    parser.add_argument("--zone-extension", type=int, default=20, help="Zone box extension in bars")
    parser.add_argument("--zone-thickness", type=float, default=0.02,
                        help="Zone thickness as %% of price")

    # Output
    parser.add_argument("--chart", action="store_true", help="Show matplotlib chart")
    parser.add_argument("--save-chart", default="", help="Save chart to file path")
    parser.add_argument("--save-signals", action="store_true", help="Save signals to JSON")
    parser.add_argument("--output-dir", default="output", help="Output directory")

    args = parser.parse_args()

    # Build config
    config = AppConfig(
        signal=SignalSettings(
            mode=SignalMode(args.mode),
            confirmation_bars=args.confirmation_bars,
        ),
        sensitivity=SensitivitySettings(
            preset=SensitivityPreset(args.sensitivity),
            custom_atr_multiplier=args.atr_multiplier,
            custom_percent_threshold=args.percent_threshold,
        ),
        advanced=AdvancedSettings(
            calculation_method=CalculationMethod(args.method),
            absolute_reversal=args.absolute_reversal,
            atr_length=args.atr_length,
            average_length=args.average_length,
        ),
        zones=ZoneSettings(
            show_cloud=args.show_zones,
            num_zones=args.num_zones,
            zone_extension_bars=args.zone_extension,
            zone_thickness_pct=args.zone_thickness,
        ),
        ema=EMASettings(),
        data=DataSettings(
            source=args.source,
            file_path=args.file,
            exchange=args.exchange,
            symbol=args.symbol,
            timeframe=args.timeframe,
            limit=args.limit,
        ),
        show_chart=args.chart,
        save_chart=args.save_chart,
        save_signals=args.save_signals,
        output_dir=args.output_dir,
    )

    return config


def load_bars(config: AppConfig) -> List[OHLCVBar]:
    """Load OHLCV bars from the configured data source."""
    if config.data.source == "csv":
        if not config.data.file_path:
            print("ERROR: --file is required when --source=csv")
            sys.exit(1)
        provider = CSVProvider(config.data.file_path)
        return provider.fetch(limit=config.data.limit)
    elif config.data.source == "ccxt":
        provider = CCXTProvider(
            exchange_id=config.data.exchange,
            api_key=config.data.api_key,
            secret=config.data.secret,
        )
        return provider.fetch(
            symbol=config.data.symbol,
            timeframe=config.data.timeframe,
            limit=config.data.limit,
        )
    else:
        print(f"ERROR: Unknown source '{config.data.source}'")
        sys.exit(1)


def run(config: AppConfig) -> None:
    """Execute the full analysis pipeline."""
    # ── Load data ────────────────────────────────────────────────
    bars = load_bars(config)
    if not bars:
        print("ERROR: No bars loaded. Check your data source.")
        sys.exit(1)

    print(f"Loaded {len(bars)} bars from {config.data.source}")

    # ── Build custom sensitivity if needed ───────────────────────
    custom_config = None
    if config.sensitivity.preset == SensitivityPreset.CUSTOM:
        custom_config = SensitivityConfig.from_custom(
            atr_multiplier=config.sensitivity.custom_atr_multiplier,
            percent_threshold=config.sensitivity.custom_percent_threshold,
        )

    # ── Create and execute use case ──────────────────────────────
    use_case = DetectReversalsUseCase(
        signal_mode=config.signal.mode,
        sensitivity=config.sensitivity.preset,
        custom_config=custom_config,
        calculation_method=config.advanced.calculation_method,
        atr_length=config.advanced.atr_length,
        average_length=config.advanced.average_length,
        confirmation_bars=config.signal.confirmation_bars,
        absolute_reversal=config.advanced.absolute_reversal,
        zone_thickness_pct=config.zones.zone_thickness_pct,
        zone_extension_bars=config.zones.zone_extension_bars,
        max_zones=config.zones.num_zones,
        generate_zones=config.zones.show_cloud,
        ema_fast=config.ema.superfast_length,
        ema_mid=config.ema.fast_length,
        ema_slow=config.ema.slow_length,
    )

    result = use_case.execute(bars)

    # ── Console output ───────────────────────────────────────────
    settings = {
        "signal_mode": config.signal.mode.value,
        "sensitivity": config.sensitivity.preset.value,
    }
    print_full_report(result, settings)

    # ── Save signals to JSON ─────────────────────────────────────
    if config.save_signals:
        repo = SignalRepository(output_dir=config.output_dir)
        filepath = repo.save(
            result,
            symbol=config.data.symbol,
            timeframe=config.data.timeframe,
        )
        print(f"Signals saved to: {filepath}")

    # ── Chart output ─────────────────────────────────────────────
    if config.show_chart or config.save_chart:
        from .presentation.chart_output import plot_chart
        plot_chart(
            bars,
            result,
            title=f"Reversal Pro v3.0 — {config.data.symbol} {config.data.timeframe}",
            save_path=config.save_chart or None,
            show=config.show_chart,
        )


def main():
    """CLI entry point."""
    config = parse_args()
    run(config)


if __name__ == "__main__":
    main()
