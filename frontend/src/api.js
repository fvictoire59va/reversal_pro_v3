/**
 * API client — communicates with the FastAPI backend.
 */

const API_BASE = '/api';

export async function fetchChartData(symbol, timeframe, limit, sensitivity, signalMode) {
    const params = new URLSearchParams({
        limit: String(limit),
        sensitivity: sensitivity,
        signal_mode: signalMode,
    });

    // Replace / with - for URL compatibility
    const urlSymbol = symbol.replace('/', '-');
    const url = `${API_BASE}/analysis/chart/${urlSymbol}/${timeframe}?${params}`;
    const res = await fetch(url);

    if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || `API error ${res.status}`);
    }

    return res.json();
}

export async function fetchFromExchange(symbol, timeframe, exchange = 'binance', limit = 500) {
    const params = new URLSearchParams({
        exchange,
        limit: String(limit),
    });

    // Replace / with - for URL compatibility
    const urlSymbol = symbol.replace('/', '-');
    const url = `${API_BASE}/ohlcv/fetch/${urlSymbol}/${timeframe}?${params}`;
    const res = await fetch(url, { method: 'POST' });

    if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || `Fetch error ${res.status}`);
    }

    return res.json();
}

export async function uploadCSV(file, symbol, timeframe) {
    const formData = new FormData();
    formData.append('file', file);

    const params = new URLSearchParams({ symbol, timeframe });
    const url = `${API_BASE}/ohlcv/upload?${params}`;

    const res = await fetch(url, { method: 'POST', body: formData });

    if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || `Upload error ${res.status}`);
    }

    return res.json();
}

export async function getWatchlist() {
    const res = await fetch(`${API_BASE}/watchlist/`);
    return res.json();
}

export async function addToWatchlist(item) {
    const res = await fetch(`${API_BASE}/watchlist/`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(item),
    });
    return res.json();
}
