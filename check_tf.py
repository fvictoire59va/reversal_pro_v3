import requests
API = 'http://176.131.66.167:8080'
for tf in ['1m', '5m', '15m', '1h']:
    r = requests.get(f'{API}/api/analysis/chart/BTC-USDT/{tf}', params={
        'limit': 500, 'sensitivity': 'Medium', 'signal_mode': 'Confirmed Only'
    }, timeout=30)
    if r.status_code == 200:
        d = r.json()
        candles = len(d.get('candles', []))
        signals = len(d.get('markers', []))
        atr = d.get('current_atr', 0)
        thr = d.get('threshold', 0)
        mult = d.get('atr_multiplier', 0)
        print(f"{tf}: {candles} candles, {signals} signals, ATR={atr:.2f}, mult={mult}, threshold={thr:.2f}")
    else:
        print(f"{tf}: Error {r.status_code} - {r.text[:100]}")
