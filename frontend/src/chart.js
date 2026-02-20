/**
 * Chart Manager — TradingView Lightweight Charts integration.
 * Renders candlesticks, EMAs, supply/demand zones, and reversal markers.
 */

import { createChart, CrosshairMode, LineStyle } from 'lightweight-charts';\nimport { esc } from './escapeHtml.js';

/**
 * Shift a UTC Unix timestamp (seconds) so lightweight-charts displays it as Paris time.
 * lightweight-charts always renders timestamps as UTC, so we add the Paris offset.
 */
function utcToParisTimestamp(utcTimestamp) {
    const date = new Date(utcTimestamp * 1000);
    const parisStr = date.toLocaleString('en-US', { timeZone: 'Europe/Paris' });
    const utcStr = date.toLocaleString('en-US', { timeZone: 'UTC' });
    const offsetMs = new Date(parisStr) - new Date(utcStr);
    return utcTimestamp + Math.round(offsetMs / 1000);
}

export class ChartManager {
    constructor(containerId) {
        this.container = document.getElementById(containerId);
        this.chart = null;
        this.candleSeries = null;
        this.ema9Series = null;
        this.ema14Series = null;
        this.ema21Series = null;
        this.volumeSeries = null;
        this.zonePriceLines = [];
        this.agentPositionLines = []; // Store agent position lines
        this.agentZoneSeries = [];    // TP/SL colored zone series
        this.signalMarkers = [];      // Reversal signal markers (from analysis)
        this.agentMarkers = [];       // Agent position entry markers
        this.skippedMarkers = [];     // Skipped signal markers (grey)
        this.signalMeta = {};         // Signal metadata keyed by Paris-shifted time
        this.agentMeta = {};          // Agent marker metadata keyed by Paris-shifted time
        this.skippedMeta = {};        // Skipped signal metadata keyed by Paris-shifted time
        this.lastCandleTime = null;   // Last candle time (Paris-shifted)
        
        // Position Tool state
        this.positionTool = {
            active: false,
            mode: null, // 'LONG' or 'SHORT'
            step: 0, // 0: entry, 1: TP, 2: SL
            entry: null,
            tp: null,
            sl: null,
            lines: [],
        };
        // Bound reference for proper subscribe/unsubscribe
        this._boundPositionClick = this._handlePositionClick.bind(this);
        
        this._init();
    }

    _init() {
        this.chart = createChart(this.container, {
            layout: {
                background: { color: '#ffffff' },
                textColor: '#606873',
                fontFamily: "'JetBrains Mono', monospace",
                fontSize: 11,
            },
            grid: {
                vertLines: { color: '#e8eef5' },
                horzLines: { color: '#e8eef5' },
            },
            crosshair: {
                mode: CrosshairMode.Normal,
                vertLine: {
                    color: '#00aa5555',
                    width: 1,
                    style: LineStyle.Dashed,
                    labelBackgroundColor: '#00aa55',
                },
                horzLine: {
                    color: '#00aa5555',
                    width: 1,
                    style: LineStyle.Dashed,
                    labelBackgroundColor: '#00aa55',
                },
            },
            rightPriceScale: {
                borderColor: '#d0d7de',
                scaleMargins: { top: 0.05, bottom: 0.15 },
            },
            timeScale: {
                borderColor: '#d0d7de',
                timeVisible: true,
                secondsVisible: false,
                barSpacing: 8,
                rightOffset: 15,
            },
            handleScroll: true,
            handleScale: true,
        });

        // ── Candlestick series ──
        this.candleSeries = this.chart.addCandlestickSeries({
            upColor: '#00aa55',
            downColor: '#dd3344',
            borderUpColor: '#00aa55',
            borderDownColor: '#dd3344',
            wickUpColor: '#00aa5588',
            wickDownColor: '#dd334488',
        });

        // ── Volume histogram ──
        this.volumeSeries = this.chart.addHistogramSeries({
            priceFormat: { type: 'volume' },
            priceScaleId: 'volume',
        });
        this.chart.priceScale('volume').applyOptions({
            scaleMargins: { top: 0.85, bottom: 0 },
        });

        // ── EMA lines ──
        this.ema9Series = this.chart.addLineSeries({
            color: '#FFD700',
            lineWidth: 1,
            crosshairMarkerVisible: false,
            priceLineVisible: false,
            lastValueVisible: false,
            title: 'EMA 9',
        });

        this.ema14Series = this.chart.addLineSeries({
            color: '#00BFFF',
            lineWidth: 1,
            crosshairMarkerVisible: false,
            priceLineVisible: false,
            lastValueVisible: false,
            title: 'EMA 14',
        });

        this.ema21Series = this.chart.addLineSeries({
            color: '#FF69B4',
            lineWidth: 1,
            crosshairMarkerVisible: false,
            priceLineVisible: false,
            lastValueVisible: false,
            title: 'EMA 21',
        });

        // ── Resize observer ──
        this._resizeObserver = new ResizeObserver(entries => {
            const { width, height } = entries[0].contentRect;
            this.chart.applyOptions({ width, height });
        });
        this._resizeObserver.observe(this.container);

        // ── Signal tooltip on crosshair move ──
        this._tooltipEl = document.getElementById('signalTooltip');
        this._agentTooltipEl = document.getElementById('agentTooltip');
        this._skippedTooltipEl = document.getElementById('skippedTooltip');
        this.chart.subscribeCrosshairMove(param => this._handleCrosshairMove(param));
    }

