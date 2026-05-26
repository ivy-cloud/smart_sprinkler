#!/usr/bin/env python3
"""
Run YOLO11 segmentation on a frame and return a nozzle aim angle.

  python3 ml/vision/segmentation/infer.py path/to/frame.jpg
  python3 ml/vision/segmentation/infer.py frame.jpg --weights runs/segment/train/weights/best.pt
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

SEG_DIR = Path(__file__).resolve().parent
if str(SEG_DIR) not in sys.path:
    sys.path.insert(0, str(SEG_DIR))
from angle import NozzleAim, aim_from_grass_detection  # noqa: E402
REPO_ROOT = SEG_DIR.parents[2]

DEFAULT_WEIGHT_CANDIDATES = [
    SEG_DIR / "runs/segment/train/weights/best.pt",
    SEG_DIR / "yolo11n-seg.pt",
]


def resolve_weights(path: str | Path | None = None) -> Path:
    if path is not None:
        p = Path(path).expanduser().resolve()
        if not p.is_file():
            raise FileNotFoundError(f"Vision weights not found: {p}")
        return p
    env = os.environ.get("SMART_SPRINKLER_VISION_WEIGHTS")
    if env:
        p = Path(env).expanduser().resolve()
        if p.is_file():
            return p
    for candidate in DEFAULT_WEIGHT_CANDIDATES:
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(
        "No segmentation weights found. Train with train.py or set "
        "SMART_SPRINKLER_VISION_WEIGHTS to a .pt file."
    )


def predict_nozzle_aim(
    image: str | Path,
    *,
    weights: str | Path | None = None,
    imgsz: int = 640,
    fallback_angle: int = 90,
    min_confidence: float = 0.25,
    min_area_px: float = 400.0,
    invert_x: bool = False,
    angle_offset_deg: float = 0.0,
    angle_scale: float = 1.0,
) -> NozzleAim:
    try:
        from ultralytics import YOLO
    except ImportError as exc:
        raise SystemExit(
            "ultralytics not installed. Run: pip install -r ml/requirements.txt"
        ) from exc

    image_path = Path(image).expanduser().resolve()
    if not image_path.is_file():
        raise FileNotFoundError(f"Image not found: {image_path}")

    model = YOLO(str(resolve_weights(weights)))
    results = model.predict(
        str(image_path),
        imgsz=imgsz,
        verbose=False,
    )
    if not results:
        return aim_from_grass_detection(
            _empty_result_stub(image_path),
            fallback_angle=fallback_angle,
            min_confidence=min_confidence,
            min_area_px=min_area_px,
            invert_x=invert_x,
            angle_offset_deg=angle_offset_deg,
            angle_scale=angle_scale,
        )
    return aim_from_grass_detection(
        results[0],
        fallback_angle=fallback_angle,
        min_confidence=min_confidence,
        min_area_px=min_area_px,
        invert_x=invert_x,
        angle_offset_deg=angle_offset_deg,
        angle_scale=angle_scale,
    )


class _EmptyResultStub:
    """Minimal object when predict returns nothing."""

    names = ["car", "grass", "road", "water"]
    boxes = None

    def __init__(self, path: Path) -> None:
        try:
            from PIL import Image

            with Image.open(path) as im:
                self.orig_shape = (im.height, im.width)
        except Exception:
            self.orig_shape = (480, 640)


def _empty_result_stub(path: Path) -> _EmptyResultStub:
    return _EmptyResultStub(path)


def main() -> int:
    parser = argparse.ArgumentParser(description="Grass segmentation → nozzle angle")
    parser.add_argument("image", help="Path to camera frame (jpg/png)")
    parser.add_argument("--weights", help="YOLO .pt weights (default: search repo paths)")
    parser.add_argument("--fallback-angle", type=int, default=90)
    parser.add_argument("--min-confidence", type=float, default=0.25)
    parser.add_argument("--min-area-px", type=float, default=400.0)
    parser.add_argument("--invert-x", action="store_true", help="Mirror horizontal aim")
    # Bench calibration: align camera left/center/right with servo 0°/90°/180°.
    parser.add_argument(
        "--angle-offset",
        type=float,
        default=0.0,
        help="Degrees added after mapping (default 0)",
    )
    parser.add_argument(
        "--angle-scale",
        type=float,
        default=1.0,
        help="Scale sweep around 90° (default 1)",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    try:
        aim = predict_nozzle_aim(
            args.image,
            weights=args.weights,
            fallback_angle=args.fallback_angle,
            min_confidence=args.min_confidence,
            min_area_px=args.min_area_px,
            invert_x=args.invert_x,
            angle_offset_deg=args.angle_offset,
            angle_scale=args.angle_scale,
        )
    except (FileNotFoundError, SystemExit) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(aim.to_dict(), indent=2))
    else:
        print(f"angle_deg: {aim.angle_deg}")
        print(f"source:    {aim.source}")
        for note in aim.notes:
            print(f"  - {note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
