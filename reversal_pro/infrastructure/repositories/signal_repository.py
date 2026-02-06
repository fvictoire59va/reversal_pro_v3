"""
Signal repository — persists analysis results to JSON files.
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from ...domain.entities import AnalysisResult, ReversalSignal, SupplyDemandZone


class SignalRepository:
    """Save and load analysis results as JSON."""

    def __init__(self, output_dir: str = "output"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def save(
        self,
        result: AnalysisResult,
        symbol: str = "UNKNOWN",
        timeframe: str = "",
    ) -> Path:
        """Serialize an AnalysisResult to a JSON file."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"signals_{symbol.replace('/', '_')}_{timestamp}.json"
        filepath = self.output_dir / filename

        data = {
            "symbol": symbol,
            "timeframe": timeframe,
            "generated_at": datetime.now().isoformat(),
            "summary": {
                "current_atr": result.current_atr,
                "current_threshold": result.current_threshold,
                "atr_multiplier": result.atr_multiplier,
                "current_trend": result.current_trend.state.value if result.current_trend else "N/A",
                "total_signals": len(result.signals),
                "total_pivots": len(result.pivots),
                "total_zones": len(result.zones),
            },
            "signals": [self._signal_to_dict(s) for s in result.signals],
            "zones": [self._zone_to_dict(z) for z in result.zones],
        }

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, default=str)

        return filepath

    @staticmethod
    def _signal_to_dict(signal: ReversalSignal) -> dict:
        return {
            "bar_index": signal.bar_index,
            "price": signal.price,
            "actual_price": signal.actual_price,
            "is_bullish": signal.is_bullish,
            "is_preview": signal.is_preview,
            "label": signal.label,
            "direction": signal.direction_text,
        }

    @staticmethod
    def _zone_to_dict(zone: SupplyDemandZone) -> dict:
        return {
            "zone_type": zone.zone_type.value,
            "center_price": zone.center_price,
            "top_price": zone.top_price,
            "bottom_price": zone.bottom_price,
            "start_bar": zone.start_bar,
            "end_bar": zone.end_bar,
        }
