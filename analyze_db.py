"""Analyze the reversal detection delay via the remote API."""
import requests
import json
from datetime import datetime

API = "http://176.131.66.167:8080"

# 1. Health check
print("=" * 60)
print("HEALTH CHECK")
print("=" * 60)
try:
    r = requests.get(f"{API}/api/health", timeout=5)
    print(f"  Status: {r.status_code} — {r.json()}")
except Exception as e:
    print(f"  Error: {e}")
    # Try direct backend port
    API = "http://176.131.66.167:8000"
    try:
        r = requests.get(f"{API}/health", timeout=5)
        print(f"  Direct backend: {r.status_code} — {r.json()}")
    except Exception as e2:
        print(f"  Direct backend also failed: {e2}")

# 2. Get chart data for BTC/USDT 1m — this includes signals + OHLCV
print("\n" + "=" * 60)
print("CHART DATA — BTC/USDT 1m")
print("=" * 60)
try:
    r = requests.get(f"{API}/api/analysis/chart/BTC-USDT/1m", params={
        "limit": 500,
        "sensitivity": "Medium",
        "signal_mode": "Confirmed Only",
    }, timeout=30)
    if r.status_code == 200:
        data = r.json()
        print(f"  Symbol: {data.get('symbol')}")
        print(f"  Timeframe: {data.get('timeframe')}")
        print(f"  Candles: {len(data.get('candles', []))}")
        print(f"  Current ATR: {data.get('current_atr', 'N/A')}")
        print(f"  Threshold: {data.get('threshold', 'N/A')}")
        print(f"  ATR Multiplier: {data.get('atr_multiplier', 'N/A')}")
        print(f"  Current Trend: {data.get('current_trend', 'N/A')}")

        # Signals (markers)
        markers = data.get('markers', [])
        print(f"\n  Signals (markers): {len(markers)}")
        for m in markers:
            ts = datetime.utcfromtimestamp(m['time'])
            direction = "LONG" if m.get('color') == '#00FF00' else "SHORT"
            detected_at = m.get('detected_at', 'N/A')
            candles_delay = m.get('candles_delay', 'N/A')
            print(f"    {ts} | {direction:5s} | {m.get('text','')} | detected_at={detected_at} | delay={candles_delay} candles")

        # Show OHLCV around 08:20-08:30 and 09:33-09:40
        candles = data.get('candles', [])
        print(f"\n  Candles near 08:22 (reversal point):")
        for c in candles:
            ts = datetime.utcfromtimestamp(c['time'])
            if ts.hour == 8 and 18 <= ts.minute <= 30 and ts.day == 14:
                print(f"    {ts} | O={c['open']:,.2f} H={c['high']:,.2f} L={c['low']:,.2f} C={c['close']:,.2f}")

        print(f"\n  Candles near 09:37 (detection time):")
        for c in candles:
            ts = datetime.utcfromtimestamp(c['time'])
            if ts.hour == 9 and 33 <= ts.minute <= 40 and ts.day == 14:
                print(f"    {ts} | O={c['open']:,.2f} H={c['high']:,.2f} L={c['low']:,.2f} C={c['close']:,.2f}")

        # Find the lowest low in vicinity
        if candles:
            target_candles = [c for c in candles 
                              if datetime.utcfromtimestamp(c['time']).day == 14
                              and 8 <= datetime.utcfromtimestamp(c['time']).hour <= 9]
            if target_candles:
                min_c = min(target_candles, key=lambda c: c['low'])
                max_c = max(target_candles, key=lambda c: c['high'])
                print(f"\n  08:00-10:00 range:")
                print(f"    Lowest low:  {datetime.utcfromtimestamp(min_c['time'])} @ {min_c['low']:,.2f}")
                print(f"    Highest high: {datetime.utcfromtimestamp(max_c['time'])} @ {max_c['high']:,.2f}")
                print(f"    Range: ${max_c['high'] - min_c['low']:,.2f}")
    else:
        print(f"  Error: {r.status_code} — {r.text[:500]}")
except Exception as e:
    print(f"  Error: {e}")

# 3. Watchlist
print("\n" + "=" * 60)
print("WATCHLIST")
print("=" * 60)
try:
    r = requests.get(f"{API}/api/watchlist/", timeout=10)
    if r.status_code == 200:
        data = r.json()
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    print(f"  {item.get('symbol')} | {item.get('timeframe')} | {item.get('exchange')} | active={item.get('is_active')}")
                else:
                    print(f"  {item}")
        else:
            print(f"  Response: {data}")
    else:
        print(f"  Error: {r.status_code} — {r.text[:200]}")
except Exception as e:
    print(f"  Error: {e}")

# 4. Check OHLCV data availability per timeframe
print("\n" + "=" * 60)
print("OHLCV DATA PER TIMEFRAME")
print("=" * 60)
for tf in ['1m', '5m', '15m', '1h', '4h', '1d']:
    try:
        r = requests.get(f"{API}/api/analysis/chart/BTC-USDT/{tf}", params={
            "limit": 10,
            "sensitivity": "Medium",
            "signal_mode": "Confirmed Only",
        }, timeout=15)
        if r.status_code == 200:
            data = r.json()
            n = len(data.get('candles', []))
            print(f"  {tf}: {n} candles available")
        else:
            print(f"  {tf}: Error {r.status_code} — {r.text[:100]}")
    except Exception as e:
        print(f"  {tf}: Error — {e}")
