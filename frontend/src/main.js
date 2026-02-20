/**
 * Main application — wires UI controls to Chart and API.
 *
 * Split into focused modules:
 *   state.js         – shared state, DOM refs, UI helpers
 *   notifications.js – signal bell sound
 *   agentPanel.js    – agent CRUD, positions table
 *   perfTree.js      – performance tree view
 */

import { ChartManager } from './chart.js';
import {
    fetchChartData, fetchFromExchange, uploadCSV,
    getAgentPositionsForChart, getSkippedSignalsForChart,
} from './api.js';
import { state, dom, showLoading, setStatus } from './state.js';
import { checkNewSignals } from './notifications.js';
import { initAgentBroker } from './agentPanel.js';
import { initPerfTree } from './perfTree.js';

// ── Initialize ──────────────────────────────────────────────
function init() {
    state.chart = new ChartManager('chartContainer');

    // Event listeners
    dom.symbolSelect.addEventListener('change', (e) => {
        state.currentSymbol = e.target.value;
        loadChart();
    });

    dom.timeframeButtons.addEventListener('click', (e) => {
        if (e.target.classList.contains('tf-btn')) {
            document.querySelectorAll('.tf-btn').forEach(b => b.classList.remove('active'));
            e.target.classList.add('active');
            state.currentTimeframe = e.target.dataset.tf;
            
            // Restart live mode with new timeframe interval
            if (state.isLiveMode) {
                stopLiveMode();
                startLiveMode(); // startLiveMode already calls fetchFromExchangeAndLoad
            } else {
                loadChart();
            }
        }
    });

    dom.sensitivitySelect.addEventListener('change', (e) => {
        state.currentSensitivity = e.target.value;
        loadChart();
    });

    dom.signalModeSelect.addEventListener('change', (e) => {
        state.currentSignalMode = e.target.value;
        loadChart();
    });

    dom.limitSelect.addEventListener('change', (e) => {
        state.currentLimit = parseInt(e.target.value);
        loadChart();
    });

    dom.liveBtn.addEventListener('click', toggleLiveMode);

    dom.refreshBtn.addEventListener('click', () => loadChart());

    dom.fetchExchangeBtn.addEventListener('click', () => fetchFromExchangeAndLoad());

    dom.uploadBtn.addEventListener('click', () => dom.csvFileInput.click());
    dom.csvFileInput.addEventListener('change', handleCSVUpload);

    // Position tool listeners
    dom.longToolBtn.addEventListener('click', () => {
        dom.longToolBtn.classList.toggle('active');
        dom.shortToolBtn.classList.remove('active');
        
        if (dom.longToolBtn.classList.contains('active')) {
            state.chart.activatePositionTool('LONG');
            setStatus('Click: 1) Entry, 2) Take Profit, 3) Stop Loss');
        } else {
            state.chart.deactivatePositionTool();
            setStatus('Position tool deactivated');
        }
    });

    dom.shortToolBtn.addEventListener('click', () => {
        dom.shortToolBtn.classList.toggle('active');
        dom.longToolBtn.classList.remove('active');
        
        if (dom.shortToolBtn.classList.contains('active')) {
            state.chart.activatePositionTool('SHORT');
            setStatus('Click: 1) Entry, 2) Take Profit, 3) Stop Loss');
        } else {
            state.chart.deactivatePositionTool();
            setStatus('Position tool deactivated');
        }
    });

    dom.clearPositionBtn.addEventListener('click', () => {
        state.chart.clearPosition();
        dom.longToolBtn.classList.remove('active');
        dom.shortToolBtn.classList.remove('active');
        state.chart.deactivatePositionTool();
        setStatus('Position cleared');
    });

    // Initial load
    setStatus('Ready — click Refresh or Fetch to load data');

    // Auto-start live mode after short delay
    setTimeout(() => startLiveMode(), 500);

    // ── Agent Broker init ───────────────────────────────────
    initAgentBroker();

    // ── Performance Tree init ───────────────────────────────
    initPerfTree();
}

// ── Load chart data ─────────────────────────────────────────
let isLoading = false;
let isLoadingTimeout = null;  // Safety timeout to reset isLoading flag
let loadRetryCount = 0;
const MAX_RETRIES = 3;
const RETRY_DELAY_MS = 2000;
let hasChartData = false; // Track if chart ever received data

function resetLoadingState() {
    isLoading = false;
    showLoading(false);
    if (isLoadingTimeout) {
        clearTimeout(isLoadingTimeout);
        isLoadingTimeout = null;
    }
}

