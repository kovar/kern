# Kern Scale Interface

Web application for communicating with Kern laboratory scales using KCP (Kern Communications Protocol). Reads weight data over serial or WebSocket, displays live readings with statistics, charts measurements in real-time, and exports to CSV.

![Screenshot — dark mode demo](https://img.shields.io/badge/no_build_step-ES_modules-teal)

## Features

- **Live weight readout** with tare, zero, and unit controls
- **Real-time chart** with configurable time window and Y-axis range
- **Running statistics** — min, max, mean, count (Welford's algorithm)
- **CSV recording** — timestamped weight export
- **Two connection modes** — USB (Web Serial) or WebSocket bridge
- **Dark/light theme** — auto-detects OS preference
- **Demo mode** — try it without hardware
- **Custom commands** — send any KCP command directly

## Quick Start

```bash
uv run serve.py
```

This starts a local server at **http://localhost:8000** and opens your browser.

> Don't open `index.html` directly — ES modules require a web server.

### Connect to your scale

- **Chrome/Edge:** Click **USB** to connect directly via Web Serial API
- **Firefox/Safari/remote:** Start the bridge, then click **Bridge**:
  ```bash
  uv run bridge.py                        # auto-detect serial port
  uv run bridge.py /dev/cu.usbserial-10   # specify port
  ```
- **No hardware:** Click **Demo** to generate fake data

## Architecture

No build step, no npm, no bundler. Plain ES modules served over HTTP. Chart.js loaded from CDN.

```
index.html          HTML shell
css/styles.css      Styles with CSS custom properties for theming
js/
  main.js           Entry point, event wiring
  serial.js         Web Serial transport (Chromium only)
  websocket.js      WebSocket transport (connects to bridge.py)
  connection.js     ConnectionManager — uniform event interface
  chart-manager.js  Chart.js wrapper with time-axis
  recorder.js       CSV recording and Blob-based download
  stats.js          Welford's online statistics
  ui.js             Button states, formatting, toasts
bridge.py           WebSocket-to-serial relay (pyserial + websockets)
serve.py            Local dev server
```

## KCP Protocol

Serial config: 9600 baud, 8N1, no parity.

| Command | Description |
|---------|-------------|
| `SI`    | Send weight immediately (stable or dynamic) |
| `S`     | Send stable weight (waits for stability) |
| `T`     | Tare — store current weight as tare value |
| `Z`     | Zero — set new zero reference point |
| `@`     | Reset (like power cycling) |
| `SIR`   | Send weight immediately, repeat continuously |

The full command reference is built into the app under **KCP Command Reference**.

## Dependencies

**Browser:** None — everything loads from CDN or is vanilla JS.

**Python tools** (managed automatically by `uv` via PEP 723 inline metadata):
- `bridge.py` — `pyserial`, `websockets`
- `serve.py` — stdlib only

## Deployment

Deployed to GitHub Pages on push to `main` via `.github/workflows/static.yml`.
