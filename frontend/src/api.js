/**
 * API client — communicates with the FastAPI backend.
 */

const API_BASE = '/api';

export async function fetchChartData(symbol, timeframe, limit, sensitivity, signalMode) {
    const params = new URLSearchParams({
        limit: String(limit),
        sensitivity: sensitivity,
        signal_mode: signalMode,
        _t: Date.now(), // Cache-busting parameter
    });

    // Replace / with - for URL compatibility
    const urlSymbol = symbol.replace('/', '-');
    const url = `${API_BASE}/analysis/chart/${urlSymbol}/${timeframe}?${params}`;
    const res = await fetch(url, {
        cache: 'no-store', // Disable browser cache
    });

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

// ── Agent Broker API ────────────────────────────────────────

export async function getAgentsOverview() {
    const res = await fetch(`${API_BASE}/agents/`);
    if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || `API error ${res.status}`);
    }
    return res.json();
}

export async function createAgent(data) {
    const res = await fetch(`${API_BASE}/agents/`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
    });
    if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || `Create error ${res.status}`);
    }
    return res.json();
}

export async function deleteAgent(agentId) {
    const res = await fetch(`${API_BASE}/agents/${agentId}`, {
        method: 'DELETE',
    });
    if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || `Delete error ${res.status}`);
    }
    return res.json();
}

export async function toggleAgent(agentId) {
    const res = await fetch(`${API_BASE}/agents/${agentId}/toggle`, {
        method: 'PATCH',
    });
    if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || `Toggle error ${res.status}`);
    }
    return res.json();
}

export async function closePosition(positionId) {
    const res = await fetch(`${API_BASE}/agents/positions/${positionId}/close`, {
        method: 'POST',
    });
    if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || `Close error ${res.status}`);
    }
    return res.json();
}

export async function getAgentLogs(agentId, limit = 50) {
    const res = await fetch(`${API_BASE}/agents/${agentId}/logs?limit=${limit}`);
    if (!res.ok) return [];
    return res.json();
}

export async function getAgentPerformance(agentId) {
    const res = await fetch(`${API_BASE}/agents/${agentId}/performance`);
    if (!res.ok) return null;
    return res.json();
}

export async function getAgentPositionsForChart(symbol, timeframe) {
    const urlSymbol = symbol.replace('/', '-');
    const res = await fetch(`${API_BASE}/agents/positions-by-chart/${urlSymbol}/${timeframe}`);
    if (!res.ok) return { positions: [] };
    return res.json();
}

export async function getSkippedSignalsForChart(symbol, timeframe) {
    const urlSymbol = symbol.replace('/', '-');
    const res = await fetch(`${API_BASE}/agents/skipped-signals/${urlSymbol}/${timeframe}`);
    if (!res.ok) return { skipped_signals: [] };
    return res.json();
}
