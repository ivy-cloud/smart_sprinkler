"""
Optional PyTorch models from ml/soil/*/artifacts — blended with rule-based decisions.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from services.weather.client import WeatherForecast

from .types import SoilReading

REPO_ROOT = Path(__file__).resolve().parents[2]
BINARY_MODEL = REPO_ROOT / "ml/soil/binary/artifacts/model.pt"
REGRESSION_MODEL = REPO_ROOT / "ml/soil/regression/artifacts/model.pt"

REGRESSION_FEATURES = [
    "humidity",
    "temperature",
    "salinity",
    "ec",
    "rain",
    "et0",
    "vpd",
]
BINARY_FEATURES = [
    "humidity_pct",
    "temperature_c",
    "salinity_uS_cm",
    "conductivity_uS_cm",
]


@dataclass
class MlSoilInsights:
    binary_available: bool = False
    regression_available: bool = False
    prob_watered: float | None = None
    prob_needs_water: float | None = None
    ml_skip_watering: bool = False
    ml_boost_watering: bool = False
    days_to_next_watering: float | None = None
    regression_inputs: dict[str, float] | None = None
    notes: list[str] | None = None


def ml_insights_to_dict(insights: MlSoilInsights | None) -> dict[str, Any] | None:
    if insights is None:
        return None
    return asdict(insights)


def _torch_available() -> bool:
    try:
        import torch  # noqa: F401

        return True
    except ImportError:
        return False


def _estimate_salinity_ec(reading: SoilReading) -> tuple[float, float]:
    """Proxy when STM32 CSV has no EC/salinity columns."""
    current = reading.current if reading.current is not None else 0.4
    voltage = reading.voltage if reading.voltage is not None else 12.0
    salinity = (
        reading.salinity_uS_cm
        if reading.salinity_uS_cm is not None
        else max(0.0, current * 180.0)
    )
    ec = (
        reading.conductivity_uS_cm
        if reading.conductivity_uS_cm is not None
        else max(5.0, voltage * 7.5)
    )
    return salinity, ec


def agro_features_from_forecast(forecast: WeatherForecast) -> dict[str, float]:
    summary = forecast.summary_24h()
    rows = forecast.hourly[:24]
    et0_vals = [r.et0_mm for r in rows if r.et0_mm is not None]
    vpd_vals = [r.vpd_kpa for r in rows if r.vpd_kpa is not None]
    et0 = summary.get("et0_avg_mm") or (sum(et0_vals) / len(et0_vals) if et0_vals else 4.0)
    vpd = summary.get("vpd_avg_kpa") or (sum(vpd_vals) / len(vpd_vals) if vpd_vals else 1.5)
    rain = 1.0 if summary.get("rain_recent") else 0.0
    return {"rain": rain, "et0": float(et0), "vpd": float(vpd)}


def build_regression_sample(
    reading: SoilReading, forecast: WeatherForecast
) -> dict[str, float]:
    agro = agro_features_from_forecast(forecast)
    salinity, ec = _estimate_salinity_ec(reading)
    return {
        "humidity": float(reading.humidity_pct if reading.humidity_pct is not None else 40.0),
        "temperature": float(reading.soil_temp_c if reading.soil_temp_c is not None else 22.0),
        "salinity": salinity,
        "ec": ec,
        "rain": agro["rain"],
        "et0": agro["et0"],
        "vpd": agro["vpd"],
    }


def build_binary_sample(reading: SoilReading) -> dict[str, float]:
    salinity, ec = _estimate_salinity_ec(reading)
    return {
        "humidity_pct": float(reading.humidity_pct if reading.humidity_pct is not None else 40.0),
        "temperature_c": float(reading.soil_temp_c if reading.soil_temp_c is not None else 22.0),
        "salinity_uS_cm": salinity,
        "conductivity_uS_cm": ec,
    }


def predict_binary(reading: SoilReading, *, threshold: float = 0.5) -> dict[str, Any] | None:
    if not BINARY_MODEL.exists() or not _torch_available():
        return None

    import numpy as np
    import torch
    import torch.nn as nn

    ckpt = torch.load(BINARY_MODEL, map_location="cpu", weights_only=False)
    features = ckpt.get("features", BINARY_FEATURES)
    mu = np.array(ckpt["mu"], dtype=np.float32)
    sigma = np.array(ckpt["sigma"], dtype=np.float32)
    sample = build_binary_sample(reading)
    x = np.array([[sample[f] for f in features]], dtype=np.float32)
    x = (x - mu) / (sigma + 1e-6)

    class MLP(nn.Module):
        def __init__(self, in_dim: int, hidden: int = 64):
            super().__init__()
            self.net = nn.Sequential(
                nn.Linear(in_dim, hidden),
                nn.ReLU(),
                nn.Dropout(0.2),
                nn.Linear(hidden, hidden),
                nn.ReLU(),
                nn.Dropout(0.2),
                nn.Linear(hidden, 2),
            )

        def forward(self, t: torch.Tensor) -> torch.Tensor:
            return self.net(t)

    model = MLP(len(features))
    model.load_state_dict(ckpt["state_dict"])
    model.eval()
    with torch.no_grad():
        logits = model(torch.tensor(x))
        prob_watered = torch.softmax(logits, dim=1)[0, 1].item()

    prob_needs = 1.0 - prob_watered
    return {
        "prob_watered": round(prob_watered, 4),
        "prob_needs_water": round(prob_needs, 4),
        "needs_watering": prob_needs >= threshold,
        "already_watered": prob_watered >= threshold,
        "inputs": sample,
    }


def predict_regression_days(
    reading: SoilReading, forecast: WeatherForecast
) -> dict[str, Any] | None:
    if not REGRESSION_MODEL.exists() or not _torch_available():
        return None

    import numpy as np
    import torch
    import torch.nn as nn

    ckpt = torch.load(REGRESSION_MODEL, map_location="cpu", weights_only=False)
    features = ckpt.get("features", REGRESSION_FEATURES)
    mean = np.array(ckpt["mean"], dtype=np.float32)
    std = np.array(ckpt["std"], dtype=np.float32)
    max_days = float(ckpt.get("max_days", 14.0))
    sample = build_regression_sample(reading, forecast)
    x = np.array([sample[f] for f in features], dtype=np.float32)
    x = (x - mean) / std

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

        def forward(self, t: torch.Tensor) -> torch.Tensor:
            return self.net(t)

    model = MLP(len(features))
    model.load_state_dict(ckpt["state_dict"])
    model.eval()
    with torch.no_grad():
        days = float(model(torch.tensor(x).unsqueeze(0)).item())
    days = float(np.clip(days, 0.0, max_days))
    return {"days_to_next_watering": round(days, 2), "inputs": sample}


def analyze_ml(
    reading: SoilReading,
    forecast: WeatherForecast | None,
    *,
    watered_threshold: float = 0.65,
    dry_threshold: float = 0.55,
) -> MlSoilInsights:
    notes: list[str] = []
    insights = MlSoilInsights(notes=notes)

    binary = predict_binary(reading)
    if binary:
        insights.binary_available = True
        insights.prob_watered = binary["prob_watered"]
        insights.prob_needs_water = binary["prob_needs_water"]
        if binary["already_watered"] and (binary["prob_watered"] or 0) >= watered_threshold:
            insights.ml_skip_watering = True
            notes.append(
                f"ML binary: likely already watered (p={binary['prob_watered']:.2f})."
            )
        elif binary["needs_watering"] and (binary["prob_needs_water"] or 0) >= dry_threshold:
            insights.ml_boost_watering = True
            notes.append(
                f"ML binary: likely needs water (p={binary['prob_needs_water']:.2f})."
            )
    elif _torch_available():
        notes.append("ML binary: no trained weights (run: python3 scripts/train_ml_models.py).")
    else:
        notes.append("ML binary: install torch (pip install -r ml/requirements.txt).")

    if forecast is not None:
        reg = predict_regression_days(reading, forecast)
        if reg:
            insights.regression_available = True
            insights.days_to_next_watering = reg["days_to_next_watering"]
            insights.regression_inputs = reg["inputs"]
            notes.append(
                f"ML regression: next watering in ~{reg['days_to_next_watering']} days."
            )
        elif _torch_available():
            notes.append(
                "ML regression: no trained weights (run: python3 scripts/train_ml_models.py)."
            )
    elif _torch_available():
        notes.append("ML regression: needs weather forecast (city or lat/lon).")

    return insights
