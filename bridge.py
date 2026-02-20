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
import getpass
import re
import sys

import serial
import serial.tools.list_ports
import websockets


BAUD_RATE = 9600
WS_HOST = "localhost"
WS_PORT = 8765

# Matches value + optional unit from Kern scale output, e.g. "  123.45 g"
WEIGHT_RE = re.compile(r"(-?\d+(?:\.\d+)?)\s*([a-zA-Z%/]*)")

# InfluxDB state (set by setup_influxdb)
_influx = None  # dict with write_api, bucket, org, measurement, client


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
    return serial.Serial(
        port=port_name,
        baudrate=BAUD_RATE,
        bytesize=serial.EIGHTBITS,
        stopbits=serial.STOPBITS_ONE,
        parity=serial.PARITY_NONE,
        timeout=0.1,
    )


def setup_influxdb():
    """Interactively configure InfluxDB logging. Returns config dict or None."""
    global _influx
    try:
        answer = input("\nEnable InfluxDB logging? [y/N]: ").strip().lower()
    except EOFError:
        return None
    if answer != "y":
        return None

    from influxdb_client import InfluxDBClient

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

    write_api = client.write_api()
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
        print(f"  InfluxDB write error: {e}")


async def serial_to_ws(ser, ws):
    """Read lines from serial and send to WebSocket."""
    loop = asyncio.get_event_loop()
    buffer = ""
    while True:
        # Read available bytes in a thread to avoid blocking the event loop
        data = await loop.run_in_executor(None, ser.read, 256)
        if data:
            buffer += data.decode("ascii", errors="replace")
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                line = line.strip()
                if line:
                    try:
                        await ws.send(line)
                    except websockets.ConnectionClosed:
                        return
                    write_influx_point(line)
        else:
            await asyncio.sleep(0.05)


async def ws_to_serial(ser, ws):
    """Read commands from WebSocket and write to serial."""
    try:
        async for message in ws:
            cmd = message.strip()
            if cmd:
                ser.write((cmd + "\r\n").encode("ascii"))
                print(f"  → Sent to scale: {cmd}")
    except websockets.ConnectionClosed:
        pass


async def handler(ws, ser):
    """Handle a single WebSocket connection."""
    peer = getattr(ws, "remote_address", None)
    print(f"  Client connected: {peer}")
    try:
        await asyncio.gather(
            serial_to_ws(ser, ws),
            ws_to_serial(ser, ws),
        )
    finally:
        print(f"  Client disconnected: {peer}")


async def main():
    port_name = sys.argv[1] if len(sys.argv) > 1 else find_serial_port()
    if not port_name:
        print("No serial ports found. Connect a scale and try again,")
        print("or specify the port: uv run bridge.py /dev/cu.usbserial-10")
        sys.exit(1)

    print(f"Opening serial port: {port_name} at {BAUD_RATE} baud")
    ser = open_serial(port_name)
    print(f"Serial port opened: {ser.name}")

    setup_influxdb()

    print(f"Starting WebSocket server on ws://{WS_HOST}:{WS_PORT}")
    print("Web app can now connect via the Bridge button.\n")

    async with websockets.serve(lambda ws: handler(ws, ser), WS_HOST, WS_PORT):
        await asyncio.Future()  # run forever


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        close_influxdb()
        print("\nBridge stopped.")
