#!/usr/bin/env python3
"""Run trained binary soil watering classifier."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import torch

from train import FEATURES, MLP

ARTIFACTS = Path(__file__).resolve().parent / "artifacts"
MODEL_PATH = ARTIFACTS / "model.pt"


def load_model() -> tuple[MLP, np.ndarray, np.ndarray]:
    if not MODEL_PATH.exists():
        raise FileNotFoundError(f"No model at {MODEL_PATH}. Run: python3 ml/soil/binary/train.py")
    ckpt = torch.load(MODEL_PATH, map_location="cpu", weights_only=False)
    model = MLP(in_dim=len(ckpt["features"]))
    model.load_state_dict(ckpt["state_dict"])
    model.eval()
    mu = np.array(ckpt["mu"], dtype=np.float32)
    sigma = np.array(ckpt["sigma"], dtype=np.float32)
    return model, mu, sigma


def predict(
    humidity_pct: float,
    temperature_c: float,
    salinity_uS_cm: float,
    conductivity_uS_cm: float,
    *,
    threshold: float = 0.5,
) -> dict:
    model, mu, sigma = load_model()
    x = np.array(
        [[humidity_pct, temperature_c, salinity_uS_cm, conductivity_uS_cm]],
        dtype=np.float32,
    )
    x = (x - mu) / (sigma + 1e-6)
    with torch.no_grad():
        logits = model(torch.tensor(x))
        prob_watered = torch.softmax(logits, dim=1)[0, 1].item()
    prob_needs = 1.0 - prob_watered
    return {
        "prob_watered": round(prob_watered, 4),
        "prob_needs_water": round(prob_needs, 4),
        "pred_already_watered": int(prob_watered >= threshold),
        "needs_watering": prob_needs >= threshold,
        "features": dict(zip(FEATURES, [humidity_pct, temperature_c, salinity_uS_cm, conductivity_uS_cm])),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--humidity", type=float, required=True)
    parser.add_argument("--temp", type=float, required=True)
    parser.add_argument("--salinity", type=float, required=True)
    parser.add_argument("--conductivity", type=float, required=True)
    parser.add_argument("--threshold", type=float, default=0.5)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    out = predict(
        args.humidity,
        args.temp,
        args.salinity,
        args.conductivity,
        threshold=args.threshold,
    )
    if args.json:
        print(json.dumps(out, indent=2))
    else:
        print(f"prob_watered={out['prob_watered']:.3f}  needs_watering={out['needs_watering']}")


if __name__ == "__main__":
    main()
