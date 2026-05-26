#!/usr/bin/env python3
"""
Lab demo: angle 0 -> spray angle -> wait (from decision duration, capped 1-10 s) -> angle 0.

Uses the same irrigation decision as analyze_soil.py (rules + optional ML).
Assumes angle 0 on hp_tk_rx stops water (GPIO2 LOW); non-zero allows spray.

  python3 scripts/hp_tk_spray_experiment.py --list-ports
  python3 scripts/hp_tk_spray_experiment.py --csv "12.1,0.4,0.0,28,22.5,41" \
    --city "San Jose" --port /dev/cu.usbserial-XXX --dry-run
  python3 scripts/hp_tk_spray_experiment.py --csv "..." --city "San Jose" \
    --port /dev/cu.usbserial-XXX --angle 30 --spray-seconds 5

hp_tk_tx must already be connected to hp_tk_rx over BLE.
"""

from __future__ import annotations

import argparse
import json
import sys
import time

import _bootstrap  # noqa: F401

from services.irrigation import get_final_decision_api


def list_serial_ports() -> list[str]:
    try:
        from serial.tools import list_ports
    except ImportError as exc:
        raise SystemExit("Install pyserial: python3 -m pip install pyserial") from exc
    return [p.device for p in list_ports.comports()]


def _import_serial():
    try:
        import serial
    except ImportError as exc:
        raise SystemExit("Install pyserial: python3 -m pip install pyserial") from exc
    return serial


def open_hp_tk_serial(port: str, *, baud: int = 115200):
    """Open tx USB once; dsrdtr=False avoids resetting ESP32 on macOS."""
    serial = _import_serial()
    try:
        return serial.Serial(
            port,
            baud,
            timeout=2,
            dsrdtr=False,
            rtscts=False,
        )
    except serial.SerialException as exc:
        err = str(exc).lower()
        errno = getattr(exc, "errno", None)
        if errno == 16 or "busy" in err:
            raise SystemExit(
                f"Cannot open {port}: port is busy.\n"
                "Close Arduino Serial Monitor, Cursor/VS Code serial terminals, "
                "screen/minicom, or any other app using this USB port, then retry."
            ) from exc
        if errno == 2 or "no such file" in err:
            found = ", ".join(list_serial_ports()) or "(none — is hp_tk_tx USB plugged in?)"
            raise SystemExit(
                f"Cannot open {port}: port not found.\n"
                "macOS often changes usbserial numbers after unplug/replug.\n"
                f"Current ports: {found}\n"
                "Plug USB into hp_tk_tx (ESP32A), run: python3 scripts/hp_tk_spray_experiment.py --list-ports"
            ) from exc
        raise


def write_angle(ser, angle: int) -> None:
    if not 0 <= angle <= 180:
        raise ValueError(f"angle must be 0-180, got {angle}")
    ser.write(f"{angle}\n".encode("ascii"))
    ser.flush()


def send_angle(port: str, angle: int, *, baud: int = 115200) -> None:
    with open_hp_tk_serial(port, baud=baud) as ser:
        write_angle(ser, angle)


def duration_mapping(
    payload: dict,
    *,
    min_seconds: float = 1.0,
    max_seconds: float = 10.0,
    override: float | None = None,
) -> dict[str, float]:
    """Production duration from decision vs lab spray time (shortened on purpose)."""
    minutes = float(payload.get("duration_minutes") or 0)
    production_s = minutes * 60.0
    if production_s <= 0 and payload.get("duration_seconds"):
        production_s = float(payload["duration_seconds"])

    if override is not None:
        lab_s = max(min_seconds, min(max_seconds, override))
    else:
        lab_s = max(min_seconds, min(max_seconds, production_s))

    return {
        "production_seconds": production_s,
        "lab_seconds": lab_s,
        "min_cap": min_seconds,
        "max_cap": max_seconds,
    }