    /**
     * Show/hide the signal detection tooltip when crosshair is on a signal candle.
     * Also shows agent position tooltip when crosshair is on an agent marker.
     * Also shows skipped signal tooltip when crosshair is on a grey skipped marker.
     */
    _handleCrosshairMove(param) {
        const tooltip = this._tooltipEl;
        const agentTooltip = this._agentTooltipEl;
        const skippedTooltip = this._skippedTooltipEl;

        if (!param.time || !param.point || param.point.x < 0 || param.point.y < 0) {
            if (tooltip) tooltip.style.display = 'none';
            if (agentTooltip) agentTooltip.style.display = 'none';
            if (skippedTooltip) skippedTooltip.style.display = 'none';
            return;
        }

        // ── Agent position tooltip (priority over signal tooltip) ──
        const agentMetaList = this.agentMeta[param.time];
        if (agentMetaList && agentMetaList.length > 0 && agentTooltip) {
            if (tooltip) tooltip.style.display = 'none';
            if (skippedTooltip) skippedTooltip.style.display = 'none';

            const headerEl = document.getElementById('agentTooltipHeader');
            const bodyEl = document.getElementById('agentTooltipBody');

            let html = '';
            for (const meta of agentMetaList) {
                if (meta.type === 'entry') {
                    html += this._buildEntryTooltipHTML(meta);
                } else if (meta.type === 'exit') {
                    html += this._buildExitTooltipHTML(meta);
                }
            }

            headerEl.textContent = agentMetaList.length === 1
                ? agentMetaList[0].agentName
                : `${agentMetaList.length} positions`;
            headerEl.style.color = agentMetaList[0].color || '#e0e6f0';
            bodyEl.innerHTML = html;

            this._positionTooltip(agentTooltip, param);
            agentTooltip.style.display = 'block';
            return;
        }

        if (agentTooltip) agentTooltip.style.display = 'none';

        // ── Skipped signal tooltip ──
        const skippedMetaList = this.skippedMeta[param.time];
        if (skippedMetaList && skippedMetaList.length > 0 && skippedTooltip) {
            if (tooltip) tooltip.style.display = 'none';

            const headerEl = document.getElementById('skippedTooltipHeader');
            const bodyEl = document.getElementById('skippedTooltipBody');

            let html = '';
            for (const meta of skippedMetaList) {
                html += this._buildSkippedTooltipHTML(meta);
            }

            const count = skippedMetaList.length;
            headerEl.textContent = count === 1
                ? `⊘ Signal ignoré`
                : `⊘ ${count} signaux ignorés`;
            headerEl.style.color = '#888888';
            bodyEl.innerHTML = html;

            this._positionTooltip(skippedTooltip, param);
            skippedTooltip.style.display = 'block';
            return;
        }

        if (skippedTooltip) skippedTooltip.style.display = 'none';

        // ── Signal tooltip ──
        if (!tooltip) return;
        const meta = this.signalMeta[param.time];
        if (!meta) {
            tooltip.style.display = 'none';
            return;
        }

        // Fill tooltip content
        const headerEl = document.getElementById('signalTooltipHeader');
        const detectedEl = document.getElementById('signalTooltipDetectedAt');
        const delayEl = document.getElementById('signalTooltipDelay');

        const dirLabel = meta.isBullish ? '▲ BULLISH' : '▼ BEARISH';
        const dirColor = meta.isBullish ? '#00aa55' : '#dd3344';
        headerEl.textContent = dirLabel;
        headerEl.style.color = dirColor;

        // Format detected_at to Paris time
        if (meta.detectedAt) {
            const dt = new Date(meta.detectedAt);
            detectedEl.textContent = dt.toLocaleString('fr-FR', {
                timeZone: 'Europe/Paris',
                day: '2-digit', month: '2-digit', year: 'numeric',
                hour: '2-digit', minute: '2-digit', second: '2-digit',
            });
        } else {
            detectedEl.textContent = '—';
        }

        delayEl.textContent = meta.candlesDelay != null
            ? `${meta.candlesDelay} bougie${meta.candlesDelay > 1 ? 's' : ''}`
            : '—';

        // Position tooltip near the crosshair
        this._positionTooltip(tooltip, param);
        tooltip.style.display = 'block';
    }

    /**
     * Position a tooltip element near the crosshair point, keeping it inside chart bounds.
     */
    _positionTooltip(tooltipEl, param) {
        const chartRect = this.container.getBoundingClientRect();
        let left = param.point.x + 16;
        let top = param.point.y - 10;

        const tooltipW = tooltipEl.offsetWidth || 260;
        const tooltipH = tooltipEl.offsetHeight || 120;
        if (left + tooltipW > chartRect.width) {
            left = param.point.x - tooltipW - 16;
        }
        if (top + tooltipH > chartRect.height) {
            top = chartRect.height - tooltipH - 8;
        }
        if (top < 0) top = 8;

        tooltipEl.style.left = `${left}px`;
        tooltipEl.style.top = `${top}px`;
    }

    /**
     * Build tooltip HTML for an agent ENTRY marker.
     */
    _buildEntryTooltipHTML(meta) {
        const sideColor = meta.side === 'LONG' ? '#0088dd' : '#dd8800';
        const sideIcon = meta.side === 'LONG' ? '▲' : '▼';
        const modeLabel = meta.mode === 'live' ? '🔴 LIVE' : '📝 Paper';
        let html = `<div class="tooltip-section">`;
        html += `<div style="color:${sideColor}; font-weight:700; font-size:12px;">${sideIcon} ${meta.side}</div>`;
        html += this._tooltipRow('Entrée', `${meta.entryPrice?.toFixed(2)}`);
        if (meta.stopLoss != null) html += this._tooltipRow('Stop Loss', `${meta.stopLoss.toFixed(2)}`);
        if (meta.tp1 != null) html += this._tooltipRow('TP1', `${meta.tp1.toFixed(2)}`);
        if (meta.tp2 != null) html += this._tooltipRow('TP2', `${meta.tp2.toFixed(2)}`);
        if (meta.risk != null) html += this._tooltipRow('Risque', `${meta.risk.toFixed(2)}`);
        if (meta.rrRatio != null) html += this._tooltipRow('R:R', `1:${meta.rrRatio.toFixed(1)}`);
        if (meta.zoneTpUsed) html += this._tooltipRow('Zone TP', '✓ S/D zone');
        html += this._tooltipRow('Mode', modeLabel);
        if (meta.openedAt) {
            const dt = new Date(meta.openedAt);
            html += this._tooltipRow('Ouvert le', dt.toLocaleString('fr-FR', {
                timeZone: 'Europe/Paris', day: '2-digit', month: '2-digit',
                hour: '2-digit', minute: '2-digit', second: '2-digit',
            }));
        }
        html += `</div>`;
        return html;
    }

