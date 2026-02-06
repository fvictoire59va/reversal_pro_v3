/**
 * Main application — wires UI controls to Chart and API.
 */

import { ChartManager } from './chart.js';
import { fetchChartData, fetchFromExchange, uploadCSV } from './api.js';

// ── State ───────────────────────────────────────────────────
let chart = null;
let currentSymbol = 'BTC/USDT';
let currentTimeframe = '1h';
let currentSensitivity = 'Medium';
let currentSignalMode = 'Confirmed Only';
let currentLimit = 500;
let isLiveMode = false;
let liveInterval = null;

// ── DOM refs ────────────────────────────────────────────────
const symbolSelect = document.getElementById('symbolSelect');
const timeframeButtons = document.getElementById('timeframeButtons');
const sensitivitySelect = document.getElementById('sensitivitySelect');
const signalModeSelect = document.getElementById('signalModeSelect');
const limitSelect = document.getElementById('limitSelect');
const liveBtn = document.getElementById('liveBtn');
const refreshBtn = document.getElementById('refreshBtn');
const fetchExchangeBtn = document.getElementById('fetchExchangeBtn');
const uploadBtn = document.getElementById('uploadBtn');
const csvFileInput = document.getElementById('csvFileInput');
const loadingOverlay = document.getElementById('loadingOverlay');
const statusText = document.getElementById('statusText');
const statusTime = document.getElementById('statusTime');
const signalsList = document.getElementById('signalsList');
const toggleSidebar = document.getElementById('toggleSidebar');
const sidebar = document.getElementById('sidebar');

// Position tool buttons
const longToolBtn = document.getElementById('longToolBtn');
const shortToolBtn = document.getElementById('shortToolBtn');
const clearPositionBtn = document.getElementById('clearPositionBtn');

// Info panel
const trendValue = document.getElementById('trendValue');
const atrValue = document.getElementById('atrValue');
const thresholdValue = document.getElementById('thresholdValue');
const signalsCount = document.getElementById('signalsCount');

// ── Initialize ──────────────────────────────────────────────
function init() {
    chart = new ChartManager('chartContainer');

    // Event listeners
    symbolSelect.addEventListener('change', (e) => {
        currentSymbol = e.target.value;
        loadChart();
    });

    timeframeButtons.addEventListener('click', (e) => {
        if (e.target.classList.contains('tf-btn')) {
            document.querySelectorAll('.tf-btn').forEach(b => b.classList.remove('active'));
            e.target.classList.add('active');
            currentTimeframe = e.target.dataset.tf;
            
            // Restart live mode with new timeframe interval
            if (isLiveMode) {
                stopLiveMode();
                startLiveMode();
            }
            
            loadChart();
        }
    });

    sensitivitySelect.addEventListener('change', (e) => {
        currentSensitivity = e.target.value;
        loadChart();
    });

    signalModeSelect.addEventListener('change', (e) => {
        currentSignalMode = e.target.value;
        loadChart();
    });

    limitSelect.addEventListener('change', (e) => {
        currentLimit = parseInt(e.target.value);
        loadChart();
    });

    liveBtn.addEventListener('click', toggleLiveMode);

    refreshBtn.addEventListener('click', () => loadChart());

    fetchExchangeBtn.addEventListener('click', () => fetchFromExchangeAndLoad());

    uploadBtn.addEventListener('click', () => csvFileInput.click());
    csvFileInput.addEventListener('change', handleCSVUpload);

    toggleSidebar.addEventListener('click', () => {
        sidebar.classList.toggle('collapsed');
    });

    // Position tool listeners
    longToolBtn.addEventListener('click', () => {
        longToolBtn.classList.toggle('active');
        shortToolBtn.classList.remove('active');
        
        if (longToolBtn.classList.contains('active')) {
            chart.activatePositionTool('LONG');
            setStatus('Click: 1) Entry, 2) Take Profit, 3) Stop Loss');
        } else {
            chart.deactivatePositionTool();
            setStatus('Position tool deactivated');
        }
    });

    shortToolBtn.addEventListener('click', () => {
        shortToolBtn.classList.toggle('active');
        longToolBtn.classList.remove('active');
        
        if (shortToolBtn.classList.contains('active')) {
            chart.activatePositionTool('SHORT');
            setStatus('Click: 1) Entry, 2) Take Profit, 3) Stop Loss');
        } else {
            chart.deactivatePositionTool();
            setStatus('Position tool deactivated');
        }
    });

    clearPositionBtn.addEventListener('click', () => {
        chart.clearPosition();
        longToolBtn.classList.remove('active');
        shortToolBtn.classList.remove('active');
        chart.deactivatePositionTool();
        setStatus('Position cleared');
    });

    // Initial load
    setStatus('Ready — click Refresh or Fetch to load data');

    // Auto-load after short delay
    setTimeout(() => loadChart(), 500);
}