def print_summary(
    payload: dict,
    *,
    angle_spray: int,
    timing: dict[str, float],
    settle_s: float,
    dry_run: bool,
) -> None:
    sprinkler_on = bool(payload.get("sprinkler_on"))
    prod_s = timing["production_seconds"]
    lab_s = timing["lab_seconds"]

    print("=" * 60)
    print("  IRRIGATION DECISION (real values from rules + ML)")
    print("=" * 60)
    print(f"  Sprinkler ON:          {payload.get('sprinkler_on')}")
    print(f"  Duration (label):      {payload.get('duration')}")
    print(f"  Duration (minutes):    {payload.get('duration_minutes')}")
    print(f"  Duration (seconds):    {payload.get('duration_seconds')}")
    print(f"  Decision source:       {payload.get('decision_source')}")
    if payload.get("days_to_next_watering") is not None:
        print(f"  Days to next (ML):     {payload.get('days_to_next_watering')}")
    if payload.get("skip_reason"):
        print(f"  Skip reason:           {payload.get('skip_reason')}")
    ml = payload.get("ml") or {}
    if ml.get("notes"):
        print("  ML notes:")
        for note in ml["notes"]:
            print(f"    - {note}")

    print()
    print("=" * 60)
    print("  LAB DEMO (intentionally shortened for bench / safety)")
    print("=" * 60)
    print("  Production would run the full duration above (e.g. many minutes).")
    print("  This experiment only sprays for a few seconds so you can watch")
    print("  the relay/servo without flooding the bench.")
    print()
    print(f"  Production run time:   {prod_s:.0f} s  ({prod_s / 60:.2f} min)")
    print(f"  This demo run time:    {lab_s:.1f} s  (capped {timing['min_cap']:.0f}-{timing['max_cap']:.0f} s)")
    if prod_s > lab_s and sprinkler_on:
        print(f"  Scale factor:          ~{prod_s / lab_s:.0f}x shorter than production")
    vision = payload.get("vision") or {}
    if vision.get("source", "").startswith("vision_grass"):
        print(f"  Nozzle angles:         0 -> {angle_spray} -> 0  (YOLO grass aim)")
    else:
        print(f"  Nozzle angles:         0 -> {angle_spray} -> 0  (0 = stop on your wiring)")
    if vision.get("notes"):
        for note in vision["notes"]:
            print(f"    {note}")
    print(f"  Settle pauses:         {settle_s:.1f} s after each angle 0")
    if dry_run:
        print("  Mode:                  DRY-RUN (no serial writes)")
    print("=" * 60)
    print()


