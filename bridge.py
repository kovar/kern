#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "pyserial",
#     "websockets",
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
import sys

import serial
import serial.tools.list_ports
import websockets


BAUD_RATE = 9600
WS_HOST = "localhost"
WS_PORT = 8765


def find_serial_port():
    """List available serial ports. If more than one, prompt the user to pick."""
    ports = list(serial.tools.list_ports.comports())
    if not ports:
        return None
    if len(ports) == 1:
        print(f"Found serial port: {ports[0].device}  —  {ports[0].description}")
        return ports[0].device
    print("Multiple serial ports found:\n")
    for i, p in enumerate(ports, 1):
        print(f"  [{i}]  {p.device}  —  {p.description}")
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

    print(f"Starting WebSocket server on ws://{WS_HOST}:{WS_PORT}")
    print("Web app can now connect via the Bridge button.\n")

    async with websockets.serve(lambda ws: handler(ws, ser), WS_HOST, WS_PORT):
        await asyncio.Future()  # run forever


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nBridge stopped.")
