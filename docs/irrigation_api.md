# Irrigation API & architecture

Weather forecast and soil sensors are **two inputs**; the **final ON/OFF + duration** is produced by a merge step. Use either the Python library directly or the HTTP API.

## Architecture

```text
                    ┌─────────────────────┐
  Open-Meteo ──────►│  weather.py         │
                    │  decide_weather()   │──► WeatherDecision
                    └─────────────────────┘
                              │
  STM32 / heli CSV ─►┌─────────────────────┐
                     │  soil.py            │
                     │  analyze_soil()     │──► SoilDecision
                     └─────────────────────┘
                              │
                              ▼
                    ┌─────────────────────┐
                    │  merge.py           │
                    │  merge_decisions()  │──► FinalIrrigationDecision
                    └─────────────────────┘
```

### Merge rules (v1)

| Priority | Condition | Result |
|----------|-----------|--------|
| 1 | Rain tonight / tomorrow (hard) | **OFF** — soil cannot override |
| 2 | Soil wet (level ≥ 70% or humidity ≥ 78%) | **OFF** |
| 3 | Soil adequate, weather OK | **OFF** |
| 4 | Both need water | `duration = min(weather, soil)` minutes |
| 5 | Soil critically dry (≤ 20%) | Allow **minimum 8 min** even if weather suggested soft skip |

---

## Option A — Python library (recommended for scripts)

From repo root:

```python
import sys
sys.path.insert(0, ".")  # repo root

from services.irrigation import get_final_decision_api, analyze_soil_api, get_weather_decision_api

# Weather only
weather = get_weather_decision_api(city="San Jose")

# Soil only (matches heli_tx CSV line)
soil = analyze_soil_api("12.1,0.4,0.0,28.0,22.5,41.0")

# Combined final decision
final = get_final_decision_api(
    city="San Jose",
    sensor="12.1,0.4,0.0,28.0,22.5,41.0",
)
print(final["sprinkler_on"], final["duration_minutes"])
```

Sensor dict (camelCase aliases supported):

```python
final = get_final_decision_api(
    lat=37.34,
    lon=-121.89,
    sensor={
        "voltage": 12.1,
        "current": 0.4,
        "flowRate": 0.0,
        "waterLevel": 28.0,
        "soilTemp": 22.5,
        "humidity": 41.0,
    },
)
```

---

## Option B — CLI

```bash
# Merged decision
python3 scripts/analyze_soil.py --csv "12.1,0.4,0.0,28,22.5,41" --city "San Jose"

# Soil only
python3 scripts/analyze_soil.py --csv "12.1,0.4,0.0,28,22.5,41" --soil-only

# Weather only (unchanged)
python3 scripts/sprinkler_schedule.py --city "San Jose"
```

---

## Option C — HTTP API

```bash
pip install -r requirements.txt
python3 scripts/api_server.py --port 8765
```

Open docs: http://127.0.0.1:8765/docs

### Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Liveness |
| POST | `/v1/weather/decision` | Forecast-based schedule |
| POST | `/v1/soil/analyze` | Soil sensor analysis |
| POST | `/v1/irrigation/decision` | **Final merged decision** |

### Example: final decision

```bash
curl -s http://127.0.0.1:8765/v1/irrigation/decision \
  -H 'Content-Type: application/json' \
  -d '{
    "city": "San Jose",
    "base_minutes": 20,
    "flow_gpm": 8,
    "sensor": {
      "csv_line": "12.1,0.4,0.0,28.0,22.5,41.0"
    }
  }' | python3 -m json.tool
```

### Response (key fields)

```json
{
  "sprinkler_on": true,
  "duration_minutes": 10,
  "duration_seconds": 600,
  "duration": "10 min (600 sec)",
  "decision_source": "merged_min",
  "skip_reason": null,
  "weather": { "...": "..." },
  "soil": { "...": "..." }
}
```

---

## File layout

```text
scripts/
  fetch_weather.py          # Open-Meteo client
  sprinkler_schedule.py       # Weather CLI
  analyze_soil.py             # Soil + merge CLI
  api_server.py               # FastAPI HTTP server
  irrigation/
    config.py                 # Thresholds & defaults
    types.py                  # SoilReading, *Decision dataclasses
    weather.py                # Forecast logic
    soil.py                   # Sensor logic
    merge.py                  # Final merge
    __init__.py               # Public API functions
docs/
  irrigation_schedule_design.md
  irrigation_api.md           # This file
```

---

## Integration with ESP32 / heli pipeline

```text
STM32 sensors → CSV → analyze_soil (local or POST /v1/soil/analyze)
                              ↘
Open-Meteo ──► weather decision ──► POST /v1/irrigation/decision → valve/servo
```

Your laptop or a Raspberry Pi can run `api_server.py`; firmware sends one CSV line and receives `duration_minutes`.

---

## Why this structure?

| Approach | Pros |
|----------|------|
| **Single monolith script** | Simple, but hard to test and reuse |
| **Library + thin CLI + API** ✓ | Same logic for CLI, soil script, HTTP, future ESP32 bridge |
| **Only HTTP** | Good for remote devices, but awkward for local dev |

The library is the source of truth; CLI and FastAPI are thin wrappers.

See also: [irrigation_schedule_design.md](./irrigation_schedule_design.md)
