#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "pyserial",
#     "websockets",
#     "influxdb-client",
# ]
# ///
"""
bridge.py — WebSocket ↔ Serial bridge for Kern scales.

Allows non-Chromium browsers (Firefox, Safari) to communicate with a
serial scale by relaying data between a WebSocket and a serial port.

Usage:
    uv run bridge.py                        # auto-detect serial port
    uv run bridge.py /dev/cu.usbserial-10   # specify port
    uv run bridge.py COM3                   # Windows

The web app connects to ws://localhost:8765 (default).
"""

import asyncio
import datetime
import getpass
import os
import re
import shutil
import signal
import sys

import serial
import serial.tools.list_ports
import websockets


BAUD_RATE = 9600
WS_HOST = "localhost"
WS_PORT = 8765
TUI_ROWS = 12  # fixed terminal rows used by TUI

# Matches value + optional unit from Kern scale output, e.g. "  123.45 g"
WEIGHT_RE = re.compile(r"(-?\d+(?:\.\d+)?)\s*([a-zA-Z%/]*)")

# ─────────────────────────────────────────────────────────────────────────────
# USER CONFIGURATION
# Hard-code values here to skip the interactive prompts at startup.
# Leave a field as None to be prompted interactively.
# ─────────────────────────────────────────────────────────────────────────────
SERIAL_PORT          = None   # e.g. "/dev/ttyUSB0" or "/dev/serial/by-id/usb-..."
INFLUXDB_URL         = None   # e.g. "http://localhost:8086"
INFLUXDB_ORG         = None   # e.g. "my-org"
INFLUXDB_BUCKET      = None   # e.g. "sensors"
INFLUXDB_TOKEN       = None   # e.g. "my-token=="
INFLUXDB_MEASUREMENT = None   # e.g. "kern_scale"
# ─────────────────────────────────────────────────────────────────────────────

# InfluxDB state (set by setup_influxdb)
_influx = None  # dict with write_api, bucket, org, measurement, client

# ─────────────────────────────────────────────────────────────────────────────
# TUI STATE
# ─────────────────────────────────────────────────────────────────────────────
_tui_active           = False
_tui_weight           = None          # last parsed weight value (float)
_tui_unit             = None          # last parsed unit string
_tui_client           = None          # connected client IP string or None
_tui_influx_desc      = "disabled"    # "disabled" or "enabled (name)"
_tui_transport_desc   = ""
_tui_input_buf        = ""
_tui_last_update      = ""
_tui_term_state       = None          # saved termios state for restore
_tui_loop             = None          # event loop reference set in tui_start()
_tui_send_func        = None          # async def(cmd: str) -> str, set in main()
_tui_response_queue   = None          # asyncio.Queue for manual command responses
_tui_waiting_for_resp = False         # True while _tui_send_func awaits response
_serial_lock          = None          # asyncio.Lock, initialized in main()
_tui_w                = 80            # current terminal width


# ─────────────────────────────────────────────────────────────────────────────
# TUI HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _tui_can_use():
    """Return True if terminal TUI is supported on this system."""
    if os.name != "posix":
        return False
    if not sys.stdout.isatty():
        return False
    try:
        import tty as _t, termios as _m  # noqa: F401
        return True
    except ImportError:
        return False


def _tui_box_line(content, row):
    """Write a │-bordered content line at the given 1-indexed row."""
    inner = _tui_w - 2
    padded = content[:inner].ljust(inner)
    sys.stdout.write(f"\033[{row};1H\u2502{padded}\u2502")


def _tui_weight_line():
    """Format the current weight reading centered in the available width."""
    inner = _tui_w - 2
    if _tui_weight is None:
        s = "---"
    else:
        unit = f" {_tui_unit}" if _tui_unit else ""
        s = f"{_tui_weight:.3f}{unit}"
    return s.center(inner)


def _tui_position_cursor():
    """Move cursor to end of input on row 11: │ > {buf}│"""
    col = 5 + len(_tui_input_buf)  # 1=│  2=space  3=>  4=space  5+=input
    sys.stdout.write(f"\033[11;{col}H")


# ─────────────────────────────────────────────────────────────────────────────
# TUI LIFECYCLE
# ─────────────────────────────────────────────────────────────────────────────

