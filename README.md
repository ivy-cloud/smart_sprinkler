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

Docs: [irrigation_schedule_design.md](docs/irrigation_schedule_design.md) · [irrigation_api.md](docs/irrigation_api.md)

## Code layout

| Path | Role |
|------|------|
| `services/irrigation/` | Weather + soil + merge decision library |
| `services/weather/` | Open-Meteo forecast client |
| `services/api/` | FastAPI HTTP server |
| `scripts/` | Thin CLIs (`fetch_weather`, `sprinkler_schedule`, `analyze_soil`, `api_server`) |
| `configs/` | Example env and irrigation JSON |
| `firmware/` | Placeholder sketches (working prototypes in `pre_code/ESP32_TASK/`) |
| `pre_code/` | Legacy prototypes (`ESP32_TASK/`) kept for reference |
| `ml/` | ML training & inference (soil MLPs, YOLO grass segmentation) — [docs/ml_overview.md](docs/ml_overview.md) |

## Repository status

Application code, firmware, and scripts are added as development progresses.

## License

TBD.
