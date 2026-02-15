/**
 * WebSerialTransport — Web Serial API transport (Chromium only).
 *
 * Events emitted:
 *   'connected'    — serial port opened
 *   'disconnected' — serial port closed
 *   'reading'      — { raw, value, unit } parsed from scale response
 *   'log'          — { message } informational log line
 *   'error'        — { message } error description
 */
export class WebSerialTransport extends EventTarget {
  #port = null;
  #writer = null;
  #reader = null;
  #readLoopRunning = false;

  static isSupported() {
    return 'serial' in navigator;
  }

  async connect() {
    try {
      this.#port = await navigator.serial.requestPort();
      await this.#port.open({ baudRate: 9600, dataBits: 8, stopBits: 1, parity: 'none' });
      this.#writer = this.#port.writable.getWriter();
      this.#emit('connected');
      this.#emit('log', { message: 'Serial port opened (9600 8N1)' });
      this.#readLoop();
    } catch (err) {
      this.#emit('error', { message: 'Connect failed: ' + err.message });
      throw err;
    }
  }

  async disconnect() {
    this.#readLoopRunning = false;
    try {
      if (this.#reader) {
        await this.#reader.cancel();
        this.#reader.releaseLock();
        this.#reader = null;
      }
    } catch (_) { /* reader may already be released */ }
    try {
      if (this.#writer) {
        this.#writer.releaseLock();
        this.#writer = null;
      }
    } catch (_) { /* writer may already be released */ }
    try {
      if (this.#port) {
        await this.#port.close();
        this.#port = null;
      }
    } catch (err) {
      this.#emit('error', { message: 'Close error: ' + err.message });
    }
    this.#emit('disconnected');
    this.#emit('log', { message: 'Serial port closed' });
  }

  async send(cmd) {
    if (!this.#writer) {
      this.#emit('error', { message: 'Not connected' });
      return;
    }
    const data = new TextEncoder().encode(cmd.trim() + '\r\n');
    await this.#writer.write(data);
    this.#emit('log', { message: 'Sent: ' + cmd.trim() });
  }

  async #readLoop() {
    const decoder = new TextDecoder();
    this.#reader = this.#port.readable.getReader();
    this.#readLoopRunning = true;
    let buffer = '';
    try {
      while (this.#readLoopRunning) {
        const { value, done } = await this.#reader.read();
        if (done) break;
        if (value) {
          buffer += decoder.decode(value, { stream: true });
          let lines = buffer.split('\n');
          buffer = lines.pop();
          for (const line of lines) {
            const trimmed = line.replace(/\r/g, '').trim();
            if (trimmed) this.#parseLine(trimmed);
          }
        }
      }
    } catch (err) {
      if (this.#readLoopRunning) {
        this.#emit('error', { message: 'Read error: ' + err.message });
      }
    } finally {
      try { this.#reader.releaseLock(); } catch (_) {}
      this.#reader = null;
    }
  }

  #parseLine(line) {
    this.#emit('log', { message: 'Received: ' + line });
    // KCP responses: status indicator, then value, then unit
    // e.g. "S S     120.45 g" or "S D     120.45 g"
    // Also handles plain numeric lines
    const match = line.match(/(-?\d+(\.\d+)?)\s*([a-zA-Z%/]*)/);
    if (match) {
      const value = parseFloat(match[1]);
      const unit = match[3] || '';
      this.#emit('reading', { raw: line, value, unit });
    } else {
      this.#emit('reading', { raw: line, value: null, unit: '' });
    }
  }

  #emit(type, detail = {}) {
    this.dispatchEvent(new CustomEvent(type, { detail }));
  }
}