def tui_start(transport_desc, influx_desc):
    """Initialize TUI: save terminal, setcbreak, hide cursor, draw frame."""
    global _tui_active, _tui_transport_desc, _tui_influx_desc
    global _tui_term_state, _tui_w, _tui_loop

    if not _tui_can_use():
        return
    cols, rows = shutil.get_terminal_size()
    if cols < 50 or rows < TUI_ROWS:
        return

    import tty, termios  # noqa: E401

    _tui_transport_desc = transport_desc
    _tui_influx_desc = influx_desc
    _tui_w = min(cols, 120)
    _tui_active = True

    fd = sys.stdin.fileno()
    _tui_term_state = termios.tcgetattr(fd)
    tty.setcbreak(fd)

    sys.stdout.write("\033[?25l\033[2J")
    sys.stdout.flush()
    tui_draw()

    _tui_loop = asyncio.get_event_loop()
    _tui_loop.add_reader(fd, _tui_on_stdin)
    try:
        _tui_loop.add_signal_handler(signal.SIGWINCH,
                                     lambda: (tui_draw(), sys.stdout.flush()))
    except (OSError, NotImplementedError):
        pass


def tui_stop():
    """Restore terminal to original state and show cursor."""
    global _tui_active, _tui_term_state

    if not _tui_active:
        return
    _tui_active = False

    if _tui_loop is not None and not _tui_loop.is_closed():
        try:
            _tui_loop.remove_reader(sys.stdin.fileno())
        except Exception:
            pass
        try:
            _tui_loop.remove_signal_handler(signal.SIGWINCH)
        except Exception:
            pass

    if _tui_term_state is not None:
        try:
            import termios
            termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, _tui_term_state)
        except Exception:
            pass

    sys.stdout.write(f"\033[?25h\033[{TUI_ROWS + 1};1H\033[J")
    sys.stdout.flush()


# ─────────────────────────────────────────────────────────────────────────────
# TUI DRAWING
# ─────────────────────────────────────────────────────────────────────────────

def tui_draw():
    """Full TUI redraw — used on startup and terminal resize."""
    global _tui_w

    if not _tui_active:
        return

    cols, _ = shutil.get_terminal_size()
    _tui_w = min(cols, 120)
    w = _tui_w
    inner = w - 2

    # Row 1: top border with title
    title = f" Kern Bridge  ws://{WS_HOST}:{WS_PORT}  [{_tui_transport_desc}] "
    fill = max(0, w - 2 - len(title) - 1)
    top = ("\u250c\u2500" + title + "\u2500" * fill + "\u2510")[:w]
    sys.stdout.write(f"\033[1;1H{top}")

    # Row 2: blank
    _tui_box_line("", 2)

    # Row 3: label
    _tui_box_line("Weight".center(inner), 3)

    # Row 4: blank
    _tui_box_line("", 4)

    # Row 5: current weight value
    _tui_box_line(_tui_weight_line(), 5)

    # Row 6: blank
    _tui_box_line("", 6)

    # Row 7: InfluxDB + client status
    influx_str = f"InfluxDB: {_tui_influx_desc}"
    client_str = ("Client: connected (" + _tui_client + ")"
                  if _tui_client else "Client: disconnected")
    gap = max(2, inner - 4 - len(influx_str) - len(client_str))
    _tui_box_line(f"  {influx_str}{' ' * gap}{client_str}", 7)

    # Row 8: blank
    _tui_box_line("", 8)

    # Row 9: last update time
    _tui_box_line(f"  Updated: {_tui_last_update or '--:--:--'}", 9)

    # Row 10: KCP command input divider
    div_title = " KCP Command "
    div_fill = max(0, w - 2 - len(div_title) - 1)
    div = ("\u251c\u2500" + div_title + "\u2500" * div_fill + "\u2524")[:w]
    sys.stdout.write(f"\033[10;1H{div}")

    # Row 11: input line
    _tui_box_line(f" > {_tui_input_buf}", 11)

    # Row 12: bottom border
    bot = ("\u2514" + "\u2500" * (w - 2) + "\u2518")[:w]
    sys.stdout.write(f"\033[12;1H{bot}")

    _tui_position_cursor()
    sys.stdout.flush()


