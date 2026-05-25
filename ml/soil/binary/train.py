#!/usr/bin/env python3
"""
Train a small MLP: soil electrical features → watered (0/1).

Data: ml/soil/binary/data/soil_watering_{train,val}.csv
Artifacts: ml/soil/binary/artifacts/model.pt, mu.npy, sigma.npy, training_history.csv
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

FEATURES = ["humidity_pct", "temperature_c", "salinity_uS_cm", "conductivity_uS_cm"]
TARGET = "watered"


class MLP(nn.Module):
    def __init__(self, in_dim: int = 4, hidden: int = 64, dropout: float = 0.2):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(in_dim, hidden),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(hidden, hidden),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(hidden, 2),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.net(x)


def metrics_from_logits(logits: torch.Tensor, y_true: torch.Tensor) -> dict:
    probs = torch.softmax(logits, dim=1)[:, 1]
    y_pred = (probs >= 0.5).long()
    y_true = y_true.long()
    tp = ((y_pred == 1) & (y_true == 1)).sum().item()
    tn = ((y_pred == 0) & (y_true == 0)).sum().item()
    fp = ((y_pred == 1) & (y_true == 0)).sum().item()
    fn = ((y_pred == 0) & (y_true == 1)).sum().item()
    acc = (tp + tn) / max(1, tp + tn + fp + fn)
    prec = tp / max(1, tp + fp)
    rec = tp / max(1, tp + fn)
    f1 = 0.0 if (prec + rec) == 0 else 2 * prec * rec / (prec + rec)
    return {"acc": acc, "precision": prec, "recall": rec, "f1": f1}


def eval_loader(model, loader, criterion, device) -> tuple[float, dict]:
    model.eval()
    total_loss = 0.0
    all_logits: list[torch.Tensor] = []
    all_y: list[torch.Tensor] = []
    with torch.no_grad():
        for xb, yb in loader:
            xb, yb = xb.to(device), yb.to(device)
            logits = model(xb)
            total_loss += criterion(logits, yb).item() * xb.size(0)
            all_logits.append(logits.cpu())
            all_y.append(yb.cpu())
    logits_cat = torch.cat(all_logits, dim=0)
    y_cat = torch.cat(all_y, dim=0)
    return total_loss / len(loader.dataset), metrics_from_logits(logits_cat, y_cat)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--epochs", type=int, default=30)
    parser.add_argument("--plots", action="store_true", help="Save loss/accuracy PNGs")
    args = parser.parse_args()

    train_df = pd.read_csv(DATA_DIR / "soil_watering_train.csv")
    val_df = pd.read_csv(DATA_DIR / "soil_watering_val.csv")

    X_train = train_df[FEATURES].values.astype(np.float32)
    y_train = train_df[TARGET].values.astype(np.int64)
    X_val = val_df[FEATURES].values.astype(np.float32)
    y_val = val_df[TARGET].values.astype(np.int64)

    mu = X_train.mean(axis=0, keepdims=True)
    sigma = X_train.std(axis=0, keepdims=True) + 1e-6
    X_train = (X_train - mu) / sigma
    X_val = (X_val - mu) / sigma

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    train_loader = DataLoader(
        TensorDataset(torch.tensor(X_train), torch.tensor(y_train)),
        batch_size=64,
        shuffle=True,
    )
    val_loader = DataLoader(
        TensorDataset(torch.tensor(X_val), torch.tensor(y_val)),
        batch_size=256,
        shuffle=False,
    )

    model = MLP(in_dim=len(FEATURES)).to(device)
    criterion = nn.CrossEntropyLoss()
    optimizer = torch.optim.Adam(model.parameters(), lr=1e-3, weight_decay=1e-4)

    history: list[dict] = []
    for ep in range(1, args.epochs + 1):
        model.train()
        running = 0.0
        train_logits: list[torch.Tensor] = []
        train_y: list[torch.Tensor] = []
        for xb, yb in train_loader:
            xb, yb = xb.to(device), yb.to(device)
            optimizer.zero_grad()
            logits = model(xb)
            loss = criterion(logits, yb)
            loss.backward()
            optimizer.step()
            running += loss.item() * xb.size(0)
            train_logits.append(logits.detach().cpu())
            train_y.append(yb.detach().cpu())
        train_loss = running / len(train_loader.dataset)
        train_m = metrics_from_logits(torch.cat(train_logits), torch.cat(train_y))
        val_loss, val_m = eval_loader(model, val_loader, criterion, device)
        history.append(
            {
                "epoch": ep,
                "train_loss": train_loss,
                "val_loss": val_loss,
                "val_acc": val_m["acc"],
                "val_f1": val_m["f1"],
            }
        )
        print(f"epoch {ep:02d}  val_loss={val_loss:.4f}  val_acc={val_m['acc']:.3f}  val_f1={val_m['f1']:.3f}")

    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    torch.save(
        {
            "state_dict": model.cpu().state_dict(),
            "features": FEATURES,
            "mu": mu.squeeze().tolist(),
            "sigma": sigma.squeeze().tolist(),
        },
        ARTIFACTS / "model.pt",
    )
    pd.DataFrame(history).to_csv(ARTIFACTS / "training_history.csv", index=False)

    if args.plots:
        import matplotlib.pyplot as plt

        hist = pd.DataFrame(history)
        plt.figure()
        plt.plot(hist["epoch"], hist["train_loss"], label="train")
        plt.plot(hist["epoch"], hist["val_loss"], label="val")
        plt.legend()
        plt.savefig(ARTIFACTS / "loss_curve.png", dpi=200)
        plt.close()

    print(f"Saved artifacts to {ARTIFACTS}")


if __name__ == "__main__":
    main()