function showChartError(message) {
    let overlay = document.getElementById('chartErrorOverlay');
    if (!overlay) {
        overlay = document.createElement('div');
        overlay.id = 'chartErrorOverlay';
        overlay.style.cssText = `
            position: absolute; top: 0; left: 0; right: 0; bottom: 0;
            display: flex; flex-direction: column; align-items: center; justify-content: center;
            background: rgba(255,255,255,0.92); z-index: 10; gap: 12px;
            font-family: 'JetBrains Mono', monospace; text-align: center;
        `;
        document.querySelector('.chart-wrapper').appendChild(overlay);
    }
    overlay.innerHTML = `
        <div style="font-size: 36px;">⚠️</div>
        <div style="font-size: 14px; color: #606873; max-width: 300px;">${message}</div>
        <button onclick="this.parentElement.remove(); loadChart()" 
                style="padding: 6px 16px; border: 1px solid #00aa55; background: #00aa55; 
                       color: white; border-radius: 4px; cursor: pointer; font-size: 12px;">
            ⟳ Réessayer
        </button>
    `;
    overlay.style.display = 'flex';
}

function hideChartError() {
    const overlay = document.getElementById('chartErrorOverlay');
    if (overlay) overlay.style.display = 'none';
}

async function loadChart(retryOnFail = true) {
    // Prevent multiple simultaneous loads
    if (isLoading) {
        console.log('Load already in progress, skipping...');
        return;
    }
    
    isLoading = true;
    showLoading(true);
    setStatus(`Loading ${state.currentSymbol} ${state.currentTimeframe}...`);
    
    // Safety: auto-reset isLoading after 30s to prevent permanent lock
    isLoadingTimeout = setTimeout(() => {
        if (isLoading) {
            console.warn('Loading timeout — resetting state');
            resetLoadingState();
            setStatus('Timeout — retrying...', true);
            if (retryOnFail && loadRetryCount < MAX_RETRIES) {
                loadRetryCount++;
                setTimeout(() => loadChart(true), RETRY_DELAY_MS);
            }
        }
    }, 30000);

    try {
        const data = await fetchChartData(
            state.currentSymbol,
            state.currentTimeframe,
            state.currentLimit,
            state.currentSensitivity,
            state.currentSignalMode,
        );

        state.chart.setData(data);
        updateInfoPanel(data);
        checkNewSignals(data);
        hideChartError();
        hasChartData = true;
        loadRetryCount = 0; // Reset retry counter on success
        
        // Load and display agent positions for this chart
        try {
            const [posResp, skipResp] = await Promise.all([
                getAgentPositionsForChart(state.currentSymbol, state.currentTimeframe),
                getSkippedSignalsForChart(state.currentSymbol, state.currentTimeframe),
            ]);
            state.chart.showAgentPositions(posResp.positions || []);
            state.chart.showSkippedSignals(skipResp.skipped_signals || []);
        } catch (posErr) {
            console.warn('Could not load agent positions:', posErr);
        }
        
        setStatus(`${state.currentSymbol} ${state.currentTimeframe} — ${data.candles?.length || 0} bars loaded`);
    } catch (err) {
        // Check if error is due to missing data
        if (err.message.includes('No OHLCV data found') || err.message.includes('404')) {
            const msg = `No data for ${state.currentSymbol} ${state.currentTimeframe}`;
            setStatus(`${msg}. Click "Fetch" to load from exchange.`, true);
            
            // Reset loading state before showing prompt
            resetLoadingState();
            
            // Auto-prompt to fetch data (only on first encounter, not in live loop)
            if (!hasChartData && confirm(`${msg}.\n\nWould you like to fetch it from the exchange now?`)) {
                await fetchFromExchangeAndLoad();
            } else if (!hasChartData) {
                showChartError(`Aucune donnée pour ${state.currentSymbol} ${state.currentTimeframe}.<br>Cliquez sur "Fetch" pour charger.`);
            }
            return;
        } else {
            const errMsg = `Erreur: ${err.message}`;
            setStatus(errMsg, true);
            console.error('Load error:', err);
            
            // Retry with backoff
            if (retryOnFail && loadRetryCount < MAX_RETRIES) {
                loadRetryCount++;
                const delay = RETRY_DELAY_MS * loadRetryCount;
                setStatus(`${errMsg} — retry ${loadRetryCount}/${MAX_RETRIES} dans ${delay/1000}s...`, true);
                resetLoadingState();
                setTimeout(() => loadChart(true), delay);
                return;
            } else if (!hasChartData) {
                showChartError(`Impossible de charger les données.<br>${err.message}`);
            }
        }
    } finally {
        resetLoadingState();
    }
}

