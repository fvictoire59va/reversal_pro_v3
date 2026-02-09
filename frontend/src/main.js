/**
 * Main application — wires UI controls to Chart and API.
 */

import { ChartManager } from './chart.js';
import {
    fetchChartData, fetchFromExchange, uploadCSV,
    getAgentsOverview, createAgent, deleteAgent, toggleAgent, closePosition,
    getAgentPositionsForChart,
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
}

// ── Load chart data ─────────────────────────────────────────
let isLoading = false;

async function loadChart() {
    // Prevent multiple simultaneous loads
    if (isLoading) {
        console.log('Load already in progress, skipping...');
        return;
    }
    
    isLoading = true;
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
        checkNewSignals(data);
        
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
            showLoading(false);
            isLoading = false;
            
            // Auto-prompt to fetch data
            if (confirm(`${msg}.\n\nWould you like to fetch it from the exchange now?`)) {
                await fetchFromExchangeAndLoad();
            }
            return;
        } else {
            setStatus(`Error: ${err.message}`, true);
        }
        console.error('Load error:', err);
    } finally {
        showLoading(false);
        isLoading = false;
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
        isLoading = false;
        
        // Now load chart with analysis
        await loadChart();
    } catch (err) {
        setStatus(`Fetch error: ${err.message}`, true);
        showLoading(false);
        isLoading = false;
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

// ── Boot ────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', init);
