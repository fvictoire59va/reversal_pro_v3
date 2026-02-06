"""
Generate sample OHLCV CSV data for testing / demo purposes.
Creates a synthetic BTC-like price series with trend and noise.

Usage:
    python generate_sample_data.py
"""

import csv
import math
import random
from datetime import datetime, timedelta
from pathlib import Path


def generate_sample_data(
    output_file: str = "data/sample_BTCUSDT_1h.csv",
    num_bars: int = 500,
    start_price: float = 42000.0,
    volatility: float = 0.008,
    seed: int = 42,
) -> str:
    """Generate a realistic-looking OHLCV CSV file."""
    random.seed(seed)

    Path(output_file).parent.mkdir(parents=True, exist_ok=True)

    start_time = datetime(2025, 1, 1, 0, 0, 0)
    price = start_price

    rows = []
    for i in range(num_bars):
        timestamp = start_time + timedelta(hours=i)

        # Trend component (sine wave + drift)
        trend = math.sin(i / 50) * 0.002 + 0.0001

        # Random walk
        change = random.gauss(trend, volatility)
        price *= (1 + change)

        open_p = price
        # Intra-bar noise
        high_p = open_p * (1 + abs(random.gauss(0, volatility * 0.5)))
        low_p = open_p * (1 - abs(random.gauss(0, volatility * 0.5)))
        close_p = open_p * (1 + random.gauss(0, volatility * 0.3))

        # Ensure high >= open/close and low <= open/close
        high_p = max(high_p, open_p, close_p)
        low_p = min(low_p, open_p, close_p)

        volume = random.uniform(100, 5000)

        rows.append([
            timestamp.strftime("%Y-%m-%d %H:%M:%S"),
            f"{open_p:.2f}",
            f"{high_p:.2f}",
            f"{low_p:.2f}",
            f"{close_p:.2f}",
            f"{volume:.2f}",
        ])

        price = close_p

    with open(output_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["timestamp", "open", "high", "low", "close", "volume"])
        writer.writerows(rows)

    print(f"Generated {num_bars} bars → {output_file}")
    return output_file


if __name__ == "__main__":
    generate_sample_data()
