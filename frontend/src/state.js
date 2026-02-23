/**
 * Shared application state, DOM references and UI helpers.
 *
 * Every module that needs to read or mutate global state imports
 * the `state` object; mutations are visible everywhere because
 * the object reference is shared (ES module live binding).
 */

// ── Mutable application state ───────────────────────────────
export const state = {
    chart: null,
    currentSymbol: 'BTC/USDT',
    currentTimeframe: '1h',
    currentSensitivity: 'Medium',
    currentSignalMode: 'Confirmed Only',
    currentLimit: 500,
    // Engine params (synced from agent on TF switch)
    engineParams: {
        confirmation_bars: 0,
        method: 'average',
        atr_length: 5,
        average_length: 5,
        absolute_reversal: 0.5,
    },
    isLiveMode: false,
    liveInterval: null,
    knownSignalKeys: new Set(),
    isFirstLoad: true,
};

// ── Cached DOM references (safe — module scripts are deferred) ──
export const dom = {
    symbolSelect:      document.getElementById('symbolSelect'),
    timeframeButtons:  document.getElementById('timeframeButtons'),
    sensitivitySelect: document.getElementById('sensitivitySelect'),
    signalModeSelect:  document.getElementById('signalModeSelect'),
    limitSelect:       document.getElementById('limitSelect'),
    liveBtn:           document.getElementById('liveBtn'),
    refreshBtn:        document.getElementById('refreshBtn'),
    fetchExchangeBtn:  document.getElementById('fetchExchangeBtn'),
    uploadBtn:         document.getElementById('uploadBtn'),
    csvFileInput:      document.getElementById('csvFileInput'),
    loadingOverlay:    document.getElementById('loadingOverlay'),
    statusText:        document.getElementById('statusText'),
    statusTime:        document.getElementById('statusTime'),
    longToolBtn:       document.getElementById('longToolBtn'),
    shortToolBtn:      document.getElementById('shortToolBtn'),
    clearPositionBtn:  document.getElementById('clearPositionBtn'),
    trendValue:        document.getElementById('trendValue'),
    atrValue:          document.getElementById('atrValue'),
    thresholdValue:    document.getElementById('thresholdValue'),
    signalsCount:      document.getElementById('signalsCount'),
};

// ── UI helpers ──────────────────────────────────────────────
export function showLoading(show) {
    dom.loadingOverlay.classList.toggle('active', show);
}

export function setStatus(text, isError = false) {
    dom.statusText.textContent = text;
    dom.statusText.style.color = isError ? '#ff4466' : '#888899';
    dom.statusTime.textContent = new Date().toLocaleTimeString('fr-FR', {
        timeZone: 'Europe/Paris',
    });
}
