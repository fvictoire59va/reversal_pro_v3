/**
 * Main application — wires UI controls to Chart and API.
 */

import { ChartManager } from './chart.js';
import {
    fetchChartData, fetchFromExchange, uploadCSV,
    getAgentsOverview, createAgent, deleteAgent, toggleAgent, closePosition,
    getAgentPositionsForChart, getAgentPerformance,
} from './api.js';

// ── State ───────────────────────────────────────────────────
let chart = null;
let currentSymbol = 'BTC/USDT';
let currentTimeframe = '1m';
let currentSensitivity = 'Low';
let currentSignalMode = 'Confirmed Only';
let currentLimit = 500;
let isLiveMode = true;
let liveInterval = null;
let knownSignalKeys = new Set(); // Track known signal keys to detect new ones
let isFirstLoad = true;          // Skip bell on initial load

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
                startLiveMode(); // startLiveMode already calls fetchFromExchangeAndLoad
            } else {
                loadChart();
            }
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
    setStatus(`Loading ${currentSymbol} ${currentTimeframe}...`);
    
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
            currentSymbol,
            currentTimeframe,
            currentLimit,
            currentSensitivity,
            currentSignalMode,
        );

        chart.setData(data);
        updateInfoPanel(data);
        checkNewSignals(data);
        hideChartError();
        hasChartData = true;
        loadRetryCount = 0; // Reset retry counter on success
        
        // Load and display agent positions for this chart
        try {
            const resp = await getAgentPositionsForChart(currentSymbol, currentTimeframe);
            chart.showAgentPositions(resp.positions || []);
        } catch (posErr) {
            console.warn('Could not load agent positions:', posErr);
        }
        
        setStatus(`${currentSymbol} ${currentTimeframe} — ${data.candles?.length || 0} bars loaded`);
    } catch (err) {
        // Check if error is due to missing data
        if (err.message.includes('No OHLCV data found') || err.message.includes('404')) {
            const msg = `No data for ${currentSymbol} ${currentTimeframe}`;
            setStatus(`${msg}. Click "Fetch" to load from exchange.`, true);
            
            // Reset loading state before showing prompt
            resetLoadingState();
            
            // Auto-prompt to fetch data (only on first encounter, not in live loop)
            if (!hasChartData && confirm(`${msg}.\n\nWould you like to fetch it from the exchange now?`)) {
                await fetchFromExchangeAndLoad();
            } else if (!hasChartData) {
                showChartError(`Aucune donnée pour ${currentSymbol} ${currentTimeframe}.<br>Cliquez sur "Fetch" pour charger.`);
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
    setStatus(`Fetching ${currentSymbol} ${currentTimeframe} from exchange...`);

    try {
        const result = await fetchFromExchange(
            currentSymbol, currentTimeframe, 'binance', currentLimit,
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
        const result = await uploadCSV(file, currentSymbol, currentTimeframe);
        setStatus(`Uploaded ${result.bars_stored} bars — running analysis...`);
        await loadChart();
    } catch (err) {
        setStatus(`Upload error: ${err.message}`, true);
        showLoading(false);
    }

    csvFileInput.value = '';
}

// ── Signal sound notification ───────────────────────────────
function playBellSound() {
    try {
        const ctx = new (window.AudioContext || window.webkitAudioContext)();
        const now = ctx.currentTime;

        // Bell-like tone: two short chimes
        [0, 0.15].forEach(offset => {
            const osc = ctx.createOscillator();
            const gain = ctx.createGain();
            osc.type = 'sine';
            osc.frequency.setValueAtTime(1200, now + offset);
            osc.frequency.exponentialRampToValueAtTime(800, now + offset + 0.3);
            gain.gain.setValueAtTime(0.35, now + offset);
            gain.gain.exponentialRampToValueAtTime(0.001, now + offset + 0.4);
            osc.connect(gain).connect(ctx.destination);
            osc.start(now + offset);
            osc.stop(now + offset + 0.4);
        });

        // Clean up context after sound finishes
        setTimeout(() => ctx.close(), 1000);
    } catch (e) {
        console.warn('Could not play bell sound:', e);
    }
}

function checkNewSignals(data) {
    const markers = data.markers || [];
    const candles = data.candles || [];
    // Use time + direction as stable key (price/text can shift on recalc)
    const currentKeys = new Set(
        markers.map(m => `${m.time}_${m.shape}`)
    );

    if (isFirstLoad) {
        // First load — just memorize, no sound
        knownSignalKeys = currentKeys;
        isFirstLoad = false;
        return;
    }

    // Determine the recent time threshold (last 3 candle intervals)
    // Only alert for truly new signals on recent candles, not old ones
    // that appear/disappear due to the sliding window edge
    let recentThreshold = 0;
    if (candles.length >= 2) {
        const interval = candles[candles.length - 1].time - candles[candles.length - 2].time;
        recentThreshold = candles[candles.length - 1].time - interval * 3;
    }

    // Only trigger on signals we haven't seen AND that are recent
    let newSignals = 0;
    for (const key of currentKeys) {
        if (!knownSignalKeys.has(key)) {
            const sigTime = parseInt(key.split('_')[0], 10);
            if (sigTime >= recentThreshold) {
                newSignals++;
            }
        }
    }

    if (newSignals > 0) {
        playBellSound();
        console.log(`🔔 ${newSignals} new signal(s) detected!`);
    }

    knownSignalKeys = currentKeys;
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

// ── Helpers ─────────────────────────────────────────────────
function showLoading(show) {
    loadingOverlay.classList.toggle('active', show);
}

function setStatus(text, isError = false) {
    statusText.textContent = text;
    statusText.style.color = isError ? '#ff4466' : '#888899';
    statusTime.textContent = new Date().toLocaleTimeString('fr-FR', { 
        timeZone: 'Europe/Paris' 
    });
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

// ── Agent Broker ────────────────────────────────────────────
let agentRefreshInterval = null;

function initAgentBroker() {
    const toggleCreateBtn = document.getElementById('toggleCreateAgent');
    const createForm = document.getElementById('createAgentForm');
    const createBtn = document.getElementById('createAgentBtn');
    const cancelBtn = document.getElementById('cancelCreateAgent');
    const refreshAgentsBtn = document.getElementById('refreshAgentsBtn');

    toggleCreateBtn.addEventListener('click', () => {
        const isVisible = createForm.style.display !== 'none';
        if (isVisible) {
            createForm.style.display = 'none';
        } else {
            // Copy current chart parameters to form
            document.getElementById('agentSymbol').value = currentSymbol;
            document.getElementById('agentTimeframe').value = currentTimeframe;
            document.getElementById('agentSensitivity').value = currentSensitivity;
            document.getElementById('agentSignalMode').value = currentSignalMode;
            document.getElementById('agentLimit').value = currentLimit.toString();
            createForm.style.display = 'block';
        }
    });

    cancelBtn.addEventListener('click', () => {
        createForm.style.display = 'none';
    });

    createBtn.addEventListener('click', handleCreateAgent);
    refreshAgentsBtn.addEventListener('click', loadAgentsOverview);

    // Initial load + auto-refresh every 30s
    setTimeout(() => loadAgentsOverview(), 1000);
    agentRefreshInterval = setInterval(loadAgentsOverview, 30000);
}

async function handleCreateAgent() {
    const symbol = document.getElementById('agentSymbol').value;
    const timeframe = document.getElementById('agentTimeframe').value;
    const amount = parseFloat(document.getElementById('agentAmount').value);
    const mode = document.getElementById('agentMode').value;
    const sensitivity = document.getElementById('agentSensitivity').value;
    const signal_mode = document.getElementById('agentSignalMode').value;
    const analysis_limit = parseInt(document.getElementById('agentLimit').value);

    try {
        await createAgent({ 
            symbol, 
            timeframe, 
            trade_amount: amount, 
            mode,
            sensitivity,
            signal_mode,
            analysis_limit
        });
        document.getElementById('createAgentForm').style.display = 'none';
        setStatus(`Agent created: ${symbol} ${timeframe} (${mode}, ${sensitivity})`);
        await loadAgentsOverview();
    } catch (err) {
        setStatus(`Error creating agent: ${err.message}`, true);
    }
}

async function handleToggleAgent(agentId) {
    try {
        await toggleAgent(agentId);
        await loadAgentsOverview();
    } catch (err) {
        setStatus(`Error toggling agent: ${err.message}`, true);
    }
}

async function handleDeleteAgent(agentId, agentName) {
    if (!confirm(`Supprimer ${agentName} ? Les positions ouvertes seront fermées.`)) return;
    try {
        await deleteAgent(agentId);
        setStatus(`Agent ${agentName} supprimé`);
        await loadAgentsOverview();
    } catch (err) {
        setStatus(`Error deleting agent: ${err.message}`, true);
    }
}

async function handleClosePosition(positionId) {
    if (!confirm('Clôturer cette position manuellement ?')) return;
    try {
        const result = await closePosition(positionId);
        const pnlStr = result.pnl >= 0 ? `+${result.pnl.toFixed(4)}` : result.pnl.toFixed(4);
        setStatus(`Position fermée — PnL: ${pnlStr} USDT`);
        await loadAgentsOverview();
    } catch (err) {
        setStatus(`Error closing position: ${err.message}`, true);
    }
}

async function loadAgentsOverview() {
    try {
        const data = await getAgentsOverview();
        renderAgentsList(data.agents);
        renderPositionsTable(data.open_positions);
        updateAgentBadges(data);
        updatePerfAgentSelect(data.agents);
        
        // Also refresh agent positions on current chart
        if (chart) {
            try {
                const resp = await getAgentPositionsForChart(currentSymbol, currentTimeframe);
                chart.showAgentPositions(resp.positions || []);
            } catch (posErr) {
                console.warn('Could not refresh chart positions:', posErr);
            }
        }
    } catch (err) {
        console.error('Failed to load agents:', err);
    }
}

function updateAgentBadges(data) {
    const activeBadge = document.getElementById('activeAgentsBadge');
    const pnlBadge = document.getElementById('totalPnlBadge');

    activeBadge.textContent = `${data.active_agents} active`;

    const pnl = data.total_realized_pnl;
    const sign = pnl >= 0 ? '+' : '';
    // Sum unrealized PnL from all agents
    const totalUnrealized = data.agents.reduce((sum, a) => sum + (a.total_unrealized_pnl || 0), 0);
    const uSign = totalUnrealized >= 0 ? '+' : '';
    const uPnlText = data.total_open_positions > 0 ? ` | Potentiel: ${uSign}${totalUnrealized.toFixed(2)}` : '';
    pnlBadge.textContent = `PnL: ${sign}${pnl.toFixed(2)}${uPnlText} USDT`;
    pnlBadge.style.color = pnl > 0 ? 'var(--green)' : pnl < 0 ? 'var(--red)' : 'var(--text-muted)';
}

function renderAgentsList(agents) {
    const container = document.getElementById('agentsList');

    if (!agents.length) {
        container.innerHTML = '<div class="empty-state" style="padding:10px;">Aucun agent — cliquez "+ Agent" pour créer</div>';
        return;
    }

    const html = agents.map(agent => {
        const statusClass = agent.is_active ? 'active' : '';
        const cardClass = agent.is_active ? '' : 'inactive';
        const modeClass = agent.mode === 'paper' ? 'paper' : 'live';
        const modeLabel = agent.mode === 'paper' ? '📄 PAPER' : '🔴 LIVE';
        const toggleLabel = agent.is_active ? '⏸' : '▶';
        const toggleTitle = agent.is_active ? 'Désactiver' : 'Activer';

        const pnl = agent.total_pnl || 0;
        const pnlClass = pnl > 0 ? 'positive' : pnl < 0 ? 'negative' : 'neutral';
        const pnlSign = pnl >= 0 ? '+' : '';

        const uPnl = agent.total_unrealized_pnl || 0;
        const uPnlClass = uPnl > 0 ? 'positive' : uPnl < 0 ? 'negative' : 'neutral';
        const uPnlSign = uPnl >= 0 ? '+' : '';
        const uPnlHtml = agent.open_positions > 0 ? `<span class="agent-pnl ${uPnlClass}" title="PnL potentiel">(${uPnlSign}${uPnl.toFixed(2)})</span>` : '';

        return `
            <div class="agent-card ${cardClass}">
                <span class="agent-status-dot ${statusClass}"></span>
                <span class="agent-name">${agent.name}</span>
                <span class="agent-info">${agent.symbol} ${agent.timeframe}</span>
                <span class="agent-mode-badge ${modeClass}">${modeLabel}</span>
                <span class="agent-info">${agent.trade_amount}€</span>
                <span class="agent-info">Solde: ${(agent.balance || 0).toFixed(2)}€</span>
                <span class="agent-pnl ${pnlClass}">${pnlSign}${pnl.toFixed(2)}</span>
                ${uPnlHtml}
                <span class="agent-info">(${agent.open_positions} pos)</span>
                <div class="agent-actions">
                    <button onclick="window._handleToggleAgent(${agent.id})" title="${toggleTitle}">${toggleLabel}</button>
                    <button class="btn-delete" onclick="window._handleDeleteAgent(${agent.id}, '${agent.name}')" title="Supprimer">✕</button>
                </div>
            </div>
        `;
    }).join('');

    container.innerHTML = html;
}

function renderPositionsTable(positions) {
    const tbody = document.getElementById('positionsBody');

    if (!positions.length) {
        tbody.innerHTML = '<tr class="empty-row"><td colspan="12">Aucune position ouverte</td></tr>';
        return;
    }

    const html = positions.map(pos => {
        const sideClass = pos.side === 'LONG' ? 'side-long' : 'side-short';
        const sideIcon = pos.side === 'LONG' ? '▲' : '▼';

        // Calculate duration
        const opened = new Date(pos.opened_at);
        const now = new Date();
        const diffMs = now - opened;
        const hours = Math.floor(diffMs / 3600000);
        const minutes = Math.floor((diffMs % 3600000) / 60000);
        const duration = hours > 0 ? `${hours}h${minutes.toString().padStart(2, '0')}m` : `${minutes}m`;

        // Format opened date and time (Paris timezone)
        const openedDate = opened.toLocaleDateString('fr-FR', { 
            day: '2-digit', 
            month: '2-digit', 
            year: 'numeric',
            timeZone: 'Europe/Paris',
        });
        const openedTime = opened.toLocaleTimeString('fr-FR', { 
            hour: '2-digit', 
            minute: '2-digit',
            timeZone: 'Europe/Paris',
        });
        const openedDateTime = `${openedDate}<br/>${openedTime}`;

        // Unrealized PnL
        const uPnl = pos.unrealized_pnl;
        const uPnlPct = pos.unrealized_pnl_percent;
        let pnlCell = '—';
        let pnlCellClass = '';
        if (uPnl !== null && uPnl !== undefined) {
            const uSign = uPnl >= 0 ? '+' : '';
            pnlCellClass = uPnl > 0 ? 'pnl-positive' : uPnl < 0 ? 'pnl-negative' : '';
            pnlCell = `${uSign}${uPnl.toFixed(4)}<br/><small>${uSign}${(uPnlPct || 0).toFixed(2)}%</small>`;
        }

        // Current price
        const curPrice = pos.current_price
            ? pos.current_price.toLocaleString('fr-FR', { minimumFractionDigits: 2 })
            : '—';

        return `
            <tr>
                <td>${pos.agent_name}</td>
                <td class="${sideClass}">${sideIcon} ${pos.side}</td>
                <td>${pos.symbol}</td>
                <td>${pos.entry_price.toLocaleString('fr-FR', { minimumFractionDigits: 2 })}</td>
                <td>${curPrice}</td>
                <td style="color:var(--red)">${pos.stop_loss.toLocaleString('fr-FR', { minimumFractionDigits: 2 })}</td>
                <td style="color:var(--green)">${pos.take_profit ? pos.take_profit.toLocaleString('fr-FR', { minimumFractionDigits: 2 }) : '—'}</td>
                <td>${pos.quantity.toFixed(6)}</td>
                <td class="${pnlCellClass}">${pnlCell}</td>
                <td>${openedDateTime}</td>
                <td>${duration}</td>
                <td><button class="btn-close-position" onclick="window._handleClosePosition(${pos.id})">Clôturer</button></td>
            </tr>
        `;
    }).join('');

    tbody.innerHTML = html;
}

// Expose handlers to window for inline onclick
window._handleToggleAgent = handleToggleAgent;
window._handleDeleteAgent = handleDeleteAgent;
window._handleClosePosition = handleClosePosition;

// ── Performance Tree Panel ──────────────────────────────────

let perfTreeInterval = null;

function initPerfTree() {
    const panel = document.getElementById('perfTreePanel');
    const toggleBtn = document.getElementById('perfTreeToggle');
    const agentSelect = document.getElementById('perfAgentSelect');

    toggleBtn.addEventListener('click', () => {
        panel.classList.toggle('collapsed');
    });

    agentSelect.addEventListener('change', () => {
        const agentId = agentSelect.value;
        if (agentId) {
            loadPerfTree(parseInt(agentId));
        } else {
            document.getElementById('perfTreeBody').innerHTML =
                '<div class="perf-tree-empty">Sélectionner un agent</div>';
        }
    });

    // Refresh perf tree every 30s if an agent is selected
    perfTreeInterval = setInterval(() => {
        const agentId = agentSelect.value;
        if (agentId) loadPerfTree(parseInt(agentId));
    }, 30000);
}

function updatePerfAgentSelect(agents) {
    const select = document.getElementById('perfAgentSelect');
    const currentVal = select.value;
    const options = '<option value="">— Agent —</option>' +
        agents.map(a => `<option value="${a.id}">${a.name}</option>`).join('');
    select.innerHTML = options;
    // Restore selection or auto-select if only one agent
    if (currentVal && agents.some(a => a.id === parseInt(currentVal))) {
        select.value = currentVal;
    } else if (agents.length === 1) {
        select.value = agents[0].id;
        loadPerfTree(agents[0].id);
    }
}

async function loadPerfTree(agentId) {
    const body = document.getElementById('perfTreeBody');
    try {
        const data = await getAgentPerformance(agentId);
        if (!data) {
            body.innerHTML = '<div class="perf-tree-empty">Agent introuvable</div>';
            return;
        }
        renderPerfTree(body, data);
    } catch (err) {
        console.error('Failed to load perf tree:', err);
        body.innerHTML = '<div class="perf-tree-empty">Erreur de chargement</div>';
    }
}

function renderPerfTree(container, data) {
    const { agent, summary, by_side, by_date, by_status } = data;

    const pnlColor = summary.total_pnl >= 0 ? 'green' : 'red';
    const pnlSign = summary.total_pnl >= 0 ? '+' : '';
    const uPnlSign = summary.unrealized_pnl >= 0 ? '+' : '';
    const uPnlColor = summary.unrealized_pnl >= 0 ? 'green' : 'red';

    let html = '';

    // ── Summary card ──
    html += `
        <div class="perf-summary">
            <div class="perf-summary-title">
                🤖 ${agent.name}
                <span style="font-size:9px;color:var(--text-muted)">${agent.symbol} · ${agent.timeframe} · ${agent.mode}</span>
            </div>
            <div class="perf-summary-pnl ${pnlColor}">${pnlSign}${summary.total_pnl.toFixed(2)}€</div>
            <div class="perf-summary-row">
                <span class="perf-summary-label">Non réalisé</span>
                <span class="perf-summary-value" style="color:var(--${uPnlColor})">${uPnlSign}${summary.unrealized_pnl.toFixed(2)}€</span>
            </div>
            <div class="perf-summary-row">
                <span class="perf-summary-label">Capital</span>
                <span class="perf-summary-value">${agent.trade_amount.toFixed(0)}€</span>
            </div>
            <div class="perf-summary-row">
                <span class="perf-summary-label">Solde</span>
                <span class="perf-summary-value">${agent.balance.toFixed(2)}€</span>
            </div>
            <div class="perf-summary-row">
                <span class="perf-summary-label">Win Rate</span>
                <span class="perf-summary-value">${summary.win_rate}%</span>
            </div>
            <div class="perf-summary-row">
                <span class="perf-summary-label">Trades</span>
                <span class="perf-summary-value">${summary.closed_count || 0} clos · ${summary.open_count || 0} ouv</span>
            </div>
            <div class="perf-summary-row">
                <span class="perf-summary-label">Meilleur</span>
                <span class="perf-summary-value green">${summary.best >= 0 ? '+' : ''}${summary.best.toFixed(2)}€</span>
            </div>
            <div class="perf-summary-row">
                <span class="perf-summary-label">Pire</span>
                <span class="perf-summary-value red">${summary.worst.toFixed(2)}€</span>
            </div>
            <div class="perf-summary-row">
                <span class="perf-summary-label">Durée moy</span>
                <span class="perf-summary-value">${formatDuration(summary.avg_duration_min)}</span>
            </div>
        </div>
    `;

    // ── By Side node ──
    html += buildSideNode('LONG', '▲', by_side.LONG);
    html += buildSideNode('SHORT', '▼', by_side.SHORT);

    // ── By Date node ──
    html += `<div class="perf-node">
        <div class="perf-node-header" onclick="this.parentElement.classList.toggle('open')">
            <span class="perf-node-arrow">▶</span>
            <span class="perf-node-icon">📅</span>
            <span class="perf-node-label">Par date</span>
            <span class="perf-node-badge neutral">${by_date.length}j</span>
        </div>
        <div class="perf-node-children">`;

    for (const day of by_date) {
        const dayPnl = day.stats.pnl;
        const daySign = dayPnl >= 0 ? '+' : '';
        const dayClass = dayPnl > 0 ? 'positive' : dayPnl < 0 ? 'negative' : 'neutral';
        const dateLabel = formatDateLabel(day.date);

        html += `<div class="perf-node">
            <div class="perf-node-header" onclick="this.parentElement.classList.toggle('open')">
                <span class="perf-node-arrow">▶</span>
                <span class="perf-node-label">${dateLabel}</span>
                <span class="perf-node-badge ${dayClass}">${daySign}${dayPnl.toFixed(2)}€</span>
            </div>
            <div class="perf-node-children">
                ${buildStatsBlock(day.stats)}
                ${day.positions.map(p => buildPositionLeaf(p)).join('')}
            </div>
        </div>`;
    }

    html += `</div></div>`; // close by_date

    // ── By Status node ──
    html += `<div class="perf-node">
        <div class="perf-node-header" onclick="this.parentElement.classList.toggle('open')">
            <span class="perf-node-arrow">▶</span>
            <span class="perf-node-icon">📋</span>
            <span class="perf-node-label">Par statut</span>
        </div>
        <div class="perf-node-children">`;

    for (const [status, label, icon] of [['OPEN', 'Ouvertes', '🟢'], ['CLOSED', 'Fermées', '✅'], ['STOPPED', 'Stoppées', '🛑']]) {
        const st = by_status[status];
        if (st.stats.count > 0) {
            const stPnl = st.stats.pnl;
            const stSign = stPnl >= 0 ? '+' : '';
            const stClass = stPnl > 0 ? 'positive' : stPnl < 0 ? 'negative' : 'neutral';
            html += `<div class="perf-node">
                <div class="perf-node-header" onclick="this.parentElement.classList.toggle('open')">
                    <span class="perf-node-arrow">▶</span>
                    <span class="perf-node-icon">${icon}</span>
                    <span class="perf-node-label">${label}</span>
                    <span class="perf-node-badge ${stClass}">${st.stats.count} · ${stSign}${stPnl.toFixed(2)}€</span>
                </div>
                <div class="perf-node-children">
                    ${buildStatsBlock(st.stats)}
                    ${st.positions.map(p => buildPositionLeaf(p)).join('')}
                </div>
            </div>`;
        }
    }

    html += `</div></div>`; // close by_status

    container.innerHTML = html;
}

function buildSideNode(side, icon, data) {
    const pnl = data.stats.pnl;
    const sign = pnl >= 0 ? '+' : '';
    const pnlClass = pnl > 0 ? 'positive' : pnl < 0 ? 'negative' : 'neutral';
    const sideColor = side === 'LONG' ? '#00cc66' : '#ff4466';

    return `<div class="perf-node">
        <div class="perf-node-header" onclick="this.parentElement.classList.toggle('open')">
            <span class="perf-node-arrow">▶</span>
            <span class="perf-node-icon" style="color:${sideColor}">${icon}</span>
            <span class="perf-node-label" style="color:${sideColor}">${side}</span>
            <span class="perf-node-badge ${pnlClass}">${data.stats.count} · ${sign}${pnl.toFixed(2)}€</span>
        </div>
        <div class="perf-node-children">
            ${buildStatsBlock(data.stats)}
            ${data.positions.map(p => buildPositionLeaf(p)).join('')}
        </div>
    </div>`;
}

function buildStatsBlock(stats) {
    return `<div class="perf-stats">
        <div class="perf-stat">
            <span class="perf-stat-label">Trades</span>
            <span class="perf-stat-value">${stats.closed_count || stats.count || 0}</span>
        </div>
        <div class="perf-stat">
            <span class="perf-stat-label">Win Rate</span>
            <span class="perf-stat-value">${stats.win_rate}%</span>
        </div>
        <div class="perf-stat">
            <span class="perf-stat-label">Gagnants</span>
            <span class="perf-stat-value green">${stats.wins}</span>
        </div>
        <div class="perf-stat">
            <span class="perf-stat-label">Perdants</span>
            <span class="perf-stat-value red">${stats.losses}</span>
        </div>
        <div class="perf-stat">
            <span class="perf-stat-label">PnL moy</span>
            <span class="perf-stat-value ${stats.avg_pnl >= 0 ? 'green' : 'red'}">${stats.avg_pnl >= 0 ? '+' : ''}${stats.avg_pnl.toFixed(2)}€</span>
        </div>
        <div class="perf-stat">
            <span class="perf-stat-label">Durée moy</span>
            <span class="perf-stat-value">${formatDuration(stats.avg_duration_min)}</span>
        </div>
    </div>`;
}

function buildPositionLeaf(pos) {
    const sideClass = pos.side === 'LONG' ? 'long' : 'short';
    const time = pos.opened_at ? new Date(pos.opened_at).toLocaleTimeString('fr-FR', {
        hour: '2-digit', minute: '2-digit', timeZone: 'Europe/Paris',
    }) : '';

    let pnlText = '';
    let pnlColor = '';
    if (pos.status === 'OPEN') {
        const u = pos.unrealized_pnl || 0;
        pnlText = `${u >= 0 ? '+' : ''}${u.toFixed(2)}€`;
        pnlColor = u >= 0 ? 'green' : 'red';
    } else if (pos.pnl !== null) {
        pnlText = `${pos.pnl >= 0 ? '+' : ''}${pos.pnl.toFixed(2)}€`;
        pnlColor = pos.pnl >= 0 ? 'green' : 'red';
    }

    const statusIcon = pos.status === 'OPEN' ? '🟢' : pos.status === 'STOPPED' ? '🛑' : '✅';

    return `<div class="perf-position">
        <div class="perf-position-header">
            <span class="perf-position-side ${sideClass}">${pos.side}</span>
            <span class="perf-position-time">${time}</span>
            <span style="margin-left:auto">${statusIcon}</span>
        </div>
        <div class="perf-position-detail">
            <span>${pos.entry_price.toLocaleString('fr-FR', {minimumFractionDigits:2})}${pos.exit_price ? ' → ' + pos.exit_price.toLocaleString('fr-FR', {minimumFractionDigits:2}) : ''}</span>
            <span class="perf-position-pnl" style="color:var(--${pnlColor})">${pnlText}</span>
        </div>
    </div>`;
}

function formatDuration(minutes) {
    if (!minutes || minutes <= 0) return '—';
    if (minutes < 60) return `${Math.round(minutes)}m`;
    const h = Math.floor(minutes / 60);
    const m = Math.round(minutes % 60);
    return `${h}h${m.toString().padStart(2, '0')}m`;
}

function formatDateLabel(dateStr) {
    const d = new Date(dateStr + 'T00:00:00');
    return d.toLocaleDateString('fr-FR', { weekday: 'short', day: '2-digit', month: '2-digit' });
}

// ── Boot ────────────────────────────────────────────────────
// Expose loadChart globally for the chart error overlay retry button
window.loadChart = (...args) => loadChart(...args);
document.addEventListener('DOMContentLoaded', init);
