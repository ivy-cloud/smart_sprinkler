#!/usr/bin/env python3
"""
Run irrigation decision (rules + optional ML) and send nozzle angle to hp_tk_tx over USB.

Assumption (your hardware): angle 0 on hp_tk_rx stops water (GPIO2 LOW); non-zero allows flow.

  python3 scripts/irrigation_to_hp_tk.py --csv "12.1,0.4,0.0,28,22.5,41" --city "San Jose"
  python3 scripts/irrigation_to_hp_tk.py --csv "..." --city "San Jose" --port /dev/cu.usbserial-XXX
  python3 scripts/irrigation_to_hp_tk.py --csv "..." --city "San Jose" --dry-run

Requires: pip install pyserial
hp_tk_tx must be connected to hp_tk_rx over BLE before angles take effect on the sprinkler.
"""

from __future__ import annotations

import argparse
import json
import sys

import _bootstrap  # noqa: F401

from services.irrigation import get_final_decision_api


def list_serial_ports() -> list[str]:
    try:
        from serial.tools import list_ports
    except ImportError as exc:
        raise SystemExit("Install pyserial: python3 -m pip install pyserial") from exc
    return [p.device for p in list_ports.comports()]


def send_angle(port: str, angle: int, *, baud: int = 115200) -> None:
    try:
        import serial
    except ImportError as exc:
        raise SystemExit("Install pyserial: python3 -m pip install pyserial") from exc

    line = f"{angle}\n".encode("ascii")
    with serial.Serial(
        port, baud, timeout=2, dsrdtr=False, rtscts=False
    ) as ser:
        ser.write(line)
        ser.flush()
    print(f"Sent angle {angle} to {port} ({baud} baud)")


def decision_to_angle(payload: dict, *, angle_when_on: int) -> tuple[int, str]:
    """Map merged decision -> hp_tk angle (0 = stop on your wiring)."""
    if payload.get("sprinkler_on"):
        return angle_when_on, "irrigation ON"
    return 0, payload.get("skip_reason") or "irrigation OFF"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Irrigation decision -> serial angle for hp_tk_tx -> BLE -> hp_tk_rx"
    )
    parser.add_argument(
        "--csv",
        help="Sensor line from heli_tx / STM32 (required except with --list-ports)",
    )
    parser.add_argument("--city")
    parser.add_argument("--lat", type=float)
    parser.add_argument("--lon", type=float)
    parser.add_argument("--base-minutes", type=float, default=20)
    parser.add_argument("--flow-gpm", type=float, default=8.0)
    parser.add_argument(
        "--angle-on",
        type=int,
        default=90,
        help="Servo angle when sprinkler_on (1-180, default 90)",
    )
    parser.add_argument(
        "--port",
        help="USB serial port for ESP32 running hp_tk_tx (e.g. /dev/cu.usbserial-*)",
    )
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--no-ml", action="store_true", help="Rules-only decision")
    parser.add_argument("--dry-run", action="store_true", help="Print decision only, no serial")
    parser.add_argument("--json", action="store_true", help="Print full decision JSON")
    parser.add_argument(
        "--list-ports",
        action="store_true",
        help="List serial ports and exit",
    )
    args = parser.parse_args()

    if args.list_ports:
        for device in list_serial_ports():
            print(device)
        return 0

    if not args.csv:
        parser.error("--csv is required (omit only when using --list-ports)")

    if args.angle_on < 1 or args.angle_on > 180:
        print("Error: --angle-on must be 1-180", file=sys.stderr)
        return 1

    try:
        payload = get_final_decision_api(
            city=args.city,
            lat=args.lat,
            lon=args.lon,
            sensor=args.csv,
            base_minutes=args.base_minutes,
            flow_gpm=args.flow_gpm,
            use_ml=not args.no_ml,
        )
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    angle, reason = decision_to_angle(payload, angle_when_on=args.angle_on)

    if args.json:
        out = dict(payload)
        out["hp_tk_angle"] = angle
        out["hp_tk_reason"] = reason
        print(json.dumps(out, indent=2))
    else:
        print("=== Irrigation -> hp_tk ===")
        print(f"Sprinkler ON:     {payload.get('sprinkler_on')}")
        print(f"Duration:         {payload.get('duration')}")
        print(f"Decision source:  {payload.get('decision_source')}")
        if payload.get("days_to_next_watering") is not None:
            print(f"Next water (ML):  {payload.get('days_to_next_watering')} days")
        print(f"hp_tk angle:      {angle}  ({reason})")

    if args.dry_run:
        print("(dry-run: serial not written)")
        return 0

    if not args.port:
        ports = list_serial_ports()
        print(
            "Error: pass --port (e.g. from --list-ports). "
            f"Found: {', '.join(ports) if ports else 'none'}",
            file=sys.stderr,
        )
        return 1

    try:
        send_angle(args.port, angle, baud=args.baud)
    except Exception as exc:
        print(f"Serial error: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
