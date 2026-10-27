"""
Serial Bridge: ESP32 -> VIDE backend
=====================================
Reads JSON reading lines from the ESP32 over USB serial and POSTs each
one to the local backend's /api/readings endpoint. No WiFi involved.

Setup:
    pip install pyserial requests

Usage:
    python serial_bridge.py

If the script can't find your port automatically, list ports with:
    python -m serial.tools.list_ports
and pass it manually:
    python serial_bridge.py --port COM5
"""

import argparse
import sys
import time

import requests
import serial
import serial.tools.list_ports

BACKEND_URL = "http://127.0.0.1:8000/api/readings"
BAUD_RATE = 115200


def find_esp32_port():
    ports = list(serial.tools.list_ports.comports())
    if not ports:
        return None
    # Prefer anything that looks like a USB-serial adapter (most ESP32 boards use CP210x/CH340)
    for p in ports:
        desc = (p.description or "").lower()
        if any(hint in desc for hint in ("cp210", "ch340", "usb serial", "esp32", "silicon labs")):
            return p.device
    return ports[0].device  # fallback: first available port


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", help="Serial port, e.g. COM5 or /dev/ttyUSB0")
    parser.add_argument("--backend", default=BACKEND_URL, help="Backend readings endpoint")
    args = parser.parse_args()

    port = args.port or find_esp32_port()
    if not port:
        print("No serial port found. Plug in the ESP32 and try again, or pass --port explicitly.")
        sys.exit(1)

    print(f"Opening {port} at {BAUD_RATE} baud...")
    try:
        ser = serial.Serial(port, BAUD_RATE, timeout=2)
    except serial.SerialException as e:
        print(f"Could not open {port}: {e}")
        print("Port is busy or locked. Close Arduino Serial Monitor / Plotter,")
        print("stop other 'connector.py' instances, then unplug/replug the ESP32.")
        print("Available ports:")
        for p in serial.tools.list_ports.comports():
            print(f"  {p.device} - {p.description}")
        print("Retry with: python connector.py --port COMx")
        sys.exit(1)
    time.sleep(2)  # let the ESP32 reset after the port opens

    print(f"Forwarding readings to {args.backend}")
    print("Press Ctrl+C to stop.\n")

    while True:
        try:
            raw = ser.readline().decode("utf-8", errors="ignore").strip()
            if not raw:
                continue

            if raw == "READY":
                print("[ESP32] booted and ready")
                continue

            if not raw.startswith("READING:"):
                print(f"[ESP32] {raw}")  # any other debug output, just echo it
                continue

            payload_str = raw[len("READING:"):]

            try:
                import json
                payload = json.loads(payload_str)
            except json.JSONDecodeError:
                print(f"Skipping malformed line: {payload_str}")
                continue

            resp = requests.post(args.backend, json=payload, timeout=5)
            if resp.status_code in (200, 201):
                print(f"OK  -> {payload.get('machine_name')}  "
                      f"V={payload.get('voltage_v'):.1f} "
                      f"I={payload.get('current_amps'):.2f} "
                      f"kW={payload.get('power_kw'):.3f}")
            else:
                print(f"Backend returned {resp.status_code}: {resp.text}")

        except requests.exceptions.RequestException as e:
            print(f"Could not reach backend: {e}")
        except KeyboardInterrupt:
            print("\nStopping bridge.")
            break
        except Exception as e:
            print(f"Unexpected error: {e}")


if __name__ == "__main__":
    main()