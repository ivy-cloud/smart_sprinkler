# ML code overview (from `smart_sprinkler_docs/code`)

Copied and organized under `ml/`. These models fill gaps that rule-based `services/irrigation/` does not cover yet: **learned soil decisions**, **days-ahead scheduling**, and **camera-based lawn targeting**.

---

## 1. Soil binary classification (`ml/soil/binary/`)

### What it does

A small **neural network (MLP)** reads four soil measurements and outputs whether the plant was **watered** (label `1`) or **not yet watered** (`0`).

| Input feature | Meaning |
|---------------|---------|
| `humidity_pct` | Soil humidity % |
| `temperature_c` | Soil temperature °C |
| `salinity_uS_cm` | Salinity µS/cm |
| `conductivity_uS_cm` | Electrical conductivity µS/cm |

### How training works (`train.py`)

1. Load CSV rows from `data/soil_watering_train.csv` and `data/soil_watering_val.csv`.
2. **Standardize** features: subtract mean, divide by std (fit on train only).
3. Network: 4 → 64 → ReLU → Dropout → 64 → ReLU → Dropout → **2 logits** (classes 0 and 1).
4. **Loss:** cross-entropy. **Optimizer:** Adam.
5. Each epoch: track accuracy, precision, recall, F1 on validation.
6. Save `artifacts/model.pt` with weights + normalization stats.

### Inference (`infer.py`)

```bash
python3 ml/soil/binary/infer.py --humidity 6.6 --temp 20.7 --salinity 71 --conductivity 90
```

Returns `prob_watered` and `needs_watering` (threshold 0.5).

### Fit with this repo

| Current (`services/irrigation/soil.py`) | This ML model |
|----------------------------------------|---------------|
| Fixed thresholds on water level % and humidity % from `heli_tx` CSV | Learned boundary from salinity + EC + temp + humidity |
| Same CSV line has **soil temp** and **humidity**; salinity/EC need extra sensors or mapping | |

**Integration idea:** call `infer.py` (or import `predict`) inside `analyze_soil()` when artifacts exist; fall back to rules if not trained.

---

## 2. Soil watering days regression (`ml/soil/regression/`)

### What it does

Predicts **how many days until the next watering** (0–14), not just ON/OFF today.

| Input | Meaning |
|-------|---------|
| `humidity`, `temperature`, `salinity`, `ec` | Soil |
| `rain` | Recent rain flag/count |
| `et0` | Evapotranspiration (mm) |
| `vpd` | Vapor pressure deficit (kPa) |

**Output:** `days_to_next_watering` (continuous, clipped to 0–14).

### How training works (`train.py`)

1. MLP: 7 inputs → 64 → 32 → **1 output**.
2. **Loss:** MSE between predicted and true days.
3. Same normalization as binary model.
4. Saves `artifacts/model.pt`.

### Inference (`infer.py`)

```bash
python3 ml/soil/regression/infer.py \
  --humidity 18 --temperature 28.5 --salinity 120 --ec 650 \
  --rain 0 --et0 6 --vpd 2.2
```

### Fit with this repo

| Current | This ML model |
|---------|---------------|
| `sprinkler_schedule.py` picks **minutes today** from humidity + rain | Answers **when to water again** in days |
| `services/weather/` already fetches humidity and rain | Add **et0** and **vpd** from Open-Meteo hourly for regression inputs |

**Integration idea:** use regression for **calendar scheduling**; keep merge logic for **today’s valve ON/OFF**.

---

## 3. Video segmentation (`ml/vision/segmentation/`)

### What it does

**YOLO11 segmentation** on outdoor CCTV images. Classes:

- `grass` — lawn to irrigate  
- `water` — puddles / ponds (avoid over-watering)  
- `road`, `car` — ignore zones  

This matches the product vision: **see dry grass / lawn borders before aiming the sprinkler**.

### Scripts

| File | Role |
|------|------|
| `convert_coco.py` | One-liner: convert COCO polygon labels to YOLO segment format |
| `train.py` | Load `yolo11n-seg.pt`, train 100 epochs at 640px using `data.yaml` |
| `data.yaml` | Paths to train/valid/test image folders |

### Dataset

Thousands of labeled frames live under `smart_sprinkler_docs/code/Video_Segmentation/` (~600MB). They are **not** copied into git; symlink per `ml/vision/segmentation/README.md`.

### Fit with this repo

| Layer | Role |
|-------|------|
| `firmware/perception/lidar_node.ino` | Distance scan (prototype) |
| **This vision model** | **Which pixels are grass** from a camera |
| `firmware/actuator/sprinkler_node.ino` | Servo angle from target direction |

**Pipeline:** frame → YOLO mask for `grass` → centroid or dry-region bbox → angle command over BLE (`hp_tk` path).

---

## How the three pieces work together

```text
  Camera ──► YOLO grass mask ──────────────► nozzle aim (not wired yet)
  STM32 CSV ──► binary MLP ──► need water today? ──┐
  Open-Meteo ──► regression MLP ──► days until next ─┼──► services/irrigation/merge (wired)
  Rules (rain, thresholds) ──────────────────────────┘
```

**Integration module:** `services/irrigation/ml_inference.py` — called from `soil.py` and `merge.py` when `use_ml=True`.

**Train once:** `python3 scripts/train_ml_models.py` then use `analyze_soil.py` or `/v1/irrigation/decision` as usual.

---

## Source vs this repo

| Original path (`smart_sprinkler_docs/code/`) | This repo |
|---------------------------------------------|-----------|
| `soil_watering_binary_classification_mlp/` | `ml/soil/binary/` |
| `soil_watering_days_regression_mlp/train_infer.py` | `ml/soil/regression/train.py` + `infer.py` |
| `Video_Segmentation/` | `ml/vision/segmentation/` (+ external dataset) |
