/**
 * Chart Manager — TradingView Lightweight Charts integration.
 * Renders candlesticks, EMAs, supply/demand zones, and reversal markers.
 */

import { createChart, CrosshairMode, LineStyle } from 'lightweight-charts';

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
    }

    /**
     * Load complete chart data from the API response.
     */
    setData(data) {
        // Candles
        if (data.candles?.length) {
            this.candleSeries.setData(data.candles);
        }

        // Volume (derive from candles — API could add volumes later)
        if (data.candles?.length) {
            const volumeData = data.candles.map(c => ({
                time: c.time,
                value: 0,  // placeholder
                color: c.close >= c.open ? '#00ff8833' : '#ff446633',
            }));
            this.volumeSeries.setData(volumeData);
        }

        // EMAs
        if (data.ema_9?.length) this.ema9Series.setData(data.ema_9);
        if (data.ema_14?.length) this.ema14Series.setData(data.ema_14);
        if (data.ema_21?.length) this.ema21Series.setData(data.ema_21);

        // Markers (reversal signals)
        if (data.markers?.length) {
            this.candleSeries.setMarkers(data.markers);
        }

        // Supply/Demand zones as price lines
        this._clearZones();
        if (data.zones?.length) {
            this._drawZones(data.zones, data.candles);
        }

        // Fit content
        this.chart.timeScale().fitContent();
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
        
        // Subscribe to chart clicks
        this.chart.subscribeClick(this._handlePositionClick.bind(this));
        this.container.style.cursor = 'crosshair';
    }

    deactivatePositionTool() {
        this.positionTool.active = false;
        this.positionTool.mode = null;
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

    destroy() {
        this._resizeObserver?.disconnect();
        this.chart?.remove();
    }
}