def tui_update_reading(weight, unit):
    """Rewrite rows 5 and 9 with the latest scale reading."""
    global _tui_weight, _tui_unit, _tui_last_update

    _tui_weight = weight
    _tui_unit = unit

    if not _tui_active:
        return

    _tui_last_update = datetime.datetime.now().strftime("%H:%M:%S")
    inner = _tui_w - 2

    sys.stdout.write(f"\033[5;1H\u2502{_tui_weight_line()[:inner].ljust(inner)}\u2502")

    content9 = f"  Updated: {_tui_last_update}"
    sys.stdout.write(f"\033[9;1H\u2502{content9[:inner].ljust(inner)}\u2502")

    _tui_position_cursor()
    sys.stdout.flush()


def tui_update_client(peer, connected):
    """Update the client connection status display."""
    global _tui_client

    if connected:
        _tui_client = peer[0] if isinstance(peer, tuple) else str(peer)
    else:
        _tui_client = None

    if not _tui_active:
        if connected:
            print(f"  Client connected: {peer}")
        else:
            print(f"  Client disconnected: {peer}")
        return

    inner = _tui_w - 2
    influx_str = f"InfluxDB: {_tui_influx_desc}"
    client_str = ("Client: connected (" + _tui_client + ")"
                  if _tui_client else "Client: disconnected")
    gap = max(2, inner - 4 - len(influx_str) - len(client_str))
    status = f"  {influx_str}{' ' * gap}{client_str}"
    sys.stdout.write(f"\033[7;1H\u2502{status[:inner].ljust(inner)}\u2502")
    _tui_position_cursor()
    sys.stdout.flush()


def tui_redraw_input():
    """Rewrite the input line (row 11)."""
    if not _tui_active:
        return
    inner = _tui_w - 2
    content = f" > {_tui_input_buf}"
    sys.stdout.write(f"\033[11;1H\u2502{content[:inner].ljust(inner)}\u2502")
    _tui_position_cursor()
    sys.stdout.flush()


def _tui_show_response(resp):
    """Display a scale response in row 9 (replaces 'Updated' until next reading)."""
    if not _tui_active:
        return
    inner = _tui_w - 2
    content = f"  Response: {resp}"
    sys.stdout.write(f"\033[9;1H\u2502{content[:inner].ljust(inner)}\u2502")
    _tui_position_cursor()
    sys.stdout.flush()


# ─────────────────────────────────────────────────────────────────────────────
# TUI INPUT
# ─────────────────────────────────────────────────────────────────────────────

def _tui_on_stdin():
    """Sync add_reader callback: char-by-char line editor."""
    global _tui_input_buf

    try:
        ch = sys.stdin.read(1)
    except Exception:
        return
    if not ch:
        return

    if ch in ("\r", "\n"):
        cmd = _tui_input_buf.strip()
        _tui_input_buf = ""
        tui_redraw_input()
        if cmd:
            asyncio.ensure_future(_tui_dispatch_command(cmd))
    elif ch in ("\x7f", "\x08"):  # backspace / DEL
        _tui_input_buf = _tui_input_buf[:-1]
        tui_redraw_input()
    elif ch == "\x15":  # Ctrl-U: clear line
        _tui_input_buf = ""
        tui_redraw_input()
    elif "\x20" <= ch < "\x7f":  # printable ASCII
        _tui_input_buf += ch
        tui_redraw_input()


async def _tui_dispatch_command(cmd):
    """Send a TUI-entered KCP command to the scale and display the response."""
    if _tui_send_func is None:
        return
    try:
        resp = await _tui_send_func(cmd)
    except Exception as e:
        resp = f"(error: {e})"
    if resp:
        _tui_show_response(resp)


# ─────────────────────────────────────────────────────────────────────────────
# DEVICE DETECTION
# ─────────────────────────────────────────────────────────────────────────────

def _is_usb_port(p):
    """Return True if this port looks like a USB serial device.

    Checks VID/PID first (most reliable), then falls back to device name
    patterns for systems where pyserial doesn't populate VID/PID from sysfs.
    USB serial devices on Linux appear as /dev/ttyUSB* or /dev/ttyACM*;
    on macOS as /dev/cu.usbserial-* or /dev/cu.usbmodem*.
    """
    if p.vid is not None:
        return True
    name = p.device.lower()
    return any(s in name for s in ("ttyusb", "ttyacm", "cu.usb", "cu.wch"))


