# Irrigation decision runbook

Exact CLI commands that produce an **irrigation decision** (sprinkler ON/OFF, duration, skip reason). Run all commands from the **repository root** (`smart_sprinkler/`).

---

## Quick pick: which command do I need?

| I have… | I want… | Command |
|---------|---------|---------|
| Nothing (just location) | Weather-only ON/OFF + minutes | `sprinkler_schedule.py --city "…"` |
| Soil sensor CSV + location | **Final** decision (recommended) | `analyze_soil.py --csv "…" --city "…"` |
| Soil CSV only | Soil rules (+ ML binary if trained) | `analyze_soil.py --csv "…" --soil-only` |
| HTTP / another app | Same as final decision | `api_server.py` then `POST /v1/irrigation/decision` |
| Debug forecast only | No decision (tables) | `fetch_weather.py` (see below) |

**Production path for your hardware:** `analyze_soil.py` with `--csv` from STM32/heli and `--city` (or `--lat` / `--lon`).

---

## 0. One-time setup

```bash
cd /path/to/smart_sprinkler

# API + irrigation services (use python3 -m pip if plain pip is not found)
python3 -m pip install -r requirements.txt
```

**Python version:** 3.10+ recommended. On **3.9**, `requirements.txt` includes `eval_type_backport` for Pydantic. If the API still fails, upgrade Python (`brew install python@3.12`).

```bash
# Optional ML (binary + regression)
python3 -m pip install -r ml/requirements.txt
python3 scripts/train_ml_models.py
```

Without ML training, rule-based decisions still work; output will note missing model files.

**Location** (pick one per run):

- `--city "San Jose, CA"`
- `--lat 37.34 --lon -121.89`
- `--auto-location` (IP guess; `fetch_weather` / `sprinkler_schedule` only)
- Environment: `WEATHER_CITY`, or `WEATHER_LAT` + `WEATHER_LON`

**Sensor CSV format** (6 values, no spaces inside numbers):

```text
voltage,current,flowRate,waterLevel,soilTemp,humidity
```

Example:

```text
12.1,0.4,0.0,28,22.5,41
```

---

## 1. Final irrigation decision (weather + soil + merge + ML)

**Triggers:** rules + rain skip + soil moisture + `min(weather, soil)` minutes + optional ML.

| Field | Meaning |
|-------|---------|
| `sprinkler_on` | Run valve now? |
| `duration_minutes` | How long to run today |
| `skip_reason` | Why OFF (if any) |
| `decision_source` | e.g. `merged_min_ml`, `weather_rain_skip` |
| `days_to_next_watering` | ML regression (if trained + `--city`) |

### Command (default: ML on)

```bash
python3 scripts/analyze_soil.py \
  --csv "12.1,0.4,0.0,28,22.5,41" \
  --city "San Jose, CA"
```

### Same, machine-readable JSON

```bash
python3 scripts/analyze_soil.py \
  --csv "12.1,0.4,0.0,28,22.5,41" \
  --city "San Jose, CA" \
  --json
```

### Coordinates instead of city

```bash
python3 scripts/analyze_soil.py \
  --csv "12.1,0.4,0.0,28,22.5,41" \
  --lat 37.3382 --lon -121.8863
```

### Rules only (no ML)

```bash
python3 scripts/analyze_soil.py \
  --csv "12.1,0.4,0.0,28,22.5,41" \
  --city "San Jose, CA" \
  --no-ml
```

### Tune base run length and flow

```bash
python3 scripts/analyze_soil.py \
  --csv "12.1,0.4,0.0,28,22.5,41" \
  --city "San Jose" \
  --base-minutes 25 \
  --flow-gpm 8
```

**Requires:** `--city` or `--lat`/`--lon` (or env vars) for weather fetch. **Requires:** `--csv` for soil.

---

## 2. Weather-only irrigation decision (no soil sensor)

**Triggers:** Open-Meteo humidity/rain rules only — no CSV, no soil merge, no soil ML.

```bash
python3 scripts/sprinkler_schedule.py --city "San Jose, CA"
```

```bash
python3 scripts/sprinkler_schedule.py --city "San Jose, CA" --json
```

```bash
python3 scripts/sprinkler_schedule.py --lat 37.34 --lon -121.89
```

```bash
python3 scripts/sprinkler_schedule.py --auto-location
```

**Output keys:** `sprinkler_on`, `duration_minutes`, `duration`, `decision` (full weather object), `rain_checks`.

**ML:** not used.

---

## 3. Soil-only decision (no weather merge)

**Triggers:** soil moisture rules on CSV; optional ML binary (+ regression if `--city` and models trained). Does **not** apply rain hard-skip or `min(weather, soil)`.

```bash
python3 scripts/analyze_soil.py \
  --csv "12.1,0.4,0.0,28,22.5,41" \
  --soil-only
```

With weather fetch for ML regression (ET₀, VPD, rain):

```bash
python3 scripts/analyze_soil.py \
  --csv "12.1,0.4,0.0,28,22.5,41" \
  --soil-only \
  --city "San Jose, CA"
```

Rules only:

```bash
python3 scripts/analyze_soil.py \
  --csv "12.1,0.4,0.0,28,22.5,41" \
  --soil-only \
  --no-ml
```