    /**
     * Build tooltip HTML for an agent EXIT marker (SL / TP / signal close).
     */
    _buildExitTooltipHTML(meta) {
        const reasonLabels = {
            'STOP_LOSS': 'Stop Loss',
            'TAKE_PROFIT': 'Take Profit',
            'TAKE_PROFIT_2': 'Take Profit 2',
            'BULLISH_REVERSAL': 'Reversal Bullish',
            'BEARISH_REVERSAL': 'Reversal Bearish',
            'MANUAL_CLOSE': 'Fermeture manuelle',
            'PARTIAL_TP1': 'TP1 Partiel (50%)',
            'SIGNAL': 'Signal opposé',
        };

        const reasonClasses = {
            'STOP_LOSS': 'reason-sl',
            'TAKE_PROFIT': 'reason-tp',
            'TAKE_PROFIT_2': 'reason-tp',
            'BULLISH_REVERSAL': 'reason-signal',
            'BEARISH_REVERSAL': 'reason-signal',
            'MANUAL_CLOSE': 'reason-manual',
            'PARTIAL_TP1': 'reason-tp',
            'SIGNAL': 'reason-signal',
        };

        const reasonText = reasonLabels[meta.closeReason] || esc(meta.closeReason) || 'Inconnue';
        const reasonClass = reasonClasses[meta.closeReason] || 'reason-manual';

        const pnlClass = meta.pnl > 0 ? 'tooltip-pnl-positive' : 'tooltip-pnl-negative';
        const pnlSign = meta.pnl > 0 ? '+' : '';

        let html = `<div class="tooltip-section">`;
        html += `<span class="tooltip-reason ${reasonClass}">${esc(reasonText)}</span>`;
        html += this._tooltipRow('Entrée', `${meta.entryPrice?.toFixed(2)}`);
        html += this._tooltipRow('Sortie', `${meta.exitPrice?.toFixed(2)}`);
        if (meta.pnl != null) {
            html += `<div class="signal-tooltip-row">
                <span class="signal-tooltip-label">PnL :</span>
                <span class="signal-tooltip-value ${pnlClass}">${pnlSign}${meta.pnl.toFixed(2)}€ (${pnlSign}${meta.pnlPercent?.toFixed(2) || '0'}%)</span>
            </div>`;
        }
        if (meta.originalSl != null && meta.originalSl !== meta.stopLoss) {
            html += this._tooltipRow('SL initial', `${meta.originalSl.toFixed(2)}`);
            html += this._tooltipRow('SL final', `${meta.stopLoss?.toFixed(2)}`);
        }
        if (meta.partialClosed) {
            html += this._tooltipRow('Partial TP', `✓ 50% fermé`);
            if (meta.partialPnl != null) html += this._tooltipRow('PnL partiel', `${meta.partialPnl.toFixed(2)}€`);
        }
        // Duration
        if (meta.openedAt && meta.closedAt) {
            const durationMs = new Date(meta.closedAt) - new Date(meta.openedAt);
            const hours = Math.floor(durationMs / 3600000);
            const mins = Math.floor((durationMs % 3600000) / 60000);
            html += this._tooltipRow('Durée', hours > 0 ? `${hours}h ${mins}m` : `${mins}m`);
        }
        html += `</div>`;
        return html;
    }

    /**
     * Helper to create a tooltip row.
     */
    _tooltipRow(label, value) {
        return `<div class="signal-tooltip-row">
            <span class="signal-tooltip-label">${esc(label)} :</span>
            <span class="signal-tooltip-value">${esc(value)}</span>
        </div>`;
    }

