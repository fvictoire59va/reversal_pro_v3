/**
 * Performance Tree panel — hierarchical agent performance view
 * grouped by side, date and status.
 */

import { getAgentPerformance } from './api.js';

// ── Private ─────────────────────────────────────────────────
let perfTreeInterval = null;

// ── Public API ──────────────────────────────────────────────

export function initPerfTree() {
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

export function updatePerfAgentSelect(agents) {
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

// ── Tree rendering ──────────────────────────────────────────

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

// ── Helpers ─────────────────────────────────────────────────

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
