# Machine learning (from `smart_sprinkler_docs/code`)

Three prototypes that complement the rule-based `services/irrigation/` layer:

| Folder | Task | Fills gap in project |
|--------|------|----------------------|
| `soil/binary/` | Classify **needs watering now** (0/1) from soil electrical features | Alternative / augment to `soil.py` thresholds |
| `soil/regression/` | Predict **days until next watering** from soil + weather drivers | Schedule planning beyond fixed 20‑min bursts |
| `vision/segmentation/` | YOLO **grass / road / water / car** masks on CCTV frames | Targeted nozzle aim (“see before you spray”) |

**Source archive:** `../smart_sprinkler_docs/code/` (full YOLO image dataset ~600MB stays there; see `vision/segmentation/README.md`).

## Quick start

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r ml/requirements.txt

# Train soil classifiers
python3 ml/soil/binary/train.py
python3 ml/soil/regression/train.py

# Inference (after training writes artifacts/)
python3 ml/soil/binary/infer.py --humidity 6.6 --temp 20.7 --salinity 71 --conductivity 90
python3 ml/soil/regression/infer.py --humidity 18 --temp 28.5 --salinity 120 --ec 650 --rain 0 --et0 6 --vpd 2.2
```

## Integrated with irrigation (active)

`services/irrigation/ml_inference.py` loads artifacts from `ml/soil/*/artifacts/` when present:

- **Binary MLP** — can skip or boost watering in `analyze_soil()` (merged with rule thresholds).
- **Regression MLP** — `days_to_next_watering` on final API/CLI output; uses **ET₀ / VPD / rain** from `services/weather/`.

Train weights once:

```bash
pip install -r ml/requirements.txt
python3 scripts/train_ml_models.py
```

Then:

```bash
python3 scripts/analyze_soil.py --csv "12.1,0.4,0.0,28,22.5,41" --city "San Jose"
```

Use `--no-ml` for rules-only. See [docs/ml_overview.md](../docs/ml_overview.md).