    /**
     * Build tooltip HTML for a SKIPPED signal marker.
     */
    _buildSkippedTooltipHTML(meta) {
        const reasonLabels = {
            'risk_too_small': 'Risque trop faible',
            'pivot_momentum_against': 'Momentum pivot contraire',
            'htf_trend_against': 'Tendance HTF contraire',
            'ema_trend_against': 'Tendance EMA contraire',
            'signal_stale': 'Signal obsolète',
            'no_balance': 'Solde insuffisant',
            'whipsaw_cooldown': 'Cooldown anti-whipsaw',
        };

        const reasonDescriptions = {
            'risk_too_small': 'Le SL est trop proche du prix d\'entrée, rendant le trade non rentable.',
            'pivot_momentum_against': 'Les 3 derniers pivots indiquent un momentum contraire à la direction du signal.',
            'htf_trend_against': 'La tendance sur le timeframe supérieur est opposée au signal.',
            'ema_trend_against': 'La tendance EMA sur le timeframe actuel est opposée au signal.',
            'signal_stale': 'Le signal a été détecté il y a trop longtemps pour être encore actionnable.',
            'no_balance': 'L\'agent n\'a plus de capital disponible pour ouvrir une position.',
            'whipsaw_cooldown': 'La position précédente a été trop courte, cooldown actif pour éviter le whipsaw.',
        };

        const sideColor = meta.side === 'LONG' ? '#0088dd' : '#dd8800';
        const sideIcon = meta.side === 'LONG' ? '▲' : '▼';
        const reasonText = reasonLabels[meta.reason] || meta.reason || 'Inconnue';
        const reasonDesc = reasonDescriptions[meta.reason] || '';

        let html = `<div class="tooltip-section">`;
        html += `<div style="color:${sideColor}; font-weight:700; font-size:12px; margin-bottom:4px;">${sideIcon} ${meta.side}</div>`;
        html += `<span class="tooltip-reason reason-skipped">${reasonText}</span>`;
        if (reasonDesc) {
            html += `<div class="tooltip-reason-desc">${reasonDesc}</div>`;
        }
        html += this._tooltipRow('Agent', meta.agentName);
        if (meta.entryPrice != null) html += this._tooltipRow('Prix', `${meta.entryPrice.toFixed(2)}`);
        if (meta.stopLoss != null) html += this._tooltipRow('SL calculé', `${meta.stopLoss.toFixed(2)}`);
        if (meta.riskPct != null) html += this._tooltipRow('Risque', `${meta.riskPct.toFixed(4)}%`);
        if (meta.htfChecked && meta.htfChecked.length > 0) html += this._tooltipRow('HTF vérifiés', meta.htfChecked.join(', '));
        if (meta.balance != null) html += this._tooltipRow('Solde', `${meta.balance.toFixed(2)}€`);
        if (meta.positionDurationS != null) {
            const mins = Math.floor(meta.positionDurationS / 60);
            const secs = meta.positionDurationS % 60;
            html += this._tooltipRow('Durée pos.', mins > 0 ? `${mins}m ${secs}s` : `${secs}s`);
        }
        if (meta.minGapS != null) {
            html += this._tooltipRow('Cooldown min.', `${Math.floor(meta.minGapS / 60)}m`);
        }
        if (meta.skippedAt) {
            const dt = new Date(meta.skippedAt);
            html += this._tooltipRow('Ignoré le', dt.toLocaleString('fr-FR', {
                timeZone: 'Europe/Paris', day: '2-digit', month: '2-digit',
                hour: '2-digit', minute: '2-digit', second: '2-digit',
            }));
        }
        html += `</div>`;
        return html;
    }

    /**
     * Load complete chart data from the API response.
     */
    setData(data) {
        // Shift all timestamps to Paris timezone for display
        const shiftTime = (item) => ({ ...item, time: utcToParisTimestamp(item.time) });

        // Candles
        let shiftedCandles = [];
        if (data.candles?.length) {
            shiftedCandles = data.candles.map(shiftTime);
            this.candleSeries.setData(shiftedCandles);
            this.lastCandleTime = shiftedCandles[shiftedCandles.length - 1].time;
            // Store candle timestamps and interval for zone alignment
            this.candleTimes = new Set(shiftedCandles.map(c => c.time));
            if (shiftedCandles.length >= 2) {
                this.candleInterval = shiftedCandles[1].time - shiftedCandles[0].time;
            }
        }

        // Volume (derive from candles — API could add volumes later)
        if (data.candles?.length) {
            const volumeData = data.candles.map(c => ({
                time: utcToParisTimestamp(c.time),
                value: 0,  // placeholder
                color: c.close >= c.open ? '#00ff8833' : '#ff446633',
            }));
            this.volumeSeries.setData(volumeData);
        }

        // EMAs
        if (data.ema_9?.length) this.ema9Series.setData(data.ema_9.map(shiftTime));
        if (data.ema_14?.length) this.ema14Series.setData(data.ema_14.map(shiftTime));
        if (data.ema_21?.length) this.ema21Series.setData(data.ema_21.map(shiftTime));

        // Markers (reversal signals) — store separately for agent overlay
        this.signalMarkers = data.markers?.length ? data.markers.map(shiftTime) : [];
        this.agentMarkers = []; // Reset agent markers when new data loads
        this.skippedMarkers = []; // Reset skipped markers when new data loads
        this.skippedMeta = {};    // Reset skipped metadata

        // Build signal metadata map keyed by Paris-shifted time
        this.signalMeta = {};
        if (data.markers?.length) {
            for (const m of data.markers) {
                const shiftedTime = utcToParisTimestamp(m.time);
                this.signalMeta[shiftedTime] = {
                    isBullish: m.shape === 'arrowUp',
                    detectedAt: m.detected_at || null,
                    candlesDelay: m.candles_delay != null ? m.candles_delay : null,
                };
            }
        }

        this.candleSeries.setMarkers(this.signalMarkers);

        // Supply/Demand zones as price lines
        this._clearZones();
        if (data.zones?.length) {
            this._drawZones(data.zones, data.candles);
        }

        // Zoom to last 100 candles with rightOffset space after last candle
        if (shiftedCandles.length) {
            const timeScale = this.chart.timeScale();
            const totalBars = shiftedCandles.length;
            const visibleBars = Math.min(100, totalBars);
            
            // Use scrollToPosition to place the last bar with rightOffset space
            timeScale.setVisibleLogicalRange({
                from: totalBars - visibleBars,
                to: totalBars - 1 + 15,  // Add 15 bars of empty space to the right
            });
        } else {
            // Fallback if no candles
            this.chart.timeScale().fitContent();
        }
    }

