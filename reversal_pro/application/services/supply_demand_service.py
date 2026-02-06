"""Supply/Demand zone generation service."""

from typing import List

from ...domain.entities import Pivot, SupplyDemandZone
from ...domain.enums import ZoneType


class SupplyDemandService:
    """Generates supply and demand zones from confirmed pivots."""

    def __init__(
        self,
        zone_thickness_pct: float = 0.02,
        zone_extension_bars: int = 20,
        max_zones: int = 3,
    ):
        self.zone_thickness_pct = zone_thickness_pct
        self.zone_extension_bars = zone_extension_bars
        self.max_zones = max_zones

    def generate_zones(self, pivots: List[Pivot]) -> List[SupplyDemandZone]:
        """
        Create supply/demand zones from pivots.
        - Pivot LOW (is_high=False) => Demand zone (GREEN)
        - Pivot HIGH (is_high=True) => Supply zone (RED)
        """
        zones: List[SupplyDemandZone] = []

        for pivot in pivots:
            if pivot.is_preview:
                continue

            zone_type = ZoneType.SUPPLY if pivot.is_high else ZoneType.DEMAND
            center = pivot.actual_price
            half = (center * self.zone_thickness_pct / 100.0) / 2.0

            zone = SupplyDemandZone(
                zone_type=zone_type,
                center_price=center,
                top_price=center + half,
                bottom_price=center - half,
                start_bar=pivot.bar_index,
                end_bar=pivot.bar_index + self.zone_extension_bars,
            )
            zones.append(zone)

        # Keep only the last N zones
        if self.max_zones > 0 and len(zones) > self.max_zones:
            zones = zones[-self.max_zones:]

        return zones
