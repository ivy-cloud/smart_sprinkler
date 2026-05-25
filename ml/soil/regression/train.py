#!/usr/bin/env python3
"""
Train MLP regression: soil + weather features → days until next watering (0–14).

Data: ml/soil/regression/data/train_watering_dataset.csv, val_watering_dataset.csv
Artifacts: ml/soil/regression/artifacts/model.pt
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, TensorDataset

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
ARTIFACTS = ROOT / "artifacts"

FEATURES = ["humidity", "temperature", "salinity", "ec", "rain", "et0", "vpd"]
TARGET = "days_to_next_watering"
MAX_DAYS = 14.0


class MLP(nn.Module):
    def __init__(self, input_dim: int):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(input_dim, 64),
            nn.ReLU(),
            nn.Linear(64, 32),
            nn.ReLU(),
            nn.Linear(32, 1),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.net(x)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--epochs", type=int, default=60)
    parser.add_argument("--plots", action="store_true")
    args = parser.parse_args()

    train_df = pd.read_csv(DATA_DIR / "train_watering_dataset.csv")
    val_df = pd.read_csv(DATA_DIR / "val_watering_dataset.csv")

    X_train = train_df[FEATURES].values.astype(np.float32)
    y_train = train_df[TARGET].values.astype(np.float32).reshape(-1, 1)
    X_val = val_df[FEATURES].values.astype(np.float32)
    y_val = val_df[TARGET].values.astype(np.float32).reshape(-1, 1)

    mean = X_train.mean(axis=0)
    std = X_train.std(axis=0) + 1e-6
    X_train = (X_train - mean) / std
    X_val = (X_val - mean) / std

    train_loader = DataLoader(
        TensorDataset(torch.tensor(X_train), torch.tensor(y_train)),
        batch_size=64,
        shuffle=True,
    )
    val_loader = DataLoader(
        TensorDataset(torch.tensor(X_val), torch.tensor(y_val)),
        batch_size=64,
        shuffle=False,
    )

    model = MLP(input_dim=len(FEATURES))
    criterion = nn.MSELoss()
    optimizer = torch.optim.Adam(model.parameters(), lr=1e-3)

    train_mse_list: list[float] = []
    val_mse_list: list[float] = []

    for epoch in range(args.epochs):
        model.train()
        train_losses = []
        for xb, yb in train_loader:
            optimizer.zero_grad()
            loss = criterion(model(xb), yb)
            loss.backward()
            optimizer.step()
            train_losses.append(loss.item())
        train_mse = float(np.mean(train_losses))

        model.eval()
        val_losses = []
        with torch.no_grad():
            for xb, yb in val_loader:
                val_losses.append(criterion(model(xb), yb).item())
        val_mse = float(np.mean(val_losses))
        train_mse_list.append(train_mse)
        val_mse_list.append(val_mse)
        if (epoch + 1) % 10 == 0 or epoch == 0:
            print(f"epoch {epoch + 1:03d}  train_mse={train_mse:.4f}  val_mse={val_mse:.4f}")

    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    torch.save(
        {
            "state_dict": model.state_dict(),
            "features": FEATURES,
            "mean": mean.tolist(),
            "std": std.tolist(),
            "max_days": MAX_DAYS,
        },
        ARTIFACTS / "model.pt",
    )

    if args.plots:
        import matplotlib.pyplot as plt

        plt.figure()
        plt.plot(train_mse_list, label="Train MSE")
        plt.plot(val_mse_list, label="Val MSE")
        plt.xlabel("Epoch")
        plt.legend()
        plt.savefig(ARTIFACTS / "mse_curve.png", dpi=200)
        plt.close()

    print(f"final train_mse={train_mse_list[-1]:.4f}  val_mse={val_mse_list[-1]:.4f}")
    print(f"Saved {ARTIFACTS / 'model.pt'}")


if __name__ == "__main__":
    main()
