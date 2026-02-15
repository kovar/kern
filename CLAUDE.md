# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Web application for communicating with Kern laboratory scales using KCP (Kern Communications Protocol). Reads weight data over serial (or WebSocket bridge), displays live readings with statistics, plots measurements in real-time, and exports to CSV.

## Architecture

```
index.html              → HTML shell (links CSS + JS modules, CDN scripts)
css/styles.css          → All styles, CSS custom properties for dark/light theming
js/
  main.js               → Entry point: imports modules, wires DOM events
  serial.js             → WebSerialTransport (Web Serial API, Chromium only)
  websocket.js          → WebSocketTransport (connects to bridge.py)
  connection.js         → ConnectionManager: picks transport, uniform event interface
  chart-manager.js      → ChartManager wrapping Chart.js with date adapter
  recorder.js           → Recorder with Blob-based CSV export
  stats.js              → StatsTracker (Welford's algorithm for live statistics)
  ui.js                 → Theme toggle, connection badge, button states, formatting

bridge.py               → WebSocket ↔ serial bridge (pyserial + websockets)
plot.py                 → Plots mass_readings.csv with Polars + Matplotlib

.github/workflows/static.yml → GitHub Pages deployment (deploys on push to main)
```

No build step. No npm. ES modules loaded via `<script type="module">`. Chart.js + date adapter loaded from CDN with pinned versions.

## Transport Layer

Two transport backends implement the same EventTarget interface:
- **Web Serial** (`serial.js`) — direct USB access in Chromium browsers
- **WebSocket** (`websocket.js`) — connects to `bridge.py` for Firefox/Safari/any browser

`ConnectionManager` (`connection.js`) auto-detects browser capabilities and presents appropriate connect options.

## KCP Protocol Commands

The scale interface uses these serial commands:
- `SI` — Immediate reading (send stable/unstable value)
- `S` — Send stable value
- `T` — Tare
- `Z` — Zero
- `@` — Reset

Serial config: 9600 baud, 8 data bits, 1 stop bit, no parity. Default sampling interval: 1000ms.

## Deployment

The site is deployed to GitHub Pages automatically on push to `main` via `.github/workflows/static.yml`.

## Running

**Web UI (local development):**
```bash
uv run serve.py     # starts http://localhost:8000 and opens browser
```
Do NOT open `index.html` directly — ES modules require HTTP, not `file://`.

- Chrome/Edge: can connect directly via USB (Web Serial API)
- Firefox/Safari: use the WebSocket bridge
- Any browser: click Demo to test with fake data

**WebSocket Bridge (for non-Chromium browsers):**
```bash
uv run bridge.py                        # auto-detect serial port
uv run bridge.py /dev/cu.usbserial-10   # specify port
```
Dependencies (`pyserial`, `websockets`) are declared inline via PEP 723 — `uv` installs them automatically.

**Plot script:**
```bash
uv run plot.py     # Plot CSV output
```
