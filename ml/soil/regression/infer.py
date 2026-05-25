#!/usr/bin/env python3
"""Predict days until next watering from soil + weather features."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import torch

from train import FEATURES, MAX_DAYS, MLP

ARTIFACTS = Path(__file__).resolve().parent / "artifacts"
MODEL_PATH = ARTIFACTS / "model.pt"


def predict_days(sample: dict[str, float]) -> float:
    if not MODEL_PATH.exists():
        raise FileNotFoundError(f"No model at {MODEL_PATH}. Run: python3 ml/soil/regression/train.py")
    ckpt = torch.load(MODEL_PATH, map_location="cpu", weights_only=False)
    model = MLP(input_dim=len(ckpt["features"]))
    model.load_state_dict(ckpt["state_dict"])
    model.eval()
    mean = np.array(ckpt["mean"], dtype=np.float32)
    std = np.array(ckpt["std"], dtype=np.float32)
    max_days = float(ckpt.get("max_days", MAX_DAYS))

    x = np.array([sample[f] for f in FEATURES], dtype=np.float32)
    x = (x - mean) / std
    with torch.no_grad():
        pred = model(torch.tensor(x).unsqueeze(0)).item()
    return float(np.clip(pred, 0.0, max_days))


def main() -> None:
    parser = argparse.ArgumentParser()
    for name in FEATURES:
        parser.add_argument(f"--{name}", type=float, required=True)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    sample = {f: getattr(args, f) for f in FEATURES}
    days = predict_days(sample)
    out = {"days_to_next_watering": round(days, 3), "inputs": sample}
    if args.json:
        print(json.dumps(out, indent=2))
    else:
        print(f"days_to_next_watering={days:.2f}")


if __name__ == "__main__":
    main()