    /**
     * Draw supply/demand zones as horizontal price lines.
     */
    _drawZones(zones, candles) {
        for (const zone of zones) {
            const isSupply = zone.zone_type === 'SUPPLY';
            const color = isSupply ? '#ff446644' : '#00ff8844';
            const lineColor = isSupply ? '#ff4466' : '#00ff88';
            const title = isSupply ? 'SUPPLY' : 'DEMAND';

            // Center line
            const centerLine = this.candleSeries.createPriceLine({
                price: zone.center_price,
                color: lineColor,
                lineWidth: 1,
                lineStyle: LineStyle.Dotted,
                axisLabelVisible: true,
                title: title,
                lineVisible: true,
            });
            this.zonePriceLines.push(centerLine);

            // Top boundary
            const topLine = this.candleSeries.createPriceLine({
                price: zone.top_price,
                color: color,
                lineWidth: 1,
                lineStyle: LineStyle.Dashed,
                axisLabelVisible: false,
                title: '',
                lineVisible: true,
            });
            this.zonePriceLines.push(topLine);

            // Bottom boundary
            const bottomLine = this.candleSeries.createPriceLine({
                price: zone.bottom_price,
                color: color,
                lineWidth: 1,
                lineStyle: LineStyle.Dashed,
                axisLabelVisible: false,
                title: '',
                lineVisible: true,
            });
            this.zonePriceLines.push(bottomLine);
        }
    }

    _clearZones() {
        for (const line of this.zonePriceLines) {
            this.candleSeries.removePriceLine(line);
        }
        this.zonePriceLines = [];
    }

    /**
     * Scroll to a specific timestamp.
     */
    scrollToTime(timestamp) {
        if (timestamp) {
            this.chart.timeScale().scrollToRealTime();
            // Find the bar index closest to the target timestamp
            const visibleRange = this.chart.timeScale().getVisibleLogicalRange();
            if (visibleRange) {
                const timeScale = this.chart.timeScale();
                const coord = timeScale.timeToCoordinate(timestamp);
                if (coord !== null) {
                    timeScale.scrollToPosition(
                        timeScale.logicalToCoordinate(visibleRange.from) - coord,
                        false
                    );
                    return;
                }
            }
        }
        // Fallback: scroll near the end
        this.chart.timeScale().scrollToPosition(-5, false);
    }

    // ──────────────────────────────────────────────────────────
    // POSITION TOOL METHODS
    // ──────────────────────────────────────────────────────────

    activatePositionTool(mode) {
        this.positionTool.active = true;
        this.positionTool.mode = mode; // 'LONG' or 'SHORT'
        this.positionTool.step = 0;
        this.clearPosition();
        
        // Unsubscribe first to prevent stacking, then subscribe
        this.chart.unsubscribeClick(this._boundPositionClick);
        this.chart.subscribeClick(this._boundPositionClick);
        this.container.style.cursor = 'crosshair';
    }

    deactivatePositionTool() {
        this.positionTool.active = false;
        this.positionTool.mode = null;
        this.chart.unsubscribeClick(this._boundPositionClick);
        this.container.style.cursor = 'default';
    }

    clearPosition() {
        // Remove all position lines
        for (const line of this.positionTool.lines) {
            this.candleSeries.removePriceLine(line);
        }
        this.positionTool.lines = [];
        this.positionTool.entry = null;
        this.positionTool.tp = null;
        this.positionTool.sl = null;
        this.positionTool.step = 0;
        
        // Hide stats panel
        const statsPanel = document.getElementById('positionStats');
        if (statsPanel) {
            statsPanel.style.display = 'none';
        }
    }

    _handlePositionClick(param) {
        if (!this.positionTool.active || !param.point) return;

        const price = this.candleSeries.coordinateToPrice(param.point.y);
        
        if (this.positionTool.step === 0) {
            // Set entry
            this.positionTool.entry = price;
            this._drawEntryLine(price);
            this.positionTool.step = 1;
        } else if (this.positionTool.step === 1) {
            // Set TP
            const isValidTP = this.positionTool.mode === 'LONG' 
                ? price > this.positionTool.entry 
                : price < this.positionTool.entry;
            
            if (!isValidTP) return; // Ignore invalid TP
            
            this.positionTool.tp = price;
            this._drawTPLine(price);
            this.positionTool.step = 2;
        } else if (this.positionTool.step === 2) {
            // Set SL
            const isValidSL = this.positionTool.mode === 'LONG' 
                ? price < this.positionTool.entry 
                : price > this.positionTool.entry;
            
            if (!isValidSL) return; // Ignore invalid SL
            
            this.positionTool.sl = price;
            this._drawSLLine(price);
            this._drawStats();
            this.positionTool.step = 3; // Done
            this.deactivatePositionTool();
        }
    }

    _drawEntryLine(price) {
        const line = this.candleSeries.createPriceLine({
            price: price,
            color: '#0088dd',
            lineWidth: 2,
            lineStyle: LineStyle.Solid,
            axisLabelVisible: true,
            title: `ENTRY ${this.positionTool.mode}`,
        });
        this.positionTool.lines.push(line);
    }

    _drawTPLine(price) {
        const line = this.candleSeries.createPriceLine({
            price: price,
            color: '#00aa55',
            lineWidth: 2,
            lineStyle: LineStyle.Dashed,
            axisLabelVisible: true,
            title: 'TAKE PROFIT',
        });
        this.positionTool.lines.push(line);
    }

    _drawSLLine(price) {
        const line = this.candleSeries.createPriceLine({
            price: price,
            color: '#dd3344',
            lineWidth: 2,
            lineStyle: LineStyle.Dashed,
            axisLabelVisible: true,
            title: 'STOP LOSS',
        });
        this.positionTool.lines.push(line);
    }

