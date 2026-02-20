/**
 * Agent Broker panel — create / toggle / delete agents,
 * display agent cards, open-positions table.
 */

import { state, setStatus } from './state.js';
import {
    getAgentsOverview, createAgent, deleteAgent, toggleAgent,
    closePosition, getAgentPositionsForChart,
} from './api.js';
import { updatePerfAgentSelect } from './perfTree.js';
import { esc, escAttr } from './escapeHtml.js';

// ── Private ─────────────────────────────────────────────────
let agentRefreshInterval = null;

// ── Public API ──────────────────────────────────────────────

export function initAgentBroker() {
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
            document.getElementById('agentSymbol').value = state.currentSymbol;
            document.getElementById('agentTimeframe').value = state.currentTimeframe;
            document.getElementById('agentSensitivity').value = state.currentSensitivity;
            document.getElementById('agentSignalMode').value = state.currentSignalMode;
            document.getElementById('agentLimit').value = state.currentLimit.toString();
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
            analysis_limit,
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

export async function loadAgentsOverview() {
    try {
        const data = await getAgentsOverview();
        renderAgentsList(data.agents, data.open_positions);
        renderPositionsTable(data.open_positions);
        updateAgentBadges(data);
        updatePerfAgentSelect(data.agents);

        // Also refresh agent positions on current chart
        if (state.chart) {
            try {
                const resp = await getAgentPositionsForChart(
                    state.currentSymbol, state.currentTimeframe,
                );
                state.chart.showAgentPositions(resp.positions || []);
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

function renderAgentsList(agents, openPositions = []) {
    const container = document.getElementById('agentsList');

    if (!agents.length) {
        container.innerHTML = '<div class="empty-state" style="padding:10px;">Aucun agent — cliquez "+ Agent" pour créer</div>';
        return;
    }

    // Build a map of agent_id -> position side
    const agentSideMap = {};
    for (const pos of openPositions) {
        agentSideMap[pos.agent_id] = pos.side;
    }

    const html = agents.map(agent => {
        const statusClass = agent.is_active ? 'active' : '';
        // Determine card color class based on state
        let cardClass = '';
        if (!agent.is_active) {
            cardClass = 'agent-inactive';
        } else if (agentSideMap[agent.id] === 'LONG') {
            cardClass = 'agent-long';
        } else if (agentSideMap[agent.id] === 'SHORT') {
            cardClass = 'agent-short';
        } else if (agent.total_pnl > 0) {
            cardClass = 'agent-profit';
        } else if (agent.total_pnl < 0) {
            cardClass = 'agent-loss';
        } else {
            cardClass = 'agent-idle';
        }
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
                <div class="agent-card-header">
                    <span class="agent-status-dot ${statusClass}"></span>
                    <span class="agent-name">${esc(agent.name)}</span>
                    <span class="agent-mode-badge ${modeClass}">${modeLabel}</span>
                    <div class="agent-actions">
                        <button onclick="window._handleToggleAgent(${agent.id})" title="${toggleTitle}">${toggleLabel}</button>
                        <button class="btn-delete" onclick="window._handleDeleteAgent(${agent.id}, '${escAttr(agent.name)}')" title="Supprimer">✕</button>
                    </div>
                </div>
                <div class="agent-card-details">
                    <span class="agent-info">${esc(agent.symbol)} ${esc(agent.timeframe)}</span>
                    <span class="agent-info">${agent.trade_amount}€</span>
                    <span class="agent-info">Solde: ${(agent.balance || 0).toFixed(2)}€</span>
                </div>
                <div class="agent-card-params">
                    <span class="agent-param" title="Sensitivity">🎯 ${agent.sensitivity || 'Medium'}</span>
                    <span class="agent-param" title="Signal Mode">📡 ${agent.signal_mode || 'Confirmed Only'}</span>
                    <span class="agent-param" title="Analysis Bars">📊 ${agent.analysis_limit || 500}</span>
                </div>
                <div class="agent-card-pnl">
                    <span class="agent-pnl ${pnlClass}">PnL: ${pnlSign}${pnl.toFixed(2)}</span>
                    ${uPnlHtml}
                    <span class="agent-info">${agent.open_positions} pos</span>
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

        // Progression: breakeven, partial TP info
        let progressCell = '';
        const isBreakeven = (pos.side === 'LONG' && pos.stop_loss >= pos.entry_price)
            || (pos.side === 'SHORT' && pos.stop_loss <= pos.entry_price);
        if (pos.partial_closed) {
            const partialSign = (pos.partial_pnl || 0) >= 0 ? '+' : '';
            progressCell += `<span style="color:var(--green)">✓ TP1 50%</span><br/>`;
            progressCell += `<small style="color:var(--green)">${partialSign}${(pos.partial_pnl || 0).toFixed(2)}€ sécurisé</small><br/>`;
            progressCell += `<small>TP2: ${pos.take_profit ? pos.take_profit.toLocaleString('fr-FR', {minimumFractionDigits: 2}) : '—'}</small>`;
        } else if (isBreakeven) {
            progressCell += `<span style="color:var(--blue)">🛡 Breakeven</span><br/>`;
            if (pos.tp2) {
                progressCell += `<small>TP1: ${pos.take_profit ? pos.take_profit.toLocaleString('fr-FR', {minimumFractionDigits: 2}) : '—'}</small><br/>`;
                progressCell += `<small>TP2: ${pos.tp2.toLocaleString('fr-FR', {minimumFractionDigits: 2})}</small>`;
            }
        } else {
            progressCell = '<small style="color:var(--muted)">En attente</small>';
            if (pos.tp2) {
                progressCell += `<br/><small>TP1: ${pos.take_profit ? pos.take_profit.toLocaleString('fr-FR', {minimumFractionDigits: 2}) : '—'}</small>`;
                progressCell += `<br/><small>TP2: ${pos.tp2.toLocaleString('fr-FR', {minimumFractionDigits: 2})}</small>`;
            }
        }

        // TP column: show current active TP target
        const tpDisplay = pos.take_profit ? pos.take_profit.toLocaleString('fr-FR', { minimumFractionDigits: 2 }) : '—';
        const tpLabel = pos.partial_closed ? 'TP2' : (pos.tp2 ? 'TP1' : 'TP');

        return `
            <tr>
                <td>${esc(pos.agent_name)}</td>
                <td class="${sideClass}">${sideIcon} ${pos.side}</td>
                <td>${esc(pos.symbol)}</td>
                <td>${pos.entry_price.toLocaleString('fr-FR', { minimumFractionDigits: 2 })}</td>
                <td>${curPrice}</td>
                <td style="color:var(--red)">${pos.stop_loss.toLocaleString('fr-FR', { minimumFractionDigits: 2 })}${isBreakeven ? '<br/><small>🛡 BE</small>' : ''}</td>
                <td style="color:var(--green)">${tpDisplay}<br/><small>${tpLabel}</small></td>
                <td>${pos.quantity.toFixed(6)}${pos.partial_closed ? '<br/><small>(50% restant)</small>' : ''}</td>
                <td>${progressCell}</td>
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
