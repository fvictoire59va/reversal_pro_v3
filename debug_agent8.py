import requests, json
from datetime import datetime

BASE = "http://176.131.66.167:8080/api"

# Correct endpoint with agent 8 settings (sensitivity=Low, signal_mode=Confirmed Only)
r = requests.get(f"{BASE}/analysis/chart/BTC-USDT/5m?limit=500&sensitivity=Low&signal_mode=Confirmed+Only")
print("Status:", r.status_code)
data = r.json()
print("Keys:", list(data.keys()))

# Signals
markers = data.get("markers", [])
print(f"Total markers: {len(markers)}")
for m in markers[-15:]:
    ts = m.get("time")
    dt = datetime.utcfromtimestamp(ts).strftime("%H:%M") if ts else "?"
    detected = m.get("detected_at", "N/A")
    delay = m.get("candles_delay", "?")
    shape = m.get("shape", "?")
    text = m.get("text", "")
    print(f"  {dt} UTC | {shape} | {text} | det={detected} | delay={delay}")

# Show last 10 candles
candles = data.get("candles", [])
print(f"\nTotal candles: {len(candles)}")
for c in candles[-10:]:
    ts = c.get("time")
    dt = datetime.utcfromtimestamp(ts).strftime("%H:%M") if ts else "?"
    o, h, l, cl = c["open"], c["high"], c["low"], c["close"]
    print(f"  {dt} UTC | O={o:.2f} H={h:.2f} L={l:.2f} C={cl:.2f}")

# Look for signals near 17:55 UTC (=18:55 Paris)
print("\n=== Signals 17:00-19:00 UTC (=18:00-20:00 Paris) ===")
for m in markers:
    ts = m.get("time")
    if ts:
        dt = datetime.utcfromtimestamp(ts)
        if 17 <= dt.hour <= 19:
            shape = m.get("shape", "?")
            text = m.get("text", "")
            detected = m.get("detected_at", "N/A")
            print(f"  {dt.strftime('%H:%M')} UTC | {shape} | {text} | det={detected}")

# Stored signals from DB
print("\n=== Stored signals from DB ===")
r2 = requests.get(f"{BASE}/analysis/signals/BTC-USDT/5m")
print("Signals status:", r2.status_code)
sigs = r2.json()
if isinstance(sigs, list):
    for s in sigs[-10:]:
        print(f"  {s}")
elif isinstance(sigs, dict) and "signals" in sigs:
    for s in sigs["signals"][-10:]:
        sig_time = s.get("signal_time", "?")
        det_time = s.get("detected_at", "?")
        direction = s.get("direction", "?")
        print(f"  signal_time={sig_time} | detected={det_time} | dir={direction}")
else:
    print(json.dumps(sigs, indent=2)[:500])
