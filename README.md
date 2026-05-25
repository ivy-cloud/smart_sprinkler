# Smart Sprinkler System (Waterlytics)

An intelligent irrigation system that uses visual analysis to detect where water is needed and irrigates with precision—reducing waste while keeping landscapes healthy.

**Proposal reference:** [Water Analytics / Smart Sprinkler](https://collegeappivy.wixsite.com/wateranalytics)

**Architecture:** [docs/architecture.md](docs/architecture.md) — overall system design, hardware roles, code structure, and roadmap.

## Vision

Most irrigation systems treat landscapes as uniform surfaces, watering entire zones equally regardless of actual conditions. This project explores responsive irrigation: **see the environment before acting**, using visual intelligence and automation for more sustainable water use.

## Weather & irrigation schedule

Forecast + sprinkler duration:

```bash
python3 scripts/sprinkler_schedule.py --city "San Jose"
python3 scripts/fetch_weather.py --city "San Jose" --with-schedule
```

**Soil + weather merged decision** (matches `heli_tx` CSV: `voltage,current,flow,level,temp,humidity`):

```bash
python3 scripts/analyze_soil.py --csv "12.1,0.4,0.0,28,22.5,41" --city "San Jose"
```

**HTTP API** (for soil analysis script or ESP32 bridge):

```bash
pip install -r requirements.txt
python3 scripts/api_server.py
# POST http://127.0.0.1:8765/v1/irrigation/decision
```

Docs: [RUNBOOK.md](docs/RUNBOOK.md) (CLI commands) · [irrigation_schedule_design.md](docs/irrigation_schedule_design.md) · [irrigation_api.md](docs/irrigation_api.md) · [ml_overview.md](docs/ml_overview.md)

## Code structure

### Repository layout

```text
smart_sprinkler/
├── services/          # Core libraries (weather, irrigation, HTTP API)
├── scripts/           # Thin CLIs that call services
├── ml/                # Model training, datasets, local artifacts (*.pt gitignored)
├── firmware/          # Target ESP32/STM32 sketches (placeholders)
├── pre_code/          # Legacy prototypes (local only, not on GitHub)
├── configs/           # Example env and irrigation JSON
└── docs/              # Architecture, API, ML, schedule design
```

### Irrigation decision pipeline

When you run `scripts/analyze_soil.py` (or `POST /v1/irrigation/decision`), flow is:

```text
scripts/analyze_soil.py
        │
        ▼
services/irrigation/__init__.py     get_final_decision_api() / analyze_soil_api()
        │
        ├── services/weather/client.py
        │      load_forecast()  →  humidity, rain, ET₀, VPD (Open-Meteo)
        │
        ├── services/irrigation/weather.py
        │      decide_weather()  →  WeatherDecision (rules: ON/OFF, minutes)
        │
        ├── services/irrigation/soil.py
        │      analyze_soil()  →  SoilDecision (rules + optional ML)
        │           └── services/irrigation/ml_inference.py
        │                 predict_binary()       ← ml/soil/binary/artifacts/model.pt
        │                 predict_regression_days() ← ml/soil/regression/artifacts/model.pt
        │
        └── services/irrigation/merge.py
               merge_decisions()  →  FinalIrrigationDecision
                    (sprinkler_on, duration, days_to_next_watering, ml {}, decision_source)
```

**Rules** handle rain skip, wet soil, and `min(weather, soil)` duration. **ML** (when trained) can skip/boost watering and predict days until next run. Use `--no-ml` for rules only.

### `services/` — production logic

| Path | Responsibility |
|------|----------------|
| `services/weather/client.py` | Geocode, fetch forecast, parse hourly rows (`et0_mm`, `vpd_kpa`) |
| `services/irrigation/config.py` | Thresholds (wet/dry %, base minutes, GPM) |
| `services/irrigation/types.py` | `SoilReading`, `WeatherDecision`, `SoilDecision`, `FinalIrrigationDecision` |
| `services/irrigation/weather.py` | Rule-based schedule from forecast |
| `services/irrigation/soil.py` | Rule-based soil need + calls `analyze_ml()` |
| `services/irrigation/ml_inference.py` | Loads PyTorch weights; features from CSV + forecast |
| `services/irrigation/merge.py` | Combines weather + soil + ML → final decision |
| `services/irrigation/__init__.py` | Public API: `get_final_decision_api`, `analyze_soil_api`, … |
| `services/api/server.py` | FastAPI routes → same `*_api` functions |

### `scripts/` — entry points

| Script | Calls |
|--------|--------|
| `_bootstrap.py` | Adds repo root to `sys.path` |
| `analyze_soil.py` | Merged or soil-only decision (`--no-ml`, `--soil-only`) |
| `sprinkler_schedule.py` | Weather rules only |
| `fetch_weather.py` | Raw forecast table |
| `api_server.py` | `uvicorn` + `services.api.app` |
| `train_ml_models.py` | Trains `ml/soil/binary` + `ml/soil/regression` artifacts |
| `irrigation_to_hp_tk.py` | Irrigation decision → serial angle on `hp_tk_tx` (0 = stop) |
| `hp_tk_spray_experiment.py` | Demo: 0 → spray angle → 1–10 s → 0 from decision duration |

### `ml/` — training (runtime uses `ml_inference.py`, not these directly)

```text
ml/
├── soil/binary/       # Classify needs watering now (0/1)
│   ├── train.py       # → artifacts/model.pt
│   ├── infer.py       # Standalone CLI (optional)
│   └── data/*.csv
├── soil/regression/   # Days until next watering (uses ET₀, VPD, rain)
│   ├── train.py
│   ├── infer.py
│   └── data/*.csv
└── vision/segmentation/   # YOLO grass/road/water (not wired to services yet)
```

Train once locally: `pip install -r ml/requirements.txt` then `python3 scripts/train_ml_models.py`.

### Other folders

| Path | Role |
|------|------|
| `firmware/` | Production `.ino` (`actuator/hp_tk_rx`, `gateway/hp_tk_tx`, …) |
| `pre_code/` | Legacy working sketches (gitignored on GitHub) |
| `configs/` | `irrigation.example.json`, `env.example` |

Sensor CSV format (STM32 / `heli_tx`): `voltage,current,flow,waterLevel,soilTemp,humidity`.

## Repository status

Application code, firmware, and scripts are added as development progresses.

## License

TBD.