def find_serial_port():
    """List available serial ports, preferring USB devices.

    The Kern scale connects via USB and presents a virtual serial port.
    We show only USB ports by default and fall back to all ports if none found.
    """
    all_ports = list(serial.tools.list_ports.comports())
    if not all_ports:
        return None

    usb_ports = [p for p in all_ports if _is_usb_port(p)]
    ports = usb_ports if usb_ports else all_ports
    if not usb_ports:
        print("No USB serial devices found — showing all ports:")

    if len(ports) == 1:
        tag = " [USB]" if _is_usb_port(ports[0]) else ""
        print(f"Found serial port: {ports[0].device}{tag}  —  {ports[0].description}")
        return ports[0].device

    print("USB serial devices found:\n")
    for i, p in enumerate(ports, 1):
        vid_pid = f"  VID:PID={p.vid:04X}:{p.pid:04X}" if p.vid is not None else ""
        print(f"  [{i}]  {p.device}  —  {p.description}{vid_pid}")
    print()
    while True:
        try:
            choice = input(f"Type a number [1-{len(ports)}] and press Enter: ").strip()
            idx = int(choice) - 1
            if 0 <= idx < len(ports):
                return ports[idx].device
        except (ValueError, EOFError):
            pass
        print(f"  Please enter a number between 1 and {len(ports)}")


def open_serial(port_name):
    """Open serial port with Kern default settings."""
    try:
        return serial.Serial(
            port=port_name,
            baudrate=BAUD_RATE,
            bytesize=serial.EIGHTBITS,
            stopbits=serial.STOPBITS_ONE,
            parity=serial.PARITY_NONE,
            timeout=0.1,
            exclusive=True,
        )
    except serial.SerialException as e:
        print(f"Cannot open {port_name}: {e}")
        print("Is another bridge already using this port?")
        sys.exit(1)


# ─────────────────────────────────────────────────────────────────────────────
# INFLUXDB
# ─────────────────────────────────────────────────────────────────────────────

def setup_influxdb():
    """Interactively configure InfluxDB logging. Returns config dict or None."""
    global _influx

    # Use pre-configured values if all USER CONFIGURATION fields are set
    if all([INFLUXDB_URL, INFLUXDB_ORG, INFLUXDB_BUCKET, INFLUXDB_TOKEN, INFLUXDB_MEASUREMENT]):
        url = INFLUXDB_URL
        org = INFLUXDB_ORG
        bucket = INFLUXDB_BUCKET
        token = INFLUXDB_TOKEN
        measurement = INFLUXDB_MEASUREMENT
        print(f"\nUsing pre-configured InfluxDB: {org}/{bucket}/{measurement}")
    else:
        try:
            answer = input("\nEnable InfluxDB logging? [y/N]: ").strip().lower()
        except EOFError:
            return None
        if answer != "y":
            return None

        print("\n── InfluxDB Setup ──────────────────────────────────")
        url = input("URL [http://localhost:8086]: ").strip() or "http://localhost:8086"
        org = input("Organization: ").strip()
        bucket = input("Bucket: ").strip()
        print("API Token")
        print("  (Find yours at: InfluxDB UI → Load Data → API Tokens)")
        token = getpass.getpass("  Token: ")
        measurement = input("Measurement name: ").strip()
        print("  Use snake_case, e.g. kern_lab1")

        if not all([org, bucket, token, measurement]):
            print("Missing required fields — InfluxDB logging disabled.")
            return None

    from influxdb_client import InfluxDBClient

    print("\nTesting connection... ", end="", flush=True)
    client = InfluxDBClient(url=url, token=token, org=org)
    try:
        health = client.health()
        if health.status != "pass":
            print(f"✗ ({health.message})")
            client.close()
            return None
    except Exception as e:
        print(f"✗ ({e})")
        client.close()
        return None
    print("✓")

    from influxdb_client.client.write_api import SYNCHRONOUS
    write_api = client.write_api(write_options=SYNCHRONOUS)
    _influx = {
        "client": client,
        "write_api": write_api,
        "bucket": bucket,
        "org": org,
        "measurement": measurement,
    }
    print(f"InfluxDB logging enabled → {org}/{bucket}/{measurement}\n")
    return _influx


def close_influxdb():
    """Flush pending writes and close the InfluxDB client."""
    global _influx
    if _influx:
        print("Flushing InfluxDB...", end=" ", flush=True)
        try:
            _influx["write_api"].close()
            _influx["client"].close()
        except Exception:
            pass
        print("done.")
        _influx = None