**Note:** This is **not** the full production decision if rain is forecast — use section 1 for that.

---

## 4. Weather forecast + embedded schedule (rules, no soil)

**Triggers:** hourly forecast table; with `--with-schedule`, adds rule-based `sprinkler_on` / duration (same family as `sprinkler_schedule.py`, lighter output).

Forecast only (no irrigation decision):

```bash
python3 scripts/fetch_weather.py --city "San Jose, CA"
```

Forecast + rule-based ON/OFF and duration:

```bash
python3 scripts/fetch_weather.py --city "San Jose, CA" --with-schedule
```

JSON:

```bash
python3 scripts/fetch_weather.py --city "San Jose, CA" --with-schedule --json
```

**ML:** not used.

---

## 5. HTTP API (same decisions as CLI)

Start server:

```bash
pip install -r requirements.txt
python3 scripts/api_server.py
# default http://127.0.0.1:8765
```

Interactive docs: http://127.0.0.1:8765/docs

### Final decision (equivalent to section 1)

```bash
curl -s -X POST http://127.0.0.1:8765/v1/irrigation/decision \
  -H "Content-Type: application/json" \
  -d '{
    "city": "San Jose, CA",
    "base_minutes": 20,
    "flow_gpm": 8,
    "use_ml": true,
    "sensor": {
      "csv_line": "12.1,0.4,0.0,28,22.5,41"
    }
  }' | python3 -m json.tool
```

### Weather-only decision (equivalent to section 2)

```bash
curl -s -X POST http://127.0.0.1:8765/v1/weather/decision \
  -H "Content-Type: application/json" \
  -d '{"city": "San Jose, CA", "base_minutes": 20}' | python3 -m json.tool
```

### Soil analyze (equivalent to section 3)

```bash
curl -s -X POST http://127.0.0.1:8765/v1/soil/analyze \
  -H "Content-Type: application/json" \
  -d '{
    "city": "San Jose, CA",
    "use_ml": true,
    "sensor": {"csv_line": "12.1,0.4,0.0,28,22.5,41"}
  }' | python3 -m json.tool
```

Set `"use_ml": false` for rules-only.

---

## 6. Commands that do **not** decide irrigation

| Command | Purpose |
|---------|---------|
| `python3 scripts/train_ml_models.py` | Train weights only; no sprinkler output |
| `python3 scripts/fetch_weather.py` (without `--with-schedule`) | Forecast tables only |
| `ml/soil/binary/infer.py` | Standalone ML test; not merged with rain rules |
| `ml/soil/regression/infer.py` | Standalone days prediction; not merged |

---

## Decision matrix (ML × path)

| Command | Weather rules | Soil rules | Merge (rain min) | ML |
|---------|---------------|------------|------------------|-----|
| `analyze_soil.py` + `--city` | ✓ | ✓ | ✓ | ✓ default |
| `analyze_soil.py` + `--no-ml` | ✓ | ✓ | ✓ | ✗ |
| `analyze_soil.py` + `--soil-only` | ✗ | ✓ | ✗ | ✓ if `--city` |
| `sprinkler_schedule.py` | ✓ | ✗ | ✗ | ✗ |
| `fetch_weather.py --with-schedule` | ✓ | ✗ | ✗ | ✗ |
| `POST /v1/irrigation/decision` | ✓ | ✓ | ✓ | ✓ default |

---

## Typical workflows

### A. Laptop receives heli_tx CSV over Bluetooth

```bash
# Paste latest line into --csv
python3 scripts/analyze_soil.py \
  --csv "12.1,0.4,0.0,28,22.5,41" \
  --city "San Jose, CA" \
  --json
```

Use `sprinkler_on` and `duration_minutes` to command pump/servo (future firmware bridge).

### B. No sensor yet — test weather policy

```bash
python3 scripts/sprinkler_schedule.py --city "San Jose, CA"
```

### C. Cron daily check

```bash
cd /path/to/smart_sprinkler && \
python3 scripts/analyze_soil.py --csv "$SENSOR_LINE" --city "San Jose" --json \
  >> /var/log/sprinkler_decisions.jsonl
```

### D. Rules-only fallback (no torch / no artifacts)

```bash
python3 scripts/analyze_soil.py --csv "12.1,0.4,0.0,28,22.5,41" --city "San Jose" --no-ml
```

---

## Troubleshooting

| Error | Fix |
|-------|-----|
| `Provide city, lat/lon, …` | Add `--city` or `--lat`/`--lon` for any merged or weather command |
| `Expected 6 comma-separated sensor values` | Fix `--csv` format (six numbers) |
| ML notes: “no trained weights” | Run `python3 scripts/train_ml_models.py` |
| ML notes: “install torch” | `pip install -r ml/requirements.txt` |
| Network / 502 from API | Check internet; Open-Meteo must be reachable |

---

## Related docs

- [irrigation_api.md](./irrigation_api.md) — API fields and merge rules  
- [irrigation_schedule_design.md](./irrigation_schedule_design.md) — humidity bands, GPM assumptions  
- [ml_overview.md](./ml_overview.md) — what binary/regression models do  
- [architecture.md](./architecture.md) — full system map  
