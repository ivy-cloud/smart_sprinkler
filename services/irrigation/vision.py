"""
Optional YOLO grass segmentation → horizontal nozzle angle (0–180°).

Duration and sprinkler ON/OFF still come from soil + weather merge; vision only aims.

angle_offset_deg / angle_scale tune the image→servo mapping on the bench so
camera left/center/right lines up with nozzle 0°/90°/180° (see angle.py).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
_SEG_INFER = REPO_ROOT / "ml" / "vision" / "segmentation" / "infer.py"


def _load_predict_nozzle_aim():
    """Import ml/vision/segmentation/infer without requiring ml as a package."""
    spec = importlib.util.spec_from_file_location(
        "smart_sprinkler_vision_infer", _SEG_INFER
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load vision infer module at {_SEG_INFER}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.predict_nozzle_aim, module.resolve_weights


def aim_from_image(
    image: str | Path,
    *,
    weights: str | Path | None = None,
    fallback_angle: int = 90,
    min_confidence: float = 0.25,
    min_area_px: float = 400.0,
    invert_x: bool = False,
    imgsz: int = 640,
    angle_offset_deg: float = 0.0,
    angle_scale: float = 1.0,
) -> dict[str, Any]:
    """
    Run segmentation on one frame and return aim metadata (includes angle_deg).
    """
    predict_nozzle_aim, resolve_weights = _load_predict_nozzle_aim()
    weights_path = resolve_weights(weights) if weights else resolve_weights(None)
    aim = predict_nozzle_aim(
        image,
        weights=weights_path,
        imgsz=imgsz,
        fallback_angle=fallback_angle,
        min_confidence=min_confidence,
        min_area_px=min_area_px,
        invert_x=invert_x,
        angle_offset_deg=angle_offset_deg,
        angle_scale=angle_scale,
    )
    out = aim.to_dict()
    out["weights"] = str(weights_path)
    return out


def vision_weights_available(weights: str | Path | None = None) -> bool:
    try:
        _, resolve_weights = _load_predict_nozzle_aim()
        resolve_weights(weights)
        return True
    except (FileNotFoundError, ImportError):
        return False


def hp_tk_angle_from_decision(
    payload: dict[str, Any],
    *,
    default_on_angle: int = 90,
    image: str | Path | None = None,
    vision_weights: str | Path | None = None,
    min_confidence: float = 0.25,
    min_area_px: float = 400.0,
    invert_x: bool = False,
    angle_offset_deg: float = 0.0,
    angle_scale: float = 1.0,
) -> tuple[int, str, dict[str, Any] | None]:
    """
    Map irrigation API payload → angle for hp_tk_tx.

    OFF → 0. ON + --image → vision angle when possible; else default_on_angle.
    """
    if not payload.get("sprinkler_on"):
        return 0, payload.get("skip_reason") or "irrigation OFF", None

    if image is None:
        return (
            max(1, min(180, default_on_angle)),
            "irrigation ON (fixed angle)",
            None,
        )

    try:
        vision = aim_from_image(
            image,
            weights=vision_weights,
            fallback_angle=default_on_angle,
            min_confidence=min_confidence,
            min_area_px=min_area_px,
            invert_x=invert_x,
            angle_offset_deg=angle_offset_deg,
            angle_scale=angle_scale,
        )
    except FileNotFoundError as exc:
        return (
            max(1, min(180, default_on_angle)),
            f"vision unavailable ({exc}); fixed angle",
            {"error": str(exc), "angle_deg": default_on_angle},
        )
    except SystemExit as exc:
        return (
            max(1, min(180, default_on_angle)),
            f"vision unavailable ({exc}); fixed angle",
            {"error": str(exc), "angle_deg": default_on_angle},
        )

    angle = int(vision.get("angle_deg", default_on_angle))
    angle = max(1, min(180, angle))
    source = vision.get("source", "vision")
    if source.startswith("vision_grass"):
        reason = "irrigation ON (YOLO grass centroid)"
    else:
        reason = "irrigation ON (vision fallback)"
    return angle, reason, vision