def write_influx_point(line):
    """Parse a scale reading and write an InfluxDB point."""
    if not _influx:
        return
    m = WEIGHT_RE.search(line)
    if not m:
        return
    try:
        value = float(m.group(1))
    except ValueError:
        return
    unit = m.group(2) or "unknown"

    from influxdb_client import Point

    point = (
        Point(_influx["measurement"])
        .tag("unit", unit)
        .field("value", value)
    )
    try:
        _influx["write_api"].write(
            bucket=_influx["bucket"],
            org=_influx["org"],
            record=point,
        )
    except Exception as e:
        if not _tui_active:
            print(f"  InfluxDB write error: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# TRANSPORT HANDLERS
# ─────────────────────────────────────────────────────────────────────────────

async def serial_to_ws(ser, ws):
    """Read lines from serial and send to WebSocket."""
    loop = asyncio.get_event_loop()
    buffer = ""
    while True:
        try:
            data = await loop.run_in_executor(None, ser.read, 256)
        except serial.SerialException as e:
            if not _tui_active:
                print(f"\n  Serial read error: {e}")
            return
        if data:
            buffer += data.decode("ascii", errors="replace")
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                line = line.strip()
                if line:
                    # Route to TUI response queue if a manual command is waiting
                    if _tui_waiting_for_resp and _tui_response_queue is not None:
                        _tui_response_queue.put_nowait(line)
                    try:
                        await ws.send(line)
                    except websockets.ConnectionClosed:
                        return
                    # Update TUI with parsed weight
                    m = WEIGHT_RE.search(line)
                    if m:
                        try:
                            tui_update_reading(float(m.group(1)), m.group(2) or "")
                        except ValueError:
                            pass
                    write_influx_point(line)
        else:
            await asyncio.sleep(0.05)


async def ws_to_serial(ser, ws):
    """Read commands from WebSocket and write to serial."""
    try:
        async for message in ws:
            cmd = message.strip()
            if cmd:
                async with _serial_lock:
                    try:
                        ser.write((cmd + "\r\n").encode("ascii"))
                    except serial.SerialException as e:
                        if not _tui_active:
                            print(f"\n  Serial write error: {e}")
                        return
    except websockets.ConnectionClosed:
        pass


async def handler(ws, ser):
    """Handle a single WebSocket connection."""
    peer = getattr(ws, "remote_address", None)
    tui_update_client(peer, True)
    try:
        await asyncio.gather(
            serial_to_ws(ser, ws),
            ws_to_serial(ser, ws),
        )
    finally:
        tui_update_client(peer, False)


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

async def main():
    global _serial_lock, _tui_send_func, _tui_response_queue

    _serial_lock = asyncio.Lock()
    _tui_response_queue = asyncio.Queue()

    if len(sys.argv) > 1:
        port_name = sys.argv[1]
    elif SERIAL_PORT:
        port_name = SERIAL_PORT
        print(f"Using pre-configured serial port: {port_name}")
    else:
        port_name = find_serial_port()
    if not port_name:
        print("No serial ports found. Connect a scale and try again,")
        print("or specify the port: uv run bridge.py /dev/cu.usbserial-10")
        sys.exit(1)

    print(f"Opening serial port: {port_name} at {BAUD_RATE} baud")
    ser = open_serial(port_name)
    print(f"Serial port opened: {ser.name}")

    influx_cfg = setup_influxdb()
    influx_desc = (f"enabled ({influx_cfg['measurement']})"
                   if influx_cfg else "disabled")

    async def _serial_send(cmd):
        global _tui_waiting_for_resp
        async with _serial_lock:
            try:
                ser.write((cmd + "\r\n").encode("ascii"))
            except serial.SerialException as e:
                return f"(serial error: {e})"
            _tui_waiting_for_resp = True
            try:
                resp = await asyncio.wait_for(_tui_response_queue.get(), timeout=2.0)
                return resp
            except asyncio.TimeoutError:
                return "(timeout)"
            finally:
                _tui_waiting_for_resp = False

    _tui_send_func = _serial_send

    print(f"Starting WebSocket server on ws://{WS_HOST}:{WS_PORT}")
    print("Web app can now connect via the Bridge button.\n")
    tui_start(f"serial: {ser.name}", influx_desc)

    async with websockets.serve(lambda ws: handler(ws, ser), WS_HOST, WS_PORT):
        await asyncio.Future()  # run forever


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        tui_stop()
        close_influxdb()
        print("\nBridge stopped.")
