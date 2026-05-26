#!/usr/bin/env python3
"""Standalone: camera frame -> grass segmentation -> nozzle angle (0-180)."""

from __future__ import annotations

import argparse
import json
import sys

import _bootstrap  # noqa: F401

from services.irrigation import aim_from_image, vision_weights_available


def main() -> int:
    parser = argparse.ArgumentParser(description="YOLO grass mask -> nozzle angle")
    parser.add_argument("image", help="Path to jpg/png frame")
    parser.add_argument("--weights", help="YOLO .pt weights path")
    parser.add_argument("--fallback-angle", type=int, default=90)
    parser.add_argument("--invert-x", action="store_true")
    # Bench calibration: camera left/center/right vs nozzle 0°/90°/180°.
    parser.add_argument("--angle-offset", type=float, default=0.0)
    parser.add_argument("--angle-scale", type=float, default=1.0)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    if not vision_weights_available(args.weights):
        print(
            "Error: no YOLO weights found. Train ml/vision/segmentation/train.py or "
            "set SMART_SPRINKLER_VISION_WEIGHTS.",
            file=sys.stderr,
        )
        return 1

    try:
        result = aim_from_image(
            args.image,
            weights=args.weights,
            fallback_angle=args.fallback_angle,
            invert_x=args.invert_x,
            angle_offset_deg=args.angle_offset,
            angle_scale=args.angle_scale,
        )
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"angle_deg: {result['angle_deg']}")
        print(f"source:    {result['source']}")
        for note in result.get("notes") or []:
            print(f"  - {note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