    _drawStats() {
        const { entry, tp, sl, mode } = this.positionTool;
        
        const risk = Math.abs(entry - sl);
        const reward = Math.abs(tp - entry);
        const rrRatio = (reward / risk).toFixed(2);
        
        const profitPct = ((reward / entry) * 100).toFixed(2);
        const lossPct = ((risk / entry) * 100).toFixed(2);
        
        // Update DOM elements
        const statsPanel = document.getElementById('positionStats');
        if (statsPanel) {
            statsPanel.style.display = 'flex';
            document.getElementById('statsEntry').textContent = entry.toFixed(2);
            document.getElementById('statsTP').textContent = tp.toFixed(2);
            document.getElementById('statsSL').textContent = sl.toFixed(2);
            document.getElementById('statsRR').textContent = rrRatio;
            document.getElementById('statsProfit').textContent = `+${profitPct}%`;
            document.getElementById('statsRisk').textContent = `-${lossPct}%`;
        }
        
        console.log(`Position ${mode}:`, {
            entry: entry.toFixed(2),
            tp: tp.toFixed(2),
            sl: sl.toFixed(2),
            rrRatio,
            profitPct: `+${profitPct}%`,
            lossPct: `-${lossPct}%`,
        });
    }

    /**
     * Display skipped signals on the chart as grey markers.
     * @param {Array} skippedSignals - Array of skipped signal data from the API
     */
    showSkippedSignals(skippedSignals) {
        // Save current visible range to prevent zoom/spacing changes
        const timeScale = this.chart.timeScale();
        const savedRange = timeScale.getVisibleLogicalRange();

        this.skippedMarkers = [];
        this.skippedMeta = {};

        if (!skippedSignals || skippedSignals.length === 0) {
            // Re-merge markers without skipped
            this._mergeAndSetMarkers();
            if (savedRange) timeScale.setVisibleLogicalRange(savedRange);
            return;
        }

        for (const skip of skippedSignals) {
            if (!skip.signal_time) continue;

            // Parse signal_time to Unix timestamp
            const signalDate = new Date(skip.signal_time);
            const rawTs = Math.floor(signalDate.getTime() / 1000);
            const parisTs = utcToParisTimestamp(rawTs);
            const snapTs = this._snapToCandleTime(parisTs);

            const isLong = skip.side === 'LONG';

            this.skippedMarkers.push({
                time: snapTs,
                position: isLong ? 'belowBar' : 'aboveBar',
                color: '#888888',
                shape: 'circle',
                text: `⊘ ${skip.side}`,
                size: 1,
            });

            // Store metadata for tooltip
            if (!this.skippedMeta[snapTs]) this.skippedMeta[snapTs] = [];
            this.skippedMeta[snapTs].push({
                agentName: skip.agent_name,
                side: skip.side,
                reason: skip.reason,
                entryPrice: skip.entry_price,
                signalPrice: skip.signal_price,
                stopLoss: skip.stop_loss,
                riskPct: skip.risk_pct,
                htfChecked: skip.htf_checked,
                balance: skip.balance,
                positionDurationS: skip.position_duration_s,
                minGapS: skip.min_gap_s,
                skippedAt: skip.skipped_at,
            });
        }

        // Merge all markers and set
        this._mergeAndSetMarkers();

        // Restore zoom
        if (savedRange) timeScale.setVisibleLogicalRange(savedRange);
    }

    /**
     * Merge signal, agent, and skipped markers and set on the candle series.
     * Markers must be sorted by time (required by lightweight-charts).
     */
    _mergeAndSetMarkers() {
        const allMarkers = [
            ...this.signalMarkers,
            ...this.agentMarkers,
            ...this.skippedMarkers,
        ].sort((a, b) => a.time - b.time);
        this.candleSeries.setMarkers(allMarkers);
    }

