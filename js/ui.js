/**
 * UI helpers — button states, formatting.
 * Theme is handled by inline script in index.html (no module dependency).
 */

export function setConnectionState(connected) {
  const dot = document.getElementById('statusDot');
  const connectSerialBtn = document.getElementById('connectSerial');
  const connectWsBtn = document.getElementById('connectWs');
  const disconnectBtn = document.getElementById('disconnect');
  const wsUrlInput = document.getElementById('wsUrl');

  if (dot) dot.classList.toggle('connected', connected);

  if (connectSerialBtn) connectSerialBtn.disabled = connected;
  if (connectWsBtn) connectWsBtn.disabled = connected;
  if (disconnectBtn) disconnectBtn.disabled = !connected;
  if (wsUrlInput) wsUrlInput.disabled = connected;

  // Enable/disable command-dependent buttons
  const cmdBtns = document.querySelectorAll('[data-requires-connection]');
  cmdBtns.forEach(btn => btn.disabled = !connected);
}

export function setMeasurementState(active) {
  const startBtn = document.getElementById('startMeasure');
  const stopBtn = document.getElementById('stopMeasure');
  if (startBtn) {
    startBtn.disabled = active;
    startBtn.classList.toggle('active', false);
  }
  if (stopBtn) {
    stopBtn.disabled = !active;
    stopBtn.classList.toggle('active', active);
  }
}

export function setRecordingState(active) {
  const startBtn = document.getElementById('startRecord');
  const stopBtn = document.getElementById('stopRecord');
  if (startBtn) {
    startBtn.disabled = active;
    startBtn.classList.toggle('active', false);
  }
  if (stopBtn) {
    stopBtn.disabled = !active;
    stopBtn.classList.toggle('active', active);
  }
}

export function formatReading(value, unit) {
  if (value === null || value === undefined) return '---';
  const num = typeof value === 'number' ? value : parseFloat(value);
  if (isNaN(num)) return String(value);
  // Display up to 4 decimal places, trimming trailing zeros
  const formatted = num.toFixed(4).replace(/\.?0+$/, '');
  return unit ? `${formatted} ${unit}` : formatted;
}

export function updateReadout(value, unit) {
  const valEl = document.getElementById('readoutValue');
  const unitEl = document.getElementById('readoutUnit');
  const timeEl = document.getElementById('readoutTime');
  if (valEl) {
    if (value === null || value === undefined) {
      valEl.textContent = '---';
    } else {
      const num = typeof value === 'number' ? value : parseFloat(value);
      valEl.textContent = isNaN(num) ? String(value) : num.toFixed(4).replace(/\.?0+$/, '');
    }
  }
  if (unitEl) unitEl.textContent = unit || '';
  if (timeEl) timeEl.textContent = new Date().toLocaleTimeString();
}

export function updateStats(stats) {
  const fmt = (v) => v === null ? '---' : v.toFixed(4).replace(/\.?0+$/, '');
  const set = (id, v) => {
    const el = document.getElementById(id);
    if (el) el.textContent = fmt(v);
  };
  set('statMin', stats.min);
  set('statMax', stats.max);
  set('statMean', stats.mean);
  set('statStddev', stats.stddev);
  const countEl = document.getElementById('statCount');
  if (countEl) countEl.textContent = stats.count;
}

export function appendLog(message) {
  const el = document.getElementById('logOutput');
  if (!el) return;
  const now = new Date().toLocaleTimeString();
  el.textContent += `[${now}] ${message}\n`;
  el.scrollTop = el.scrollHeight;
}

/**
 * Show a toast notification.
 * @param {string} message
 * @param {'info'|'success'|'error'} type
 * @param {number} duration ms before auto-dismiss
 */
export function showToast(message, type = 'info', duration = 4000) {
  const container = document.getElementById('toastContainer');
  if (!container) return;
  const el = document.createElement('div');
  el.className = `toast toast-${type}`;
  el.textContent = message;
  container.appendChild(el);
  const dismiss = () => {
    el.classList.add('toast-out');
    el.addEventListener('animationend', () => el.remove());
  };
  el.addEventListener('click', dismiss);
  if (duration > 0) setTimeout(dismiss, duration);
}
