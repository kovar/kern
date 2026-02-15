/**
 * main.js — Entry point. Wires all modules together.
 */
import { ConnectionManager } from './connection.js';
import { ChartManager } from './chart-manager.js';
import { Recorder } from './recorder.js';
import { StatsTracker } from './stats.js';
import {
  setConnectionState, setMeasurementState, setRecordingState,
  updateReadout, updateStats, appendLog, showToast,
} from './ui.js';

// ── Instances ──────────────────────────────────────────────
const conn = new ConnectionManager();
let chart;
const recorder = new Recorder();
const stats = new StatsTracker();
let measurementInterval = null;
let measurementTimeout = null;
let lastReadingTime = 0;
let demoInterval = null;
let demoState = null;

// ── DOM Ready ──────────────────────────────────────────────
// Signal that modules loaded successfully (suppresses the file:// warning banner)
window._kernModulesLoaded = true;

document.addEventListener('DOMContentLoaded', () => {
  // Theme is handled by inline script in index.html

  // Wire UI events first so buttons work even if chart init fails
  wireConnection();
  wireToolbar();
  wireCustomCommand();

  // Initial UI state
  setConnectionState(false);
  setMeasurementState(false);
  setRecordingState(false);

  // Hide Web Serial button if not supported
  const serialBtn = document.getElementById('connectSerial');
  if (!conn.hasWebSerial && serialBtn) {
    serialBtn.style.display = 'none';
  }

  // Chart init last — depends on CDN scripts
  try {
    chart = new ChartManager(document.getElementById('chartCanvas'));
    wireChart();
  } catch (err) {
    appendLog('Chart init failed: ' + err.message);
  }
});

// ── Connection Events ──────────────────────────────────────
function wireConnection() {
  conn.addEventListener('connected', () => {
    setConnectionState(true);
    appendLog('Connected');
    showToast('Connected to scale', 'success');
  });

  conn.addEventListener('disconnected', () => {
    stopMeasurement();
    setConnectionState(false);
    appendLog('Disconnected');
    showToast('Disconnected', 'info');
  });

  conn.addEventListener('reading', (e) => {
    const { value, unit, raw } = e.detail;
    lastReadingTime = Date.now();
    updateReadout(value, unit);
    if (value !== null) {
      if (chart) chart.addReading(value);
      stats.addValue(value);
      updateStats(stats.getStats());
      recorder.addReading(value, unit);
    }
  });

  conn.addEventListener('log', (e) => appendLog(e.detail.message));
  conn.addEventListener('error', (e) => {
    appendLog('ERROR: ' + e.detail.message);
    showToast(e.detail.message, 'error', 6000);
  });
}

// ── Toolbar Buttons ────────────────────────────────────────
function wireToolbar() {
  // Connect via Web Serial
  document.getElementById('connectSerial')?.addEventListener('click', async () => {
    try { await conn.connectSerial(); } catch (_) {}
  });

  // Connect via WebSocket bridge
  document.getElementById('connectWs')?.addEventListener('click', async () => {
    const url = document.getElementById('wsUrl')?.value || undefined;
    try { await conn.connectWebSocket(url); } catch (_) {}
  });

  // Disconnect
  document.getElementById('disconnect')?.addEventListener('click', () => conn.disconnect());

  // Scale commands
  document.querySelectorAll('[data-cmd]').forEach(btn => {
    btn.addEventListener('click', () => conn.send(btn.dataset.cmd));
  });

  // Measurement
  document.getElementById('startMeasure')?.addEventListener('click', startMeasurement);
  document.getElementById('stopMeasure')?.addEventListener('click', stopMeasurement);

  // Recording
  document.getElementById('startRecord')?.addEventListener('click', () => {
    recorder.start();
    setRecordingState(true);
    appendLog('Recording started');
    showToast('Recording started', 'info');
  });

  document.getElementById('stopRecord')?.addEventListener('click', () => {
    recorder.stop();
    setRecordingState(false);
    if (recorder.download()) {
      const msg = 'Recording saved (' + recorder.count + ' readings)';
      appendLog(msg);
      showToast(msg, 'success');
    } else {
      appendLog('No data recorded');
      showToast('No data recorded', 'error');
    }
  });

  // Demo
  document.getElementById('demo')?.addEventListener('click', toggleDemo);

  // Theme toggle is wired via onclick in index.html
}

