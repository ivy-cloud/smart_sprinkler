#!/usr/bin/env python3
"""
Bench experiment: compare nozzle angles with / without camera frames, then optional hp_tk_tx.

Three cases (always run in order):
  1. No image     → fixed fallback angle (default 90°), no YOLO
  2. --image-a    → YOLO grass centroid → angle (if weights + file exist)
  3. --image-b    → same

With --port, each angle is sent over USB to hp_tk_tx → BLE → hp_tk_rx.

Examples:
  # Preview only (no hardware)
  python3 scripts/vision_angle_experiment.py \\
    --image-a examples/vision/frame_grass_left.jpg \\
    --image-b examples/vision/frame_grass_right.jpg

  # No images → only case 1 (90°)
  python3 scripts/vision_angle_experiment.py --dry-run

  # Send angles to hp_tk_tx (reset tx after rx is on; close Serial Monitor first)
  python3 scripts/vision_angle_experiment.py \\
    --image-a examples/vision/frame_grass_left.jpg \\
    --image-b examples/vision/frame_grass_right.jpg \\
    --port /dev/cu.usbserial-XXXX --pause 4

See examples/vision/README.md to obtain test frames.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import _bootstrap  # noqa: F401

from hp_tk_serial import list_serial_ports, open_hp_tk_serial, write_angle

from services.irrigation import aim_from_image, vision_weights_available


@dataclass
class AngleCase:
    label: str
    image: str | None
    angle: int
    source: str
    notes: list[str]
    vision: dict[str, Any] | None = None


def resolve_angle_for_case(
    *,
    label: str,
    image: str | None,
    fallback_angle: int,
    weights: str | None,
    min_confidence: float,
    min_area_px: float,
    invert_x: bool,
    angle_offset: float,
    angle_scale: float,
) -> AngleCase:
    if image is None:
        angle = max(1, min(180, fallback_angle))
        return AngleCase(
            label=label,
            image=None,
            angle=angle,
            source="fixed_fallback_no_image",
            notes=[
                "No camera frame supplied; YOLO is not run.",
                f"Using fallback angle {angle}° (pass-through to hp_tk_tx when --port set).",
            ],
        )

    path = Path(image).expanduser()
    if not path.is_file():
        angle = max(1, min(180, fallback_angle))
        return AngleCase(
            label=label,
            image=str(path),
            angle=angle,
            source="fixed_fallback_missing_file",
            notes=[f"Image not found: {path}", f"Using fallback {angle}°."],
        )

    if not vision_weights_available(weights):
        angle = max(1, min(180, fallback_angle))
        return AngleCase(
            label=label,
            image=str(path),
            angle=angle,
            source="fixed_fallback_no_weights",
            notes=[
                "YOLO weights not found (train segmentation or set SMART_SPRINKLER_VISION_WEIGHTS).",
                f"Using fallback {angle}°.",
            ],
        )

    try:
        vision = aim_from_image(
            path,
            weights=weights,
            fallback_angle=fallback_angle,
            min_confidence=min_confidence,
            min_area_px=min_area_px,
            invert_x=invert_x,
            angle_offset_deg=angle_offset,
            angle_scale=angle_scale,
        )
    except Exception as exc:
        angle = max(1, min(180, fallback_angle))
        return AngleCase(
            label=label,
            image=str(path),
            angle=angle,
            source="fixed_fallback_vision_error",
            notes=[f"Vision error: {exc}", f"Using fallback {angle}°."],
        )

    angle = max(1, min(180, int(vision.get("angle_deg", fallback_angle))))
    source = str(vision.get("source", "vision"))
    notes = list(vision.get("notes") or [])
    return AngleCase(
        label=label,
        image=str(path),
        angle=angle,
        source=source,
        notes=notes,
        vision=vision,
    )


def print_cases(cases: list[AngleCase]) -> None:
    print("=" * 64)
    print("  VISION ANGLE EXPERIMENT")
    print("=" * 64)
    for i, case in enumerate(cases, 1):
        print(f"\n  Case {i}: {case.label}")
        print(f"    Image:   {case.image or '(none)'}")
        print(f"    Angle:   {case.angle}°")
        print(f"    Source:  {case.source}")
        for note in case.notes:
            print(f"      - {note}")
    print("\n" + "=" * 64)
    print("  Without images you always get the fallback (default 90°).")
    print("  With two frames, grass left vs right in the photo should give different angles.")
    print("=" * 64 + "\n")


def send_cases(
    cases: list[AngleCase],
    *,
    port: str,
    baud: int,
    pause_s: float,
    park_at_zero_first: bool,
    park_at_zero_end: bool,
) -> None:
    with open_hp_tk_serial(port, baud=baud) as ser:
        if park_at_zero_first:
            print("--- park: angle 0 ---")
            write_angle(ser, 0)
            time.sleep(pause_s)

        for case in cases:
            print(f"--- send: {case.label} → {case.angle}° ---")
            write_angle(ser, case.angle)
            time.sleep(pause_s)

        if park_at_zero_end:
            print("--- park: angle 0 ---")
            write_angle(ser, 0)

    print(f"\nSent {len(cases)} angle(s) to {port} (pause {pause_s}s between steps).")


def build_cases(args: argparse.Namespace) -> list[AngleCase]:
    cases: list[AngleCase] = [
        resolve_angle_for_case(
            label="no_image (fallback)",
            image=None,
            fallback_angle=args.fallback_angle,
            weights=args.vision_weights,
            min_confidence=args.min_confidence,
            min_area_px=args.min_area_px,
            invert_x=args.invert_x,
            angle_offset=args.angle_offset,
            angle_scale=args.angle_scale,
        )
    ]
    if args.image_a:
        cases.append(
            resolve_angle_for_case(
                label="image_a",
                image=args.image_a,
                fallback_angle=args.fallback_angle,
                weights=args.vision_weights,
                min_confidence=args.min_confidence,
                min_area_px=args.min_area_px,
                invert_x=args.invert_x,
                angle_offset=args.angle_offset,
                angle_scale=args.angle_scale,
            )
        )
    if args.image_b:
        cases.append(
            resolve_angle_for_case(
                label="image_b",
                image=args.image_b,
                fallback_angle=args.fallback_angle,
                weights=args.vision_weights,
                min_confidence=args.min_confidence,
                min_area_px=args.min_area_px,
                invert_x=args.invert_x,
                angle_offset=args.angle_offset,
                angle_scale=args.angle_scale,
            )
        )
    return cases


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compare vision angles (no image vs two frames) and optional hp_tk_tx send"
    )
    parser.add_argument(
        "--image-a",
        help="Frame with grass mainly on the left (expects lower angle)",
    )
    parser.add_argument(
        "--image-b",
        help="Frame with grass mainly on the right (expects higher angle)",
    )
    parser.add_argument(
        "--fallback-angle",
        type=int,
        default=90,
        help="Angle when no image or vision unavailable (default 90)",
    )
    parser.add_argument("--vision-weights", help="Path to YOLO .pt weights")
    parser.add_argument("--min-confidence", type=float, default=0.25)
    parser.add_argument("--min-area-px", type=float, default=400.0)
    parser.add_argument("--invert-x", action="store_true")
    parser.add_argument("--angle-offset", type=float, default=0.0)
    parser.add_argument("--angle-scale", type=float, default=1.0)
    parser.add_argument("--port", help="hp_tk_tx USB serial port")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument(
        "--pause",
        type=float,
        default=3.0,
        help="Seconds between serial writes (default 3)",
    )
    parser.add_argument(
        "--no-park-zero",
        action="store_true",
        help="Do not send 0 before/after the experiment sequence",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print angles only, no serial")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--list-ports", action="store_true")
    args = parser.parse_args()

    if args.list_ports:
        for device in list_serial_ports():
            print(device)
        return 0

    if not 1 <= args.fallback_angle <= 180:
        print("Error: --fallback-angle must be 1-180", file=sys.stderr)
        return 1

    cases = build_cases(args)
    print_cases(cases)

    if args.json:
        print(
            json.dumps(
                [
                    {
                        "label": c.label,
                        "image": c.image,
                        "angle_deg": c.angle,
                        "source": c.source,
                        "notes": c.notes,
                        "vision": c.vision,
                    }
                    for c in cases
                ],
                indent=2,
            )
        )

    if args.dry_run or not args.port:
        if not args.dry_run and not args.port:
            print("No --port: angles printed only. Add --port to send to hp_tk_tx.")
        return 0

    send_cases(
        cases,
        port=args.port,
        baud=args.baud,
        pause_s=args.pause,
        park_at_zero_first=not args.no_park_zero,
        park_at_zero_end=not args.no_park_zero,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