    /**
     * Display agent positions on the chart
     * @param {Array} positions - Array of agent positions
     */
    showAgentPositions(positions) {
        // Save current visible range to prevent zoom/spacing changes
        const timeScale = this.chart.timeScale();
        const savedRange = timeScale.getVisibleLogicalRange();

        // Clear existing position lines
        this.agentPositionLines.forEach(line => {
            this.candleSeries.removePriceLine(line);
        });
        this.agentPositionLines = [];

        // Clear existing zone series
        this.agentZoneSeries.forEach(series => {
            this.chart.removeSeries(series);
        });
        this.agentZoneSeries = [];

        this.agentMarkers = [];
        this.agentMeta = {};  // Reset agent metadata

        if (!positions || positions.length === 0) {
            // Restore signal + skipped markers (no agent markers)
            this._mergeAndSetMarkers();
            // Restore zoom
            if (savedRange) {
                timeScale.setVisibleLogicalRange(savedRange);
            }
            return;
        }

        // Show only open positions and last 20 closed positions
        const openPos = positions.filter(p => p.status === 'OPEN');
        const closedPos = positions.filter(p => p.status === 'CLOSED' || p.status === 'STOPPED')
            .slice(0, 20);
        
        const displayPositions = [...openPos, ...closedPos];

        displayPositions.forEach(pos => {
            const isOpen = pos.status === 'OPEN';
            const isLong = pos.side === 'LONG';
            const isClosed = pos.status === 'CLOSED' || pos.status === 'STOPPED';
            
            // Entry line
            const entryColor = isLong ? '#0088dd' : '#dd8800';
            const entryLine = this.candleSeries.createPriceLine({
                price: pos.entry_price,
                color: isOpen ? entryColor : entryColor + '66',
                lineWidth: isOpen ? 2 : 1,
                lineStyle: isOpen ? LineStyle.Solid : LineStyle.Dotted,
                axisLabelVisible: isOpen,
                title: `${pos.agent_name} ${pos.side}${isClosed ? ' (closed)' : ''}`,
            });
            this.agentPositionLines.push(entryLine);

            // Only show TP/SL for open positions
            if (isOpen) {
                // Take Profit line
                const tpLine = this.candleSeries.createPriceLine({
                    price: pos.take_profit,
                    color: '#00aa5588',
                    lineWidth: 1,
                    lineStyle: LineStyle.Dashed,
                    axisLabelVisible: false,
                    title: '',
                });
                this.agentPositionLines.push(tpLine);

                // Stop Loss line
                const slLine = this.candleSeries.createPriceLine({
                    price: pos.stop_loss,
                    color: '#dd334488',
                    lineWidth: 1,
                    lineStyle: LineStyle.Dashed,
                    axisLabelVisible: false,
                    title: '',
                });
                this.agentPositionLines.push(slLine);

                // Add markers for entry (apply Paris timezone shift like candle data)
                const entryRawTime = utcToParisTimestamp(Math.floor(new Date(pos.opened_at).getTime() / 1000));
                const entrySnapTime = this._snapToCandleTime(entryRawTime);
                const entryMarker = {
                    time: entrySnapTime,
                    position: isLong ? 'belowBar' : 'aboveBar',
                    color: isLong ? '#0088dd' : '#dd8800',
                    shape: isLong ? 'arrowUp' : 'arrowDown',
                    text: pos.agent_name,
                    size: 2,
                };
                this.agentMarkers.push(entryMarker);

                // Store entry metadata for tooltip
                const od = pos.open_details || {};
                if (!this.agentMeta[entrySnapTime]) this.agentMeta[entrySnapTime] = [];
                this.agentMeta[entrySnapTime].push({
                    type: 'entry',
                    agentName: pos.agent_name,
                    side: pos.side,
                    entryPrice: pos.entry_price,
                    stopLoss: od.stop_loss ?? pos.stop_loss,
                    tp1: od.take_profit_1 ?? pos.take_profit,
                    tp2: od.take_profit_2 ?? pos.tp2,
                    risk: od.risk,
                    rrRatio: od.rr_ratio_tp1,
                    zoneTpUsed: od.zone_tp_used,
                    mode: od.mode || 'paper',
                    openedAt: pos.opened_at,
                    color: isLong ? '#0088dd' : '#dd8800',
                });

                // Add breakeven marker on chart
                if (pos.breakeven_at) {
                    const beTs = this._snapToCandleTime(
                        utcToParisTimestamp(Math.floor(new Date(pos.breakeven_at).getTime() / 1000))
                    );
                    this.agentMarkers.push({
                        time: beTs,
                        position: 'inBar',
                        color: '#0088dd',
                        shape: 'circle',
                        text: '🛡 BE',
                        size: 1,
                    });
                }

                // Add partial TP1 marker on chart
                if (pos.partial_closed && pos.partial_tp_at) {
                    const tpTs = this._snapToCandleTime(
                        utcToParisTimestamp(Math.floor(new Date(pos.partial_tp_at).getTime() / 1000))
                    );
                    const partialSign = (pos.partial_pnl || 0) >= 0 ? '+' : '';
                    const partialPnlStr = pos.partial_pnl != null ? ` ${partialSign}${pos.partial_pnl.toFixed(2)}€` : '';
                    this.agentMarkers.push({
                        time: tpTs,
                        position: isLong ? 'aboveBar' : 'belowBar',
                        color: '#00aa55',
                        shape: 'circle',
                        text: `TP1 50%${partialPnlStr}`,
                        size: 2,
                    });

                    // Store TP1 metadata for tooltip
                    if (!this.agentMeta[tpTs]) this.agentMeta[tpTs] = [];
                    this.agentMeta[tpTs].push({
                        type: 'exit',
                        agentName: pos.agent_name,
                        side: pos.side,
                        entryPrice: pos.entry_price,
                        exitPrice: pos.open_details?.take_profit_1 || pos.take_profit,
                        pnl: pos.partial_pnl,
                        pnlPercent: null,
                        closeReason: 'PARTIAL_TP1',
                        status: 'OPEN',
                        partialClosed: true,
                        partialPnl: pos.partial_pnl,
                        color: '#00aa55',
                    });
                }

                // Draw TP/SL colored zones
                if (this.lastCandleTime && pos.take_profit && pos.stop_loss) {
                    const zoneStart = entryMarker.time;
                    const zoneEnd = this.lastCandleTime;
                    // Use only candle-aligned timestamps to avoid creating extra
                    // data points that change the time axis spacing
                    const zoneData = [];
                    if (this.candleTimes) {
                        for (const t of this.candleTimes) {
                            if (t >= zoneStart && t <= zoneEnd) {
                                zoneData.push({ time: t, value: pos.entry_price });
                            }
                        }
                        zoneData.sort((a, b) => a.time - b.time);
                    }
                    if (zoneData.length === 0) {
                        zoneData.push({ time: zoneStart, value: pos.entry_price });
                        zoneData.push({ time: zoneEnd, value: pos.entry_price });
                    }

                    // TP zone (green) — baseline series with fill between entry and TP
                    const tpZone = this.chart.addBaselineSeries({
                        baseValue: { type: 'price', price: isLong ? pos.take_profit : pos.stop_loss },
                        topLineColor: 'transparent',
                        bottomLineColor: 'transparent',
                        topFillColor1: 'transparent',
                        topFillColor2: 'transparent',
                        bottomFillColor1: isLong ? 'rgba(0, 170, 85, 0.12)' : 'rgba(221, 51, 68, 0.12)',
                        bottomFillColor2: isLong ? 'rgba(0, 170, 85, 0.06)' : 'rgba(221, 51, 68, 0.06)',
                        lineWidth: 0,
                        priceLineVisible: false,
                        lastValueVisible: false,
                        crosshairMarkerVisible: false,
                    });
                    tpZone.setData(zoneData);
                    this.agentZoneSeries.push(tpZone);

                    // SL zone (red) — baseline series with fill between entry and SL
                    const slZone = this.chart.addBaselineSeries({
                        baseValue: { type: 'price', price: isLong ? pos.stop_loss : pos.take_profit },
                        topLineColor: 'transparent',
                        bottomLineColor: 'transparent',
                        topFillColor1: isLong ? 'rgba(221, 51, 68, 0.06)' : 'rgba(0, 170, 85, 0.06)',
                        topFillColor2: isLong ? 'rgba(221, 51, 68, 0.12)' : 'rgba(0, 170, 85, 0.12)',
                        bottomFillColor1: 'transparent',
                        bottomFillColor2: 'transparent',
                        lineWidth: 0,
                        priceLineVisible: false,
                        lastValueVisible: false,
                        crosshairMarkerVisible: false,
                    });
                    slZone.setData(zoneData);
                    this.agentZoneSeries.push(slZone);
                }
            }

            // For closed/stopped positions, show exit line + entry/exit markers
            if (isClosed) {
                if (pos.exit_price) {
                    const exitLine = this.candleSeries.createPriceLine({
                        price: pos.exit_price,
                        color: pos.pnl > 0 ? '#00aa5566' : '#dd334466',
                        lineWidth: 1,
                        lineStyle: LineStyle.Dotted,
                        axisLabelVisible: false,
                        title: '',
                    });
                    this.agentPositionLines.push(exitLine);
                }

                // Entry marker for closed position (muted color)
                if (pos.opened_at) {
                    const entryTs = this._snapToCandleTime(
                        utcToParisTimestamp(Math.floor(new Date(pos.opened_at).getTime() / 1000))
                    );
                    this.agentMarkers.push({
                        time: entryTs,
                        position: isLong ? 'belowBar' : 'aboveBar',
                        color: isLong ? '#0088dd88' : '#dd880088',
                        shape: isLong ? 'arrowUp' : 'arrowDown',
                        text: `${pos.agent_name}`,
                        size: 1,
                    });

                    // Store entry metadata for tooltip (closed position)
                    const od = pos.open_details || {};
                    if (!this.agentMeta[entryTs]) this.agentMeta[entryTs] = [];
                    this.agentMeta[entryTs].push({
                        type: 'entry',
                        agentName: pos.agent_name,
                        side: pos.side,
                        entryPrice: pos.entry_price,
                        stopLoss: od.stop_loss ?? pos.original_stop_loss ?? pos.stop_loss,
                        tp1: od.take_profit_1 ?? pos.take_profit,
                        tp2: od.take_profit_2 ?? pos.tp2,
                        risk: od.risk,
                        rrRatio: od.rr_ratio_tp1,
                        zoneTpUsed: od.zone_tp_used,
                        mode: od.mode || 'paper',
                        openedAt: pos.opened_at,
                        color: isLong ? '#0088dd' : '#dd8800',
                    });
                }

                // Exit marker for closed position (circle showing result)
                if (pos.closed_at) {
                    const exitTs = this._snapToCandleTime(
                        utcToParisTimestamp(Math.floor(new Date(pos.closed_at).getTime() / 1000))
                    );
                    const isStopped = pos.status === 'STOPPED';
                    const isWin = pos.pnl > 0;
                    const pnlStr = pos.pnl != null ? (pos.pnl > 0 ? '+' : '') + pos.pnl.toFixed(2) : '';
                    // Label: SL if stopped, TP if profitable close, EXIT if closed at a loss
                    const exitLabel = isStopped ? 'SL' : (isWin ? 'TP' : 'EXIT');
                    this.agentMarkers.push({
                        time: exitTs,
                        position: isLong ? 'aboveBar' : 'belowBar',
                        color: isWin ? '#00aa55' : '#dd3344',
                        shape: 'circle',
                        text: `${exitLabel} ${pnlStr}`,
                        size: 1,
                    });

                    // Store exit metadata for tooltip
                    if (!this.agentMeta[exitTs]) this.agentMeta[exitTs] = [];
                    this.agentMeta[exitTs].push({
                        type: 'exit',
                        agentName: pos.agent_name,
                        side: pos.side,
                        entryPrice: pos.entry_price,
                        exitPrice: pos.exit_price,
                        stopLoss: pos.stop_loss,
                        originalSl: pos.original_stop_loss,
                        pnl: pos.pnl,
                        pnlPercent: pos.pnl_percent,
                        closeReason: pos.close_reason,
                        status: pos.status,
                        openedAt: pos.opened_at,
                        closedAt: pos.closed_at,
                        partialClosed: pos.partial_closed,
                        partialPnl: pos.partial_pnl,
                        color: isWin ? '#00aa55' : '#dd3344',
                    });
                }
            }
        });

        // Combine signal markers + agent markers + skipped markers and set once (sorted by time)
        this._mergeAndSetMarkers();

        // Restore the saved zoom range to prevent spacing changes
        if (savedRange) {
            timeScale.setVisibleLogicalRange(savedRange);
        }
    }

    /**
     * Snap a timestamp to the nearest existing candle time.
     * Markers MUST match a candle time to be rendered by lightweight-charts.
     */
    _snapToCandleTime(timestamp) {
        if (!this.candleTimes || this.candleTimes.size === 0) return timestamp;
        let closest = null;
        let minDiff = Infinity;
        for (const t of this.candleTimes) {
            const diff = Math.abs(t - timestamp);
            if (diff < minDiff) {
                minDiff = diff;
                closest = t;
            }
        }
        return closest !== null ? closest : timestamp;
    }

    destroy() {
        this._resizeObserver?.disconnect();
        this.chart?.remove();
    }
}