// ── Load chart data ─────────────────────────────────────────
async function loadChart() {
    showLoading(true);
    setStatus(`Loading ${currentSymbol} ${currentTimeframe}...`);

    try {
        const data = await fetchChartData(
            currentSymbol,
            currentTimeframe,
            currentLimit,
            currentSensitivity,
            currentSignalMode,
        );

        chart.setData(data);
        updateInfoPanel(data);
        updateSignalsList(data.markers || []);
        setStatus(`${currentSymbol} ${currentTimeframe} — ${data.candles?.length || 0} bars loaded`);
    } catch (err) {
        setStatus(`Error: ${err.message}`, true);
        console.error('Load error:', err);
    } finally {
        showLoading(false);
    }
}

// ── Fetch from exchange ─────────────────────────────────────
async function fetchFromExchangeAndLoad() {
    showLoading(true);
    setStatus(`Fetching ${currentSymbol} from exchange...`);

    try {
        const result = await fetchFromExchange(
            currentSymbol, currentTimeframe, 'binance', currentLimit,
        );
        setStatus(`Fetched ${result.bars_stored} bars — running analysis...`);

        // Now load chart with analysis
        await loadChart();
    } catch (err) {
        setStatus(`Fetch error: ${err.message}`, true);
        showLoading(false);
    }
}

// ── CSV upload ──────────────────────────────────────────────
async function handleCSVUpload(e) {
    const file = e.target.files[0];
    if (!file) return;

    showLoading(true);
    setStatus(`Uploading ${file.name}...`);

    try {
        const result = await uploadCSV(file, currentSymbol, currentTimeframe);
        setStatus(`Uploaded ${result.bars_stored} bars — running analysis...`);
        await loadChart();
    } catch (err) {
        setStatus(`Upload error: ${err.message}`, true);
        showLoading(false);
    }

    csvFileInput.value = '';
}

// ── Update info panel ───────────────────────────────────────
function updateInfoPanel(data) {
    // Trend
    const trend = data.current_trend || 'N/A';
    trendValue.textContent = trend;
    trendValue.style.color =
        trend === 'BULLISH' ? '#00ff88' :
        trend === 'BEARISH' ? '#ff4466' : '#aa66ff';

    // ATR
    atrValue.textContent = data.current_atr?.toFixed(2) || '—';
    atrValue.style.color = '#00ddff';

    // Threshold
    thresholdValue.textContent = data.threshold?.toFixed(2) || '—';
    thresholdValue.style.color = '#ff8800';

    // Signals count
    const bullish = (data.markers || []).filter(m => m.shape === 'arrowUp').length;
    const bearish = (data.markers || []).filter(m => m.shape === 'arrowDown').length;
    signalsCount.innerHTML = `<span style="color:#00ff88">${bullish}▲</span> / <span style="color:#ff4466">${bearish}▼</span>`;
}

// ── Update signals list ─────────────────────────────────────
function updateSignalsList(markers) {
    if (!markers.length) {
        signalsList.innerHTML = '<div class="empty-state">No signals detected</div>';
        return;
    }

    // Show most recent first
    const sorted = [...markers].sort((a, b) => b.time - a.time);
    const html = sorted.map(m => {
        const isBull = m.shape === 'arrowUp';
        const cls = isBull ? 'bullish' : 'bearish';
        const arrow = isBull ? '▲' : '▼';
        const type = isBull ? 'BULLISH' : 'BEARISH';
        const date = new Date(m.time * 1000);
        const timeStr = date.toLocaleDateString('en-US', {
            month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
        });

        return `
            <div class="signal-card ${cls}">
                <div class="signal-header">
                    <span class="signal-type">${arrow} ${type}</span>
                    <span class="signal-time">${timeStr}</span>
                </div>
                <div class="signal-price">${m.text}</div>
            </div>
        `;
    }).join('');

    signalsList.innerHTML = html;
}

// ── Helpers ─────────────────────────────────────────────────
function showLoading(show) {
    loadingOverlay.classList.toggle('active', show);
}

function setStatus(text, isError = false) {
    statusText.textContent = text;
    statusText.style.color = isError ? '#ff4466' : '#888899';
    statusTime.textContent = new Date().toLocaleTimeString();
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
    isLiveMode = true;
    liveBtn.classList.add('active');
    
    const intervalMs = getTimeframeIntervalMs(currentTimeframe);
    const minutes = Math.floor(intervalMs / 60000);
    setStatus(`Live mode active — auto-refresh every ${minutes}m`);
    
    // Fetch immediately
    fetchFromExchangeAndLoad();
    
    // Then set interval
    liveInterval = setInterval(() => {
        if (isLiveMode) {
            fetchFromExchangeAndLoad();
        }
    }, intervalMs);
}

function stopLiveMode() {
    isLiveMode = false;
    liveBtn.classList.remove('active');
    
    if (liveInterval) {
        clearInterval(liveInterval);
        liveInterval = null;
    }
    
    setStatus('Live mode stopped');
}

function toggleLiveMode() {
    if (isLiveMode) {
        stopLiveMode();
    } else {
        startLiveMode();
    }
}

// ── Boot ────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', init);