// ── Measurement ────────────────────────────────────────────
function startMeasurement() {
  if (measurementInterval) return;
  const rate = parseInt(document.getElementById('samplingRate')?.value) || 1000;
  const before = lastReadingTime;
  measurementInterval = setInterval(() => conn.send('SI'), rate);
  setMeasurementState(true);
  appendLog(`Measurement started (every ${rate} ms)`);
  showToast(`Measurement polling every ${rate} ms`, 'info');

  // Check for no response after 3 seconds
  measurementTimeout = setTimeout(() => {
    if (measurementInterval && lastReadingTime === before) {
      showToast('No response from scale — is it connected and powered on?', 'error', 6000);
      appendLog('WARNING: No readings received from scale');
    }
  }, 3000);
}

function stopMeasurement() {
  if (!measurementInterval) return;
  clearInterval(measurementInterval);
  clearTimeout(measurementTimeout);
  measurementInterval = null;
  measurementTimeout = null;
  setMeasurementState(false);
  appendLog('Measurement stopped');
}

// ── Chart Controls ─────────────────────────────────────────
function wireChart() {
  document.getElementById('timeRange')?.addEventListener('change', (e) => {
    chart.setTimeWindow(parseInt(e.target.value));
  });

  document.getElementById('yMin')?.addEventListener('change', () => {
    chart.setYRange(
      document.getElementById('yMin').value,
      document.getElementById('yMax').value,
    );
  });

  document.getElementById('yMax')?.addEventListener('change', () => {
    chart.setYRange(
      document.getElementById('yMin').value,
      document.getElementById('yMax').value,
    );
  });

  document.getElementById('resetZoom')?.addEventListener('click', () => {
    chart.resetZoom();
    document.getElementById('yMin').value = '';
    document.getElementById('yMax').value = '';
  });

  document.getElementById('clearChart')?.addEventListener('click', () => {
    chart.clear();
    stats.reset();
    updateStats(stats.getStats());
  });
}

// ── Demo Mode ──────────────────────────────────────────────
function toggleDemo() {
  const btn = document.getElementById('demo');
  if (demoInterval) {
    stopDemo();
  } else {
    startDemo();
    if (btn) { btn.textContent = 'Stop Demo'; btn.classList.add('active'); }
  }
}

function startDemo() {
  // Simulate a ~250 g sample with noise and slow drift
  demoState = { base: 250, drift: 0, step: 0 };
  const rate = parseInt(document.getElementById('samplingRate')?.value) || 1000;

  // Enable measurement/recording buttons as if connected
  setConnectionState(true);
  showToast('Demo mode — generating fake scale data', 'info');
  appendLog('Demo started');

  demoInterval = setInterval(() => {
    demoState.step++;
    // Slow sinusoidal drift (~0.5 g amplitude over ~60 steps)
    demoState.drift = 0.5 * Math.sin(demoState.step / 60 * Math.PI * 2);
    // Gaussian-ish noise: sum of 3 uniform randoms, centered, scaled to ~0.05 g stddev
    const noise = ((Math.random() + Math.random() + Math.random()) / 3 - 0.5) * 0.1;
    const value = Math.round((demoState.base + demoState.drift + noise) * 100) / 100;
    const unit = 'g';

    updateReadout(value, unit);
    if (chart) chart.addReading(value);
    stats.addValue(value);
    updateStats(stats.getStats());
    recorder.addReading(value, unit);
    appendLog(`Received: S S      ${value.toFixed(2)} ${unit}`);
  }, rate);
}

function stopDemo() {
  if (demoInterval) {
    clearInterval(demoInterval);
    demoInterval = null;
    demoState = null;
  }
  stopMeasurement();
  setConnectionState(false);
  const btn = document.getElementById('demo');
  if (btn) { btn.textContent = 'Demo'; btn.classList.remove('active'); }
  appendLog('Demo stopped');
  showToast('Demo stopped', 'info');
}

// ── Custom Command ─────────────────────────────────────────
function wireCustomCommand() {
  const input = document.getElementById('customCmdInput');
  const btn = document.getElementById('customCmdSend');
  if (!input || !btn) return;

  btn.addEventListener('click', () => {
    const cmd = input.value.trim();
    if (cmd) { conn.send(cmd); input.value = ''; }
  });

  input.addEventListener('keypress', (e) => {
    if (e.key === 'Enter') btn.click();
  });
}