def run_experiment(
    *,
    port: str | None,
    payload: dict,
    angle_spray: int,
    spray_seconds: float,
    production_seconds: float,
    baud: int,
    settle_s: float,
    dry_run: bool,
) -> int:
    sprinkler_on = bool(payload.get("sprinkler_on"))

    if not sprinkler_on:
        print("\nNo spray phase: decision says OFF — sending angle 0 only.\n")
        if dry_run:
            print("[dry-run] Would send: 0")
        elif port:
            with open_hp_tk_serial(port, baud=baud) as ser:
                write_angle(ser, 0)
            print("Sent: 0")
        return 0

    phases = [
        ("start", 0, settle_s, "Stop / park nozzle, valve off (assumed)"),
        ("spray", angle_spray, spray_seconds, f"Spray at {angle_spray}°"),
        ("end", 0, settle_s, "Stop again"),
    ]

    def run_phases(ser=None) -> int:
        for name, angle, wait_s, note in phases:
            print(f"\n--- {name}: angle {angle} ({note}) ---")
            if name == "spray" and production_seconds > wait_s:
                print(
                    f"    (lab wait {wait_s:.1f} s; production decision is {production_seconds:.0f} s)"
                )
            if dry_run:
                print(f"[dry-run] Would send: {angle}, wait {wait_s:.1f} s")
            else:
                if ser is None:
                    print("Error: --port required", file=sys.stderr)
                    return 1
                write_angle(ser, angle)
                print(f"Sent: {angle}")
                if wait_s > 0:
                    time.sleep(wait_s)
                    print(f"Waited {wait_s:.1f} s")
        return 0

    if dry_run:
        return run_phases()

    if not port:
        print("Error: --port required", file=sys.stderr)
        return 1

    with open_hp_tk_serial(port, baud=baud) as ser:
        rc = run_phases(ser)
    print("\nExperiment complete.")
    return rc


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Timed spray demo: 0 -> angle -> wait (1-10s) -> 0"
    )
    parser.add_argument("--csv", help="Sensor CSV from heli_tx (required except with --list-ports)")
    parser.add_argument("--city")
    parser.add_argument("--lat", type=float)
    parser.add_argument("--lon", type=float)
    parser.add_argument(
        "--angle",
        type=int,
        default=30,
        help="Spray angle when ON if no --image (1-180)",
    )
    parser.add_argument(
        "--image",
        help="Camera frame: YOLO grass centroid sets spray angle",
    )
    parser.add_argument("--vision-weights", help="Path to YOLO .pt weights")
    parser.add_argument("--invert-x", action="store_true", help="Mirror grass x -> angle")
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
        "--spray-seconds",
        type=float,
        default=None,
        help="Override spray duration (still capped by min/max seconds)",
    )
    parser.add_argument("--min-seconds", type=float, default=1.0, help="Min spray time (default 1)")
    parser.add_argument("--max-seconds", type=float, default=10.0, help="Max spray time (default 10)")
    parser.add_argument("--settle-seconds", type=float, default=1.0, help="Pause after 0 at start/end")
    parser.add_argument("--port", help="USB serial for hp_tk_tx")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--no-ml", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--list-ports", action="store_true")
    args = parser.parse_args()

    if args.list_ports:
        for d in list_serial_ports():
            print(d)
        return 0

    if not args.csv:
        parser.error("--csv is required (omit only when using --list-ports)")

    if not 1 <= args.angle <= 180:
        print("Error: --angle must be 1-180 when spraying", file=sys.stderr)
        return 1
    if args.min_seconds > args.max_seconds:
        print("Error: --min-seconds must be <= --max-seconds", file=sys.stderr)
        return 1

    try:
        payload = get_final_decision_api(
            city=args.city,
            lat=args.lat,
            lon=args.lon,
            sensor=args.csv,
            use_ml=not args.no_ml,
            image=args.image,
            vision_weights=args.vision_weights,
            default_nozzle_angle=args.angle,
            vision_invert_x=args.invert_x,
            vision_angle_offset_deg=args.angle_offset,
            vision_angle_scale=args.angle_scale,
        )
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    spray_angle = 0
    if payload.get("sprinkler_on"):
        spray_angle = int(payload.get("nozzle_angle_deg") or args.angle)

    timing = duration_mapping(
        payload,
        min_seconds=args.min_seconds,
        max_seconds=args.max_seconds,
        override=args.spray_seconds,
    )
    spray_s = timing["lab_seconds"]

    print_summary(
        payload,
        angle_spray=spray_angle,
        timing=timing,
        settle_s=args.settle_seconds,
        dry_run=args.dry_run,
    )

    if args.json:
        print(
            json.dumps(
                {
                    **payload,
                    "experiment": {
                        "note": "Lab demo uses shortened spray time; production uses full duration above.",
                        "production_spray_seconds": timing["production_seconds"],
                        "lab_spray_seconds": spray_s,
                        "lab_cap_seconds": [timing["min_cap"], timing["max_cap"]],
                        "spray_angle": spray_angle if payload.get("sprinkler_on") else 0,
                        "sequence": ["0", str(spray_angle), "0"]
                        if payload.get("sprinkler_on")
                        else ["0"],
                    },
                },
                indent=2,
            )
        )
        if args.dry_run:
            return 0

    return run_experiment(
        port=args.port,
        payload=payload,
        angle_spray=spray_angle,
        spray_seconds=spray_s,
        production_seconds=timing["production_seconds"],
        baud=args.baud,
        settle_s=args.settle_seconds,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    raise SystemExit(main())
