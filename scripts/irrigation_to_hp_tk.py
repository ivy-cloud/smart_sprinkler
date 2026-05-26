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

from services.irrigation import get_final_decision_api, hp_tk_angle_from_decision


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
        help="Servo angle when sprinkler_on if no --image (1-180, default 90)",
    )
    parser.add_argument(
        "--image",
        help="Camera frame: YOLO grass centroid -> nozzle angle (overrides --angle-on when ON)",
    )
    parser.add_argument("--vision-weights", help="Path to YOLO .pt (default: search repo)")
    parser.add_argument(
        "--invert-x",
        action="store_true",
        help="Mirror horizontal grass position -> angle mapping",
    )
    # Bench calibration: camera left/center/right vs nozzle 0°/90°/180°.
    parser.add_argument(
        "--angle-offset",
        type=float,
        default=0.0,
        metavar="DEG",
        help="Degrees added after vision mapping (default 0)",
    )
    parser.add_argument(
        "--angle-scale",
        type=float,
        default=1.0,
        help="Scale vision sweep around 90° (default 1)",
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
            image=args.image,
            vision_weights=args.vision_weights,
            default_nozzle_angle=args.angle_on,
            vision_invert_x=args.invert_x,
            vision_angle_offset_deg=args.angle_offset,
            vision_angle_scale=args.angle_scale,
        )
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if args.image and payload.get("nozzle_angle_deg") is not None:
        angle = int(payload["nozzle_angle_deg"])
        vision = payload.get("vision")
        reason = (
            "irrigation ON (YOLO grass centroid)"
            if vision and str(vision.get("source", "")).startswith("vision_grass")
            else "irrigation ON (vision fallback)"
        )
    else:
        angle, reason, vision = hp_tk_angle_from_decision(
            payload,
            default_on_angle=args.angle_on,
            image=None,
            vision_weights=args.vision_weights,
            invert_x=args.invert_x,
        )

    if args.json:
        out = dict(payload)
        out["hp_tk_angle"] = angle
        out["hp_tk_reason"] = reason
        if vision:
            out["vision"] = vision
        print(json.dumps(out, indent=2))
    else:
        print("=== Irrigation -> hp_tk ===")
        print(f"Sprinkler ON:     {payload.get('sprinkler_on')}")
        print(f"Duration:         {payload.get('duration')}")
        print(f"Decision source:  {payload.get('decision_source')}")
        if payload.get("days_to_next_watering") is not None:
            print(f"Next water (ML):  {payload.get('days_to_next_watering')} days")
        print(f"hp_tk angle:      {angle}  ({reason})")
        if vision:
            for note in vision.get("notes") or []:
                print(f"  Vision: {note}")

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
