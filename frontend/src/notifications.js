/**
 * Signal notification sounds — bell chime via Web Audio API.
 */

import { state } from './state.js';

let _bellAudioCtx = null;

export function playBellSound() {
    try {
        if (!_bellAudioCtx || _bellAudioCtx.state === 'closed') {
            _bellAudioCtx = new (window.AudioContext || window.webkitAudioContext)();
        }
        // Resume if suspended (browser autoplay policy)
        if (_bellAudioCtx.state === 'suspended') {
            _bellAudioCtx.resume();
        }
        const ctx = _bellAudioCtx;
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
    } catch (e) {
        console.warn('Could not play bell sound:', e);
    }
}

export function checkNewSignals(data) {
    const markers = data.markers || [];
    const candles = data.candles || [];
    // Use time + direction as stable key (price/text can shift on recalc)
    const currentKeys = new Set(
        markers.map(m => `${m.time}_${m.shape}`)
    );

    if (state.isFirstLoad) {
        // First load — just memorize, no sound
        state.knownSignalKeys = currentKeys;
        state.isFirstLoad = false;
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
        if (!state.knownSignalKeys.has(key)) {
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

    state.knownSignalKeys = currentKeys;
}