// ── Fetch from exchange ─────────────────────────────────────
async function fetchFromExchangeAndLoad() {
    if (isLoading) {
        console.log('Already loading, skipping fetch...');
        return;
    }
    
    isLoading = true;
    showLoading(true);
    setStatus(`Fetching ${state.currentSymbol} ${state.currentTimeframe} from exchange...`);

    try {
        const result = await fetchFromExchange(
            state.currentSymbol, state.currentTimeframe, 'binance', state.currentLimit,
        );
        setStatus(`Fetched ${result.bars_stored} bars — running analysis...`);

        // Reset loading flag to allow loadChart to proceed
        resetLoadingState();
        
        // Now load chart with analysis
        await loadChart();
    } catch (err) {
        console.error('Fetch error:', err);
        resetLoadingState();
        
        // If fetch fails but we have DB data, fall back to loadChart
        setStatus(`Fetch error: ${err.message} — loading cached data...`, true);
        try {
            await loadChart(false); // Don't retry to avoid loop
        } catch (loadErr) {
            setStatus(`Erreur: ${err.message}`, true);
            if (!hasChartData) {
                showChartError(`Impossible de récupérer les données.<br>${err.message}`);
            }
        }
    }
}

// ── CSV upload ──────────────────────────────────────────────
async function handleCSVUpload(e) {
    const file = e.target.files[0];
    if (!file) return;

    showLoading(true);
    setStatus(`Uploading ${file.name}...`);

    try {
        const result = await uploadCSV(file, state.currentSymbol, state.currentTimeframe);
        setStatus(`Uploaded ${result.bars_stored} bars — running analysis...`);
        await loadChart();
    } catch (err) {
        setStatus(`Upload error: ${err.message}`, true);
        showLoading(false);
    }

    dom.csvFileInput.value = '';
}

// ── Update info panel ───────────────────────────────────────
function updateInfoPanel(data) {
    // Trend
    const trend = data.current_trend || 'N/A';
    dom.trendValue.textContent = trend;
    dom.trendValue.style.color =
        trend === 'BULLISH' ? '#00ff88' :
        trend === 'BEARISH' ? '#ff4466' : '#aa66ff';

    // ATR
    dom.atrValue.textContent = data.current_atr?.toFixed(2) || '—';
    dom.atrValue.style.color = '#00ddff';

    // Threshold
    dom.thresholdValue.textContent = data.threshold?.toFixed(2) || '—';
    dom.thresholdValue.style.color = '#ff8800';

    // Signals count
    const bullish = (data.markers || []).filter(m => m.shape === 'arrowUp').length;
    const bearish = (data.markers || []).filter(m => m.shape === 'arrowDown').length;
    dom.signalsCount.innerHTML = `<span style="color:#00ff88">${bullish}▲</span> / <span style="color:#ff4466">${bearish}▼</span>`;
}

// ── Live Mode ───────────────────────────────────────────────
function getTimeframeIntervalMs(timeframe) {
    const map = {
        '1m': 60000,
        '5m': 300000,
        '15m': 900000,
        '1h': 3600000,
        '4h': 14400000,
        '1d': 86400000,
    };
    return map[timeframe] || 60000;
}

function startLiveMode() {
    state.isLiveMode = true;
    dom.liveBtn.classList.add('active');
    
    const intervalMs = getTimeframeIntervalMs(state.currentTimeframe);
    const minutes = Math.floor(intervalMs / 60000);
    setStatus(`Live mode active — auto-refresh every ${minutes}m`);
    
    // Fetch immediately
    fetchFromExchangeAndLoad();
    
    // Then set interval
    state.liveInterval = setInterval(() => {
        if (state.isLiveMode) {
            fetchFromExchangeAndLoad();
        }
    }, intervalMs);
}

function stopLiveMode() {
    state.isLiveMode = false;
    dom.liveBtn.classList.remove('active');
    
    if (state.liveInterval) {
        clearInterval(state.liveInterval);
        state.liveInterval = null;
    }
    
    setStatus('Live mode stopped');
}

function toggleLiveMode() {
    if (state.isLiveMode) {
        stopLiveMode();
    } else {
        startLiveMode();
    }
}

// ── Boot ────────────────────────────────────────────────────
// Expose loadChart globally for the chart error overlay retry button
window.loadChart = (...args) => loadChart(...args);
document.addEventListener('DOMContentLoaded', init);