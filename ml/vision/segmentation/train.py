#!/usr/bin/env python3
"""Fine-tune YOLO11n-seg on data.yaml (requires train/valid/test — see README.md)."""

from __future__ import annotations

import sys
from pathlib import Path

SEG_DIR = Path(__file__).resolve().parent
DATA_YAML = SEG_DIR / "data.yaml"


def check_dataset() -> None:
    """Ensure symlinked or local train/valid image folders exist."""
    missing = []
    for split in ("train", "valid"):
        images = SEG_DIR / split / "images"
        if not images.is_dir():
            missing.append(str(images))
    if not missing:
        return
    print("Dataset folders not found. The ~600MB images are not in git.", file=sys.stderr)
    print(file=sys.stderr)
    print("From repo root, run:", file=sys.stderr)
    print("  bash scripts/setup_segmentation_dataset.sh", file=sys.stderr)
    print(file=sys.stderr)
    print("Or manually:", file=sys.stderr)
    print(
        "  ln -s ../../smart_sprinkler_docs/code/Video_Segmentation/train train",
        file=sys.stderr,
    )
    print(
        "  ln -s ../../smart_sprinkler_docs/code/Video_Segmentation/valid valid",
        file=sys.stderr,
    )
    print(
        "  ln -s ../../smart_sprinkler_docs/code/Video_Segmentation/test test",
        file=sys.stderr,
    )
    print(file=sys.stderr)
    print("Missing:", file=sys.stderr)
    for path in missing:
        print(f"  {path}", file=sys.stderr)
    raise SystemExit(1)


def pick_device() -> str:
    """Prefer Apple GPU (MPS), else CPU."""
    import torch

    if torch.backends.mps.is_available():
        return "mps"
    print("MPS not available; using CPU.", file=sys.stderr)
    return "cpu"


def main() -> None:
    check_dataset()

    from ultralytics import YOLO

    device = pick_device()
    print(f"Training on device: {device}")
    model = YOLO("yolo11n-seg.pt")
    model.train(data=str(DATA_YAML), epochs=100, imgsz=640, device=device)


if __name__ == "__main__":
    main()
