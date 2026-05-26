"""Map YOLO grass detections to a horizontal nozzle angle (0–180°)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

GRASS_CLASS_NAME = "grass"

@dataclass
class GrassCentroid:
    cx: float
    cy: float
    area_px: float
    confidence: float
    detection_index: int


@dataclass
class NozzleAim:
    angle_deg: int
    source: str
    notes: list[str]
    centroid: GrassCentroid | None = None
    image_width: int = 0
    image_height: int = 0
    fallback_angle: int = 90

    # Bench calibration knobs used for this aim (see centroid_to_nozzle_angle).
    angle_offset_deg: float = 0.0
    angle_scale: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "angle_deg": self.angle_deg,
            "source": self.source,
            "notes": self.notes,
            "image_width": self.image_width,
            "image_height": self.image_height,
            "fallback_angle": self.fallback_angle,
            "angle_offset_deg": self.angle_offset_deg,
            "angle_scale": self.angle_scale,
        }
        if self.centroid:
            out["centroid"] = {
                "cx": round(self.centroid.cx, 1),
                "cy": round(self.centroid.cy, 1),
                "area_px": round(self.centroid.area_px, 1),
                "confidence": round(self.centroid.confidence, 3),
            }
        return out


def _grass_class_id(names: dict[int, str] | list[str]) -> int:
    if isinstance(names, dict):
        for idx, label in names.items():
            if label == GRASS_CLASS_NAME:
                return int(idx)
        raise ValueError(f"Class {GRASS_CLASS_NAME!r} not in model names: {names}")
    for idx, label in enumerate(names):
        if label == GRASS_CLASS_NAME:
            return idx
    raise ValueError(f"Class {GRASS_CLASS_NAME!r} not in model names: {names}")


def largest_grass_centroid(
    result: Any,
    *,
    min_confidence: float = 0.25,
) -> GrassCentroid | None:
    """
    Pick the largest grass box by pixel area; use box center as aim point.
    Works with ultralytics Results (boxes + optional masks).
    """
    boxes = getattr(result, "boxes", None)
    if boxes is None or len(boxes) == 0:
        return None

    grass_id = _grass_class_id(result.names)
    best: GrassCentroid | None = None

    for i in range(len(boxes)):
        if int(boxes.cls[i]) != grass_id:
            continue
        conf = float(boxes.conf[i])
        if conf < min_confidence:
            continue
        x1, y1, x2, y2 = boxes.xyxy[i].tolist()
        area = max(0.0, (x2 - x1) * (y2 - y1))
        if best is None or area > best.area_px:
            best = GrassCentroid(
                cx=(x1 + x2) / 2.0,
                cy=(y1 + y2) / 2.0,
                area_px=area,
                confidence=conf,
                detection_index=i,
            )
    return best


def centroid_to_nozzle_angle(
    cx: float,
    image_width: int,
    *,
    invert_x: bool = False,
    angle_offset_deg: float = 0.0,
    angle_scale: float = 1.0,
) -> int:
    """
    Map horizontal pixel position to servo angle.

    Assumes camera looks at the lawn left→right maps to nozzle sweep 0→180°.
    Set invert_x=True if your mount is mirrored.

    Bench calibration (camera vs servo are mounted separately):
      linear = (cx / width) * 180   [or mirrored with invert_x]
      angle  = 90 + angle_scale * (linear - 90) + angle_offset_deg
    Tune offset/scale so image left/center/right matches nozzle 0°/90°/180°.
    """
    if image_width <= 0:
        return 90
    t = cx / float(image_width)
    if invert_x:
        t = 1.0 - t
    linear = max(0.0, min(1.0, t)) * 180.0
    # Scale sweep around 90°, then shift by offset (manual bench alignment).
    calibrated = 90.0 + float(angle_scale) * (linear - 90.0) + float(angle_offset_deg)
    return int(round(max(0.0, min(180.0, calibrated))))


def aim_from_grass_detection(
    result: Any,
    *,
    fallback_angle: int = 90,
    min_confidence: float = 0.25,
    min_area_px: float = 400.0,
    invert_x: bool = False,
    angle_offset_deg: float = 0.0,
    angle_scale: float = 1.0,
) -> NozzleAim:
    """Build nozzle aim from one ultralytics predict() result."""
    notes: list[str] = []
    shape = getattr(result, "orig_shape", None) or (0, 0)
    height, width = int(shape[0]), int(shape[1])

    centroid = largest_grass_centroid(result, min_confidence=min_confidence)
    if centroid is None:
        notes.append("No grass detection above confidence; using fallback angle.")
        return NozzleAim(
            angle_deg=max(1, min(180, fallback_angle)),
            source="vision_fallback_no_grass",
            notes=notes,
            image_width=width,
            image_height=height,
            fallback_angle=fallback_angle,
        )

    if centroid.area_px < min_area_px:
        notes.append(
            f"Grass region small ({centroid.area_px:.0f} px²); using fallback angle."
        )
        return NozzleAim(
            angle_deg=max(1, min(180, fallback_angle)),
            source="vision_fallback_small_grass",
            notes=notes,
            centroid=centroid,
            image_width=width,
            image_height=height,
            fallback_angle=fallback_angle,
        )

    angle = centroid_to_nozzle_angle(
        centroid.cx,
        width,
        invert_x=invert_x,
        angle_offset_deg=angle_offset_deg,
        angle_scale=angle_scale,
    )
    if angle == 0:
        angle = 1
    notes.append(
        f"Grass centroid at ({centroid.cx:.0f}, {centroid.cy:.0f}) "
        f"conf={centroid.confidence:.2f} → angle {angle}° "
        f"(offset={angle_offset_deg:+.0f}°, scale={angle_scale:.2f})."
    )
    return NozzleAim(
        angle_deg=angle,
        source="vision_grass_centroid",
        notes=notes,
        centroid=centroid,
        image_width=width,
        image_height=height,
        fallback_angle=fallback_angle,
        angle_offset_deg=angle_offset_deg,
        angle_scale=angle_scale,
    )
